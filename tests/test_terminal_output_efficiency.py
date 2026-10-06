"""Batch output bytes without changing frame cursors or acknowledgement lifetime."""
import asyncio
import base64
import json
from contextlib import asynccontextmanager

import pytest

from server.acp_actions import ActionError
from server.sites import MAOSHANWANG
from server.terminal_bridge import Session, TerminalBridge
from tests.test_terminal_bridge import FakeSocket


@asynccontextmanager
async def buffered_terminal(frames):
    socket=FakeSocket()
    for index,frame in enumerate(frames):
        socket.messages.put_nowait(bytes([1+index%2])+frame)
    session=Session(socket,'fixture-owner')
    bridge=TerminalBridge(MAOSHANWANG,None,None)
    bridge.sessions['fixture-session']=session
    try:
        if frames:await asyncio.wait_for(session.event.wait(),.5)
        assert session.cursor==len(frames)
        yield bridge,session,socket
    finally:await bridge.close()


def decoded(output):
    return b''.join(base64.b64decode(chunk,validate=True) for chunk in output['chunks'])


@pytest.mark.asyncio
async def test_4096_small_frames_share_one_encoding_and_json_chunk(monkeypatch):
    frames=[b'0123456789abcdef']*4096
    encoded_sizes=[];encode=base64.b64encode
    def counted(data):
        encoded_sizes.append(len(data))
        return encode(data)
    monkeypatch.setattr('server.terminal_bridge.base64.b64encode',counted)
    async with buffered_terminal(frames) as (bridge,session,_):
        output=await bridge.read('fixture-session',0,'fixture-owner',wait=False)
        metrics={'frames':len(frames),'raw_bytes':session.size,'chunks':len(output['chunks']),
                 'encoding_calls':len(encoded_sizes),'compact_json_bytes':len(json.dumps(output,separators=(',',':')).encode())}
        print(json.dumps(metrics,sort_keys=True))
        assert decoded(output)==b''.join(frames) and output['cursor']==4096
        assert len(session.frames)==4096 and session.size==65536
        assert metrics['chunks']==1 and encoded_sizes==[65536]
        assert metrics['compact_json_bytes']<88000


@pytest.mark.asyncio
@pytest.mark.parametrize('frames',[
    [b'x'*(65536*2+3)],
    [b'a'*65535,b'\xe4',b'\xb8\xad\x1b',b'[31m',b'\0\xff',b'b'*65540],
    [b'a'*32768,b'b'*65536,b'c'*32768],
])
async def test_output_chunks_are_bounded_and_preserve_all_bytes(frames):
    async with buffered_terminal(frames) as (bridge,session,_):
        output=await bridge.read('fixture-session',0,'fixture-owner',wait=False)
        chunks=[base64.b64decode(chunk,validate=True) for chunk in output['chunks']]
        assert b''.join(chunks)==b''.join(frames)
        assert all(0<len(chunk)<=65536 for chunk in chunks)
        assert len(chunks)==(sum(map(len,frames))+65535)//65536
        assert output['cursor']==len(frames) and session.cursor==len(frames)


@pytest.mark.asyncio
async def test_repeated_reads_replay_until_original_frame_cursor_is_acknowledged():
    frames=[b'first',b'\xe4',b'\xb8\xad\x1b[0m',b'last']
    async with buffered_terminal(frames) as (bridge,session,_):
        first=await bridge.read('fixture-session',0,'fixture-owner',wait=False)
        assert await bridge.read('fixture-session',0,'fixture-owner',wait=False)==first
        assert list(session.frames)==list(enumerate(frames,1))
        assert session.size==sum(map(len,frames))
        remaining=await bridge.read('fixture-session',2,'fixture-owner',wait=False)
        assert remaining['cursor']==4 and decoded(remaining)==b''.join(frames[2:])
        assert list(session.frames)==[(3,frames[2]),(4,frames[3])]
        assert session.size==sum(map(len,frames[2:]))
        assert await bridge.read('fixture-session',2,'fixture-owner',wait=False)==remaining
        empty=await bridge.read('fixture-session',remaining['cursor'],'fixture-owner',wait=False)
        assert empty['cursor']==4 and empty['chunks']==[] and not empty['closed']
        assert not session.frames and session.size==0


@pytest.mark.asyncio
async def test_read_does_not_release_backpressure_before_ack(monkeypatch):
    monkeypatch.setattr('server.terminal_bridge.MAX_BUFFER',48)
    frames=[b'x'*16]*3
    async with buffered_terminal(frames) as (bridge,session,socket):
        first=await bridge.read('fixture-session',0,'fixture-owner',wait=False)
        assert first['cursor']==3 and session.size==48 and not session.ended
        session.event.clear()
        socket.messages.put_nowait(b'\1overflow')
        await asyncio.wait_for(session.event.wait(),.5)
        output=await bridge.read('fixture-session',0,'fixture-owner',wait=False)
        assert output['closed'] and '积压' in output['error']
        assert decoded(output)==b''.join(frames) and output['cursor']==3
        assert session.size==48 and len(session.frames)==3


@pytest.mark.asyncio
async def test_closed_error_input_sequence_and_owner_checks_survive_batching():
    async with buffered_terminal([b'one',b'two']) as (bridge,session,socket):
        await bridge.write('fixture-session',1,'input',None,'fixture-owner')
        await bridge.write('fixture-session',1,'input',None,'fixture-owner')
        assert socket.sent==[b'\0input']
        for cursor,owner in [(2,'other-owner'),(3,'fixture-owner'),(True,'fixture-owner')]:
            with pytest.raises(ActionError):await bridge.read('fixture-session',cursor,owner,wait=False)
        assert session.size==6 and len(session.frames)==2
        session.event.clear()
        socket.messages.put_nowait(b'\3PRIVATE upstream error')
        await asyncio.wait_for(session.event.wait(),.5)
        output=await bridge.read('fixture-session',0,'fixture-owner',wait=False)
        assert decoded(output)==b'onetwo' and output['cursor']==2 and output['sequence']==1
        assert output['closed'] and output['error'] and 'PRIVATE' not in str(output)


@pytest.mark.asyncio
async def test_acknowledged_empty_buffer_still_waits_for_new_frame():
    async with buffered_terminal([b'first']) as (bridge,session,socket):
        first=await bridge.read('fixture-session',0,'fixture-owner',wait=False)
        pending=asyncio.create_task(bridge.read('fixture-session',first['cursor'],'fixture-owner'))
        try:
            await asyncio.sleep(0)
            assert not pending.done() and session.size==0 and not session.frames
            socket.messages.put_nowait(b'\1next')
            output=await asyncio.wait_for(pending,.5)
            assert decoded(output)==b'next' and output['cursor']==2
        finally:
            pending.cancel()
            await asyncio.gather(pending,return_exceptions=True)
