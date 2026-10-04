import asyncio
import base64
import json
import re
import pytest


@pytest.mark.asyncio
async def test_remote_progress_frames_survive_chunks_and_do_not_become_result():
    from server.cci_ide import run_python
    class Bridge:
        def __init__(self):self.commands=[];self.pieces=None;self.closed=False
        async def open(self,data,owner):return {'session_id':'owned'}
        async def write(self,key,seq,data,size,owner):self.commands.append(data)
        async def read(self,key,cursor,owner):
            if self.pieces is None:
                parts=re.findall(r"'([A-Za-z0-9+/=]+)'",''.join(self.commands))
                payload=base64.b64decode(''.join(parts)).decode()
                tag=re.search(r"chr\(30\)\+'([a-f0-9]+)'",payload).group(1)
                def frame(data):return b'\x1e'+tag.encode()+json.dumps(data).encode()+b'\x1f'
                raw=b'shell noise'+frame({'__ilab_progress__':{'stage':'download_editor','downloaded_bytes':1024,'total_bytes':2048}})+frame({'__ilab_progress__':{'stage':'extract_editor'}})+frame({'port':1234})
                self.pieces=[raw[:18],raw[18:95],raw[95:]]
            return {'chunks':[base64.b64encode(self.pieces.pop(0)).decode()],'cursor':cursor+1,'closed':False}
        async def disconnect(self,key,owner):self.closed=True
    bridge=Bridge();events=[]
    result=await run_python(bridge,{},'RESULT={"port":1234}',on_progress=events.append)
    assert result=={'port':1234} and bridge.closed
    assert [e['stage'] for e in events]==['download_editor','extract_editor']
    assert events[0]['downloaded_bytes']==1024


@pytest.mark.asyncio
async def test_status_reports_only_sanitized_current_progress_and_deduplicates():
    from server.cci_ide import CCIEditor
    from server.sites import MAOSHANWANG
    editor=CCIEditor(MAOSHANWANG,None,None);gate=asyncio.Event();sent=asyncio.Event()
    async def opened(data,origin,progress=None):
        progress({'stage':'download_editor','downloaded_bytes':1024,'total_bytes':2048,'password':'PRIVATE'})
        progress({'stage':'PRIVATE','message':'PRIVATE'})
        sent.set();await gate.wait();return {'done':True}
    editor.open=opened
    data={'task_id':'CCI:job','instance':'pod','container':'main'};origin='http://localhost:8766'
    try:
        first=await editor.begin('open',data,origin);await asyncio.wait_for(sent.wait(),1)
        assert first==await editor.begin('open',data,origin)
        result=await editor.status(first['request_id'],origin)
        assert result['progress']=={'stage':'download_editor','downloaded_bytes':1024,'total_bytes':2048}
        assert 'PRIVATE' not in json.dumps(result)
        assert '下载' in result['message']
        gate.set();await editor.requests[first['request_id']]['task']
        assert (await editor.status(first['request_id'],origin))=={'pending':False,'result':{'done':True}}
    finally:await editor.close()


def test_progress_validation_unknown_total_and_invalid_numbers():
    from server.cci_ide import safe_progress
    assert safe_progress({'stage':'download_editor','downloaded_bytes':42})=={'stage':'download_editor','downloaded_bytes':42,'total_bytes':None}
    for invalid in (None,[],{'stage':'secret'},{'stage':'download_editor','downloaded_bytes':-1},{'stage':'download_editor','downloaded_bytes':True}):
        assert safe_progress(invalid) is None
    assert safe_progress({'stage':'download_editor','downloaded_bytes':42,'total_bytes':12})['total_bytes'] is None
    assert safe_progress({'stage':'connect','total_bytes':'secret','token':'secret'})=={'stage':'connect'}


def test_bootstrap_real_download_bytes_and_reuse(tmp_path,monkeypatch):
    import hashlib,io,tarfile
    from pathlib import Path
    from types import SimpleNamespace
    from server.ide_bootstrap import bootstrap_script,VERSION
    home=tmp_path/'home';home.mkdir()
    monkeypatch.setenv('HOME',str(home))
    monkeypatch.delenv('CODEX_HOME',raising=False)
    folder=home/'.local/share/ilab-cci-editor'/('code-server-'+VERSION+'-linux-amd64')
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w:gz') as tar:
        info=tarfile.TarInfo(folder.name+'/bin/code-server');info.size=4
        tar.addfile(info,io.BytesIO(b'fake'))
    archive=buffer.getvalue();requests=[];events=[]
    class Response(io.BytesIO):
        headers={'Content-Length':str(len(archive))}
    def download(*a,**kw):requests.append(1);return Response(archive)
    class Socket:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def bind(self,*a):pass
        def getsockname(self):return ('10.0.0.1',12345)
    original=Path.read_bytes
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
    monkeypatch.setattr(Path,'read_bytes',lambda p:str(folder).encode() if str(p)=='/proc/123456/cmdline' else original(p))
    monkeypatch.setattr('platform.system',lambda:'Linux')
    monkeypatch.setattr('platform.machine',lambda:'x86_64')
    monkeypatch.setattr('socket.gethostname',lambda:'pod')
    monkeypatch.setattr('socket.socket',Socket)
    monkeypatch.setattr('socket.create_connection',lambda *a:Socket())
    monkeypatch.setattr('urllib.request.urlopen',download)
    monkeypatch.setattr('subprocess.Popen',lambda *a,**kw:SimpleNamespace(pid=123456,poll=lambda:None))
    monkeypatch.setattr('subprocess.run',lambda *a,**kw:SimpleNamespace(returncode=1))
    monkeypatch.setattr('server.ide_bootstrap.SHA256',hashlib.sha256(archive).hexdigest())
    script=bootstrap_script('pod','10.0.0.1')
    scope={'_ilab_report_progress':lambda *a:events.append(a)}
    # The emitted script runs against fake OS processes and an actual tiny tar archive.
    import os
    previous=os.umask(0o077)
    try:
        exec(compile(script,'bootstrap','exec'),scope)
        assert ('download_editor',len(archive),len(archive)) in events
        assert ('verify_editor',) in events and ('extract_editor',) in events
        assert scope['RESULT']['port']==12345
        events.clear()
        exec(compile(script,'bootstrap','exec'),scope)
        assert requests==[1] and all(not e[0].startswith('download') for e in events)
    finally:os.umask(previous)


@pytest.mark.parametrize('known',[True,False])
def test_codex_download_reports_bytes_before_verifying(tmp_path,monkeypatch,known):
    import io
    from pathlib import Path
    from server.ide_codex import install_codex
    class Response(io.BytesIO):headers={'Content-Length':'5'} if known else {}
    monkeypatch.setattr('urllib.request.urlopen',lambda *a,**kw:Response(b'12345'))
    events=[]
    with pytest.raises(RuntimeError,match='校验'):
        install_codex(tmp_path/'editor',Path('/never-executed'),{},progress=lambda *a:events.append(a))
    assert ('download_codex',5,5 if known else None) in events
    assert events[-1]==('verify_codex',)
    assert all(e[0]!='install_codex' for e in events)
