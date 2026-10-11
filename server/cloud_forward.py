"""Bounded TCP multiplexing; this standard-library-only module also runs in CCI."""
import asyncio
import base64
import json
import os
import selectors
import socket
import sys
import termios
import tty
from pathlib import Path
import secrets
import shlex
import zlib

CHUNK = 8192
WINDOW = 65536
TOTAL_WINDOW = 262144
MAX_STREAMS = 32
MAX_FRAME = 12000


class Stream:
    def __init__(self, key, reader=None, writer=None):
        self.key, self.reader, self.writer = key, reader, writer
        # Bytes, not TCP packet count, bound this queue (packets may be one byte).
        self.queue = asyncio.Queue(maxsize=WINDOW + 1)
        self.opened = asyncio.Event()
        self.sent = self.received = 0
        self.eof = False
        self.task = None


class TunnelPeer:
    def __init__(self, read, write, destination=None):
        self.read, self.write, self.destination = read, write, destination
        self.streams = {}
        self.jobs = set()
        self.condition = asyncio.Condition()
        self.write_lock = asyncio.Lock()
        self.ready = asyncio.get_running_loop().create_future()
        self.running = True
        self.next_id = self.sent = self.received = 0

    async def send(self, frame):
        async with self.write_lock:
            if not self.running: raise ConnectionError('tunnel closed')
            await self.write(frame)

    async def accept(self, reader, writer):
        if not self.running or len(self.streams) >= MAX_STREAMS:
            writer.close()
            return
        self.next_id += 1
        stream = Stream(self.next_id, reader, writer)
        self.streams[stream.key] = stream
        stream.task = asyncio.current_task()
        self.jobs.add(stream.task)
        try:
            await self.send(['o', stream.key])
            await asyncio.wait_for(stream.opened.wait(), 10)
            await self.transfer(stream)
        except (OSError, asyncio.TimeoutError):
            pass
        finally:
            await self.finish(stream)
            self.jobs.discard(stream.task)

    async def connect(self, stream):
        try:
            stream.reader, stream.writer = await asyncio.wait_for(
                asyncio.open_connection(*self.destination), 8)
            await self.send(['o', stream.key])
            await self.transfer(stream)
        except (OSError, asyncio.TimeoutError):
            pass
        finally:
            await self.finish(stream)

    async def transfer(self, stream):
        async def upload():
            while True:
                data = await stream.reader.read(CHUNK)
                if not data:
                    await self.send(['e', stream.key])
                    return
                async with self.condition:
                    await self.condition.wait_for(lambda: not self.running or
                        (stream.sent + len(data) <= WINDOW and self.sent + len(data) <= TOTAL_WINDOW))
                    if not self.running: raise ConnectionError('tunnel closed')
                    stream.sent += len(data); self.sent += len(data)
                await self.send(['d', stream.key, base64.b64encode(data).decode('ascii')])

        async def download():
            while True:
                data = await stream.queue.get()
                if data is None:
                    if stream.writer.can_write_eof(): stream.writer.write_eof()
                    return
                stream.writer.write(data)
                await stream.writer.drain()
                stream.received -= len(data); self.received -= len(data)
                await self.send(['a', stream.key, len(data)])
        jobs = [asyncio.create_task(upload()), asyncio.create_task(download())]
        try:
            await asyncio.gather(*jobs)
            # EOF only means queued data was framed. Wait until the destination
            # has drained it before a close is allowed to discard stream state.
            async with self.condition:
                await self.condition.wait_for(lambda: not self.running or stream.sent == 0)
        finally:
            for job in jobs: job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)

    async def finish(self, stream, notify=True):
        if self.streams.pop(stream.key, None) is not stream: return
        async with self.condition:
            self.sent -= stream.sent; self.received -= stream.received
            self.condition.notify_all()
        if stream.task and stream.task is not asyncio.current_task(): stream.task.cancel()
        if stream.writer:
            stream.writer.close()
            try: await asyncio.wait_for(stream.writer.wait_closed(), 1)
            except (OSError, asyncio.TimeoutError): pass
        if notify and self.running:
            try: await self.send(['c', stream.key])
            except OSError: pass

    async def dispatch(self, frame):
        if frame == ['r'] and self.destination is None and not self.ready.done():
            self.ready.set_result(None)
            return
        if (not isinstance(frame, list) or len(frame) not in (2, 3)
                or type(frame[1]) is not int or not 0 < frame[1] < 2 ** 31):
            raise ValueError('invalid tunnel frame')
        op, key = frame[:2]
        if op not in ('o', 'd', 'a', 'e', 'c') or len(frame) != (3 if op in ('d', 'a') else 2):
            raise ValueError('invalid tunnel operation')
        stream = self.streams.get(key)
        if op == 'o':
            if self.destination is None:
                if stream is None or stream.opened.is_set(): raise ValueError('unexpected connection')
                stream.opened.set()
            else:
                if stream is not None or len(self.streams) >= MAX_STREAMS:
                    await self.send(['c', key]); return
                stream = self.streams[key] = Stream(key)
                stream.task = asyncio.create_task(self.connect(stream))
                self.jobs.add(stream.task)
                stream.task.add_done_callback(self.jobs.discard)
            return
        if stream is None: return  # Late acknowledgements for a closed stream.
        if op == 'c':
            await self.finish(stream, notify=False)
        elif op == 'e':
            if stream.eof: raise ValueError('duplicate EOF')
            stream.eof = True; stream.queue.put_nowait(None)
        elif op == 'a':
            count = frame[2]
            if type(count) is not int or not 0 < count <= stream.sent: raise ValueError('invalid credit')
            async with self.condition:
                stream.sent -= count; self.sent -= count
                self.condition.notify_all()
        else:
            if stream.eof or not isinstance(frame[2], str) or len(frame[2]) > 4 * ((CHUNK + 2) // 3):
                raise ValueError('invalid data frame')
            data = base64.b64decode(frame[2], validate=True)
            if not 0 < len(data) <= CHUNK: raise ValueError('invalid data size')
            if stream.received + len(data) > WINDOW or self.received + len(data) > TOTAL_WINDOW:
                raise ValueError('receive window exceeded')
            stream.received += len(data); self.received += len(data)
            stream.queue.put_nowait(data)

    async def run(self):
        try:
            while self.running:
                frame = await self.read()
                if frame is None: break
                await self.dispatch(frame)
        finally:
            await self.shutdown()

    async def shutdown(self):
        self.running = False
        if not self.ready.done(): self.ready.set_exception(ConnectionError('tunnel did not become ready'))
        async with self.condition: self.condition.notify_all()
        jobs = list(self.jobs)
        for job in jobs: job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        for stream in list(self.streams.values()): await self.finish(stream, notify=False)


def run_remote(instance, address, port, tag):
    # No client frame can select another destination or execute another command.
    if socket.gethostname() != instance: raise RuntimeError('container identity changed')
    descriptor = os.open('/dev/tty', os.O_RDWR)
    previous = termios.tcgetattr(descriptor)
    tty.setraw(descriptor)

    async def serve():
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(limit=MAX_FRAME)
        transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader),
            os.fdopen(os.dup(descriptor), 'rb', buffering=0))
        write_transport, protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin,
            os.fdopen(os.dup(descriptor), 'wb', buffering=0))
        writer = asyncio.StreamWriter(write_transport, protocol, None, loop)
        prefix = tag.encode()
        async def read():
            line = await reader.readline()
            if not line: return None
            if len(line) > MAX_FRAME or not line.startswith(prefix): raise ValueError('invalid frame')
            return json.loads(line[len(prefix):])
        async def write(frame):
            writer.write(prefix + json.dumps(frame, separators=(',', ':')).encode() + b'\n')
            await writer.drain()
        peer = TunnelPeer(read, write, (address, port))
        peer.ready.set_result(None)
        try:
            await peer.send(['r'])
            await peer.run()
        finally:
            transport.close(); writer.close()
    # select handles TTYs on both Linux and macOS; kqueue rejects slave PTYs.
    loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(serve())
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
        termios.tcsetattr(descriptor, termios.TCSANOW, previous)
        os.close(descriptor)


def forward_alive(forward):
    if isinstance(forward, CloudForward): return forward.alive
    process = getattr(forward, 'process', None)
    return process is not None and process.returncode is None


class CloudForward(TunnelPeer):
    owner = 'cci-editor-tunnel'

    def __init__(self, bridge, target, address, port, *, verify=None):
        self.bridge, self.target = bridge, dict(target)
        self.verify = verify
        self.address, self.remote_port = address, port
        self.port = self.session_id = self.listener = self.runner = None
        self.disconnecting = None
        self.sequence = self.cursor = 0
        self.buffer = bytearray()
        self.tag = secrets.token_hex(16).encode()

    @property
    def alive(self):
        return self.runner is not None and not self.runner.done() and self.running

    async def write(self, frame):
        data = self.tag + json.dumps(frame, separators=(',', ':')).encode() + b'\n'
        if len(data) > MAX_FRAME: raise ValueError('tunnel frame too large')
        self.sequence += 1
        await self.bridge.write(self.session_id, self.sequence, data.decode(), None, self.owner)

    async def read(self):
        while True:
            start = self.buffer.find(self.tag)
            if start >= 0:
                end = self.buffer.find(b'\n', start)
                if end >= 0:
                    frame = self.buffer[start+len(self.tag):end]
                    del self.buffer[:end+1]
                    if len(frame) > MAX_FRAME: raise ValueError('tunnel frame too large')
                    return json.loads(frame)
            if len(self.buffer) > 65536: raise ValueError('invalid tunnel output')
            result = await self.bridge.read(self.session_id, self.cursor, self.owner)
            self.cursor = result['cursor']
            if result.get('truncated'): raise ConnectionError('tunnel output lost')
            for chunk in result['chunks']:
                self.buffer.extend(base64.b64decode(chunk, validate=True))
            if result['closed']: return None

    async def start(self):
        try:
            if self.verify is not None: await self.verify()
            opened = await self.bridge.open({**self.target, 'cols': 240, 'rows': 40}, self.owner)
            self.session_id = opened['session_id']
            source = Path(__file__).read_text()
            source += '\nrun_remote(' + ','.join(repr(value) for value in
                (self.target['instance'], self.address, self.remote_port, self.tag.decode())) + ')\n'
            encoded = base64.b64encode(zlib.compress(source.encode(), 9)).decode()
            # Transfer source only after Python owns the raw TTY. Interactive
            # shell/readline handling can corrupt a large pasted heredoc.
            tag = self.tag.decode()
            loader = ('import base64,zlib,sys,tty;tty.setraw(0);'
                      'print(' + repr(tag[:16]) + '+' + repr(tag[16:]) + '+\'["b"]\',flush=True);'
                      'exec(zlib.decompress(base64.b64decode(sys.stdin.buffer.read('
                      + str(len(encoded)) + '))))')
            command = 'exec python3 -u -c ' + shlex.quote(loader) + '\n'
            self.sequence += 1
            await self.bridge.write(self.session_id, self.sequence, command, None, self.owner)
            if await asyncio.wait_for(self.read(), 20) != ['b']:
                raise ConnectionError('tunnel bootstrap did not become ready')
            for start in range(0, len(encoded), 1800):
                self.sequence += 1
                await self.bridge.write(self.session_id, self.sequence, encoded[start:start+1800], None, self.owner)
            super().__init__(self.read, self.write)
            self.runner = asyncio.create_task(self.run())
            await asyncio.wait_for(self.ready, 20)
            self.listener = await asyncio.start_server(self.accept, '127.0.0.1', 0)
            self.port = self.listener.sockets[0].getsockname()[1]
            if not self.alive: raise ConnectionError('tunnel closed during startup')
        except BaseException:
            await self.close()
            raise

    async def run(self):
        try: await super().run()
        except Exception: pass  # Error details may contain terminal data; never log them.
        finally:
            if self.listener: self.listener.close()
            await self.disconnect()

    async def disconnect(self):
        from .acp_actions import ActionError
        if self.disconnecting is not None and self.disconnecting.done():
            self.disconnecting = None
        if self.disconnecting is None and self.session_id is not None:
            key = self.session_id
            async def cleanup():
                try:
                    await self.bridge.disconnect(key, self.owner)
                except ActionError as error:
                    if error.code != 'terminal_expired' or error.status not in (404, 410): raise
                self.session_id = None
            self.disconnecting = asyncio.create_task(cleanup())
        if self.disconnecting is not None: await asyncio.shield(self.disconnecting)

    async def close(self):
        if self.listener:
            self.listener.close(); await self.listener.wait_closed()
        if self.runner:
            self.runner.cancel()
            await asyncio.gather(self.runner, return_exceptions=True)
        await self.disconnect()
