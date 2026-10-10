"""Site-scoped cloud terminals. Cloud credentials and bounded output stay in memory."""
import asyncio
import base64
from collections import deque
from functools import partial
import hashlib
import json
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit, quote
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus
from .auth import AuthRequired, connect_browser, load_session
from .acp_actions import ActionError
from .task_logs import task_identity
from .cci_terminal import VerifiedCCITargets
from .runtime_profile import get_runtime_profile

MAX_SESSIONS=get_runtime_profile()['terminal_max_sessions']
MAX_BUFFER=1024*1024
OUTPUT_CHUNK_BYTES=64*1024
IDLE_SECONDS=15*60
READ_TIMEOUT=20
PREPARATION_POLL_SECONDS=10
HEARTBEAT_INTERVAL_SECONDS=15
WRITE_TIMEOUT_SECONDS=10


def dimensions(cols,rows):
    if type(cols) is not int or type(rows) is not int or not 2<=cols<=500 or not 1<=rows<=200:
        raise ActionError('终端尺寸无效。')
    return {'Width':cols,'Height':rows}


class CloudConnection(connect):
    def process_redirect(self,exc):
        # Credentials are in the official handshake query; never follow a redirect.
        return exc


class Session:
    def __init__(self,socket,owner):
        self.socket,self.owner=socket,owner
        self.tmux_token=secrets.token_hex(16)
        self.tmux_control=None;self.tmux_lock=asyncio.Lock()
        self.created=self.touched=time.monotonic()
        self.frames=deque();self.size=0;self.cursor=0
        self.sequence=0;self.last_digest=None
        self.ended=False;self.error=None;self.transport=None;self.retryable=False
        self.event=asyncio.Event();self.write_lock=asyncio.Lock()
        self.pump=asyncio.create_task(self.receive())
        self.heartbeat=asyncio.create_task(self.keepalive())

    async def receive(self):
        try:
            async for frame in self.socket:
                if not isinstance(frame,bytes) or not frame:continue
                if frame[0] in (1,2):
                    data=frame[1:]
                    if not data:continue
                    # Keep reading so upstream pings aren't blocked by a background tab.
                    # Retain a bounded tail; read() explicitly reports any skipped output.
                    if len(data)>MAX_BUFFER:
                        self.error='输出积压过多，已断开终端；请重新连接。';break
                    while self.frames and self.size+len(data)>MAX_BUFFER:
                        _,old=self.frames.popleft();self.size-=len(old)
                    if self.ended:break
                    self.cursor+=1;self.size+=len(data);self.frames.append((self.cursor,data))
                    self.event.set()
                elif frame[0]==3:
                    # Channel 3 also carries upstream errors, not just shell exits.
                    # Do not expose its raw text: providers may include credentials.
                    try:status=json.loads(frame[1:])
                    except ValueError:status=None
                    exited=isinstance(status,dict) and (status.get('status')=='Success' or status.get('reason')=='NonZeroExitCode')
                    self.retryable=not exited
                    self.error='云端终端会话已结束。' if exited else '云端终端连接中断，正在重新连接。'
                    break
            else:
                if not self.ended:
                    self.error='云端连接已关闭，正在重新连接。';self.retryable=True
        except asyncio.CancelledError:raise
        except Exception:
            self.error='云端终端连接中断，请重新连接。';self.retryable=True
        finally:
            self.ended=True;self.event.set()
            await self.close_tmux_control()
            await self.socket.close()

    async def keepalive(self):
        try:
            while not self.ended:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
                if self.transport is None and time.monotonic()-self.touched>IDLE_SECONDS:
                    self.error='终端会话已过期，请重新连接。'
                    self.retryable=True
                    break
                # The upstream WebSocket already sends protocol pings every 20 s.
                # An extra stdin heartbeat must not hold the command lock or kill the shell.
        except asyncio.CancelledError:raise
        except Exception:self.error='云端终端连接中断，请重新连接。'
        finally:
            if not self.ended:
                self.ended=True;self.event.set();self.pump.cancel()
            await self.socket.close()

    async def close(self):
        self.ended=True;self.event.set()
        for job in (self.pump,self.heartbeat):job.cancel()
        await asyncio.gather(self.pump,self.heartbeat,return_exceptions=True)
        await self.close_tmux_control()
        await self.socket.close()

    async def close_tmux_control(self):
        control=self.tmux_control
        if control:
            await control[1].close()
            if self.tmux_control is control:self.tmux_control=None


class TerminalBridge:
    def __init__(self,site,cci,acp,*,renew=None,session_loader=None,dial=None):
        self.site=site;self.resolvers={'CCI':cci,'ACP':acp}
        self.renew=renew or partial(connect_browser,site=site)
        self.session_loader=session_loader or partial(load_session,site)
        self.dial=dial or CloudConnection
        self.sessions={};self.requests={};self.open_lock=asyncio.Lock();self.renew_lock=asyncio.Lock()
        self.renewed_at=float('-inf')

    async def begin(self,action,data,owner):
        # The existing Android tunnel limits a single request to 95 s. Login is a
        # background preparation; short status reads work without an APK update.
        now=time.monotonic()
        for key,r in list(self.requests.items()):
            if now-r['created']>180:
                r['task'].cancel()
                await asyncio.gather(r['task'],return_exceptions=True)
                del self.requests[key]
        if len(self.requests)>=32 or sum(not r['task'].done() for r in self.requests.values())>=MAX_SESSIONS:
            raise ActionError('终端正在准备其他连接，请稍后重试。','terminal_limit',429)
        async def prepare():
            try:
                result=await asyncio.wait_for(self.targets(data['task_id']) if action=='targets' else self.open(data,owner),150)
                return {'result':result}
            except asyncio.CancelledError:raise
            except ActionError as exc:return {'error':str(exc),'code':exc.code}
            except AuthRequired:return {'error':'自动登录未完成，请检查本机登录配置或稍后重试。','code':'auth_required'}
            except Exception:return {'error':'终端准备未完成，请稍后重试。','code':'terminal_unavailable'}
        key=secrets.token_urlsafe(32)
        self.requests[key]={'owner':owner,'created':now,'task':asyncio.create_task(prepare())}
        return {'request_id':key}

    async def request_status(self,key,owner):
        r=self.requests.get(key) if isinstance(key,str) else None
        if not r or r['owner']!=owner or time.monotonic()-r['created']>180:
            raise ActionError('连接准备已过期，请重新读取。','terminal_expired',404)
        await asyncio.wait([r['task']],timeout=PREPARATION_POLL_SECONDS)
        if not r['task'].done():return {'pending':True}
        return {'pending':False,**r['task'].result()}

    async def renew_once(self,started):
        async with self.renew_lock:
            if self.renewed_at>=started:return
            if time.monotonic()-self.renewed_at<30:
                raise AuthRequired('自动续登后仍无法访问终端，请稍后重试。')
            await self.renew(headless=True,timeout=75)
            self.renewed_at=time.monotonic()

    async def targets(self,task_id):
        kind,_=task_identity(task_id);started=time.monotonic()
        try:return await self.resolvers[kind].targets(task_id)
        except AuthRequired:
            await self.renew_once(started)
            return await self.resolvers[kind].targets(task_id)

    async def open(self,data,owner,*,verified_targets=None):
        kind,name=task_identity(data['task_id'])
        size=dimensions(data['cols'],data['rows'])
        instance=data['instance'];container=data['container']
        if not isinstance(instance,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,252}',instance):
            raise ActionError('终端实例无效。')
        if not isinstance(container,str) or (kind=='ACP' and container) or (kind=='CCI' and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,62}',container)):
            raise ActionError('终端容器无效。')
        async with self.open_lock:
            now=time.monotonic()
            for key,s in list(self.sessions.items()):
                if s.ended or (s.transport is None and now-s.touched>IDLE_SECONDS):
                    await s.close();self.sessions.pop(key,None)
            if len(self.sessions)>=MAX_SESSIONS:raise ActionError('已打开太多终端，请先断开不使用的会话。','terminal_limit',429)
            started=time.monotonic()
            for attempt in range(2):
                try:
                    # IDE stages may share a verification completed in the last ten
                    # seconds. Normal terminals and post-login retries always refresh.
                    verified = (verified_targets.get(self.site, data['task_id'])
                                if kind == 'CCI' and owner == 'cci-editor-bootstrap' and attempt == 0
                                and isinstance(verified_targets, VerifiedCCITargets) else None)
                    targets=(verified or await self.resolvers[kind].targets(data['task_id'],reuse=True))['targets']
                    if not any(t['instance']==instance and (kind=='ACP' or t.get('container')==container) for t in targets):
                        raise ActionError('所选 Worker / 容器已不可用，请重新读取。','terminal_unavailable',409)
                    token=self.session_loader()['headers']['authorization']
                    if token.lower().startswith('bearer '):token=token[7:]
                    if kind=='CCI':
                        path='/compute/cci/data/v2'+self.site.workspace+'/apps/'+name+'/containerInstances/'+quote(instance,safe='')+'/containerName/'+quote(container,safe='')+'/webterminal'
                    else:
                        path='/compute/workspace/data/ws/v1'+self.site.workspace+'/trainingJobs/'+name+'/pod/'+quote(instance,safe='')+'/exec'
                    origin=urlsplit(self.site.console)
                    url='wss://'+urlsplit(self.site.services['AEC2']).netloc+path+'?'+urlencode({'token':token,'lines':size['Height'],'columns':size['Width']})
                    # Keep protocol heartbeats without a short pong deadline during network stalls.
                    socket=await self.dial(url,subprotocols=['channel.k8s.io'],origin=origin.scheme+'://'+origin.netloc,
                        proxy=None,open_timeout=20,close_timeout=3,max_size=MAX_BUFFER,compression=None,ping_interval=20,ping_timeout=None)
                    # The cloud ignores handshake dimensions: stty reports 0 0 until
                    # channel 4 is sent. Set the PTY before accepting any input.
                    try:
                        await asyncio.wait_for(socket.send(b'\4'+json.dumps(size,separators=(',',':')).encode()),10)
                    except BaseException:
                        await socket.close()
                        raise
                    break
                except (AuthRequired,InvalidStatus) as exc:
                    if isinstance(exc,InvalidStatus) and exc.response.status_code not in (401,403):
                        raise ActionError('云端拒绝建立终端连接，请稍后重试。','terminal_unavailable',502) from None
                    if attempt:raise AuthRequired('自动续登后仍无法访问终端。') from None
                    await self.renew_once(started)
                except ActionError:raise
                except Exception:raise ActionError('终端连接暂不可用，请稍后重试。','terminal_unavailable',502) from None
            key=secrets.token_urlsafe(32)
            self.sessions[key]=Session(socket,owner)
            self.sessions[key].target={field:data[field] for field in ('task_id','instance','container')}
            return {'session_id':key,'site':self.site.id,'task_id':data['task_id'],'tmux_token':self.sessions[key].tmux_token}

    def get(self,key,owner):
        s=self.sessions.get(key) if isinstance(key,str) else None
        if s is None or s.owner!=owner:
            raise ActionError('终端会话已失效，请重新连接。','terminal_expired',404)
        if s.transport is None and time.monotonic()-s.touched>IDLE_SECONDS:
            raise ActionError('终端会话已过期，请重新连接。','terminal_expired',410)
        s.touched=time.monotonic()
        return s

    def attach(self,key,cursor,owner):
        s=self.get(key,owner)
        if type(cursor) is not int or not 0<=cursor<=s.cursor:
            raise ActionError('终端读取位置无效。')
        # A replacement transport owns the lease; late cleanup from the old one
        # must not expire or close the same cloud shell.
        s.transport=object()
        return s.transport

    def detach(self,key,lease,owner):
        s=self.sessions.get(key)
        if s and s.owner==owner and s.transport is lease:
            s.transport=None;s.touched=time.monotonic()

    async def read(self,key,cursor,owner,*,wait=True):
        s=self.get(key,owner)
        if type(cursor) is not int or cursor<0 or cursor>s.cursor or type(wait) is not bool:
            raise ActionError('终端读取位置无效。')
        # Client acknowledgement releases memory. Repeated reads at the same cursor are safe.
        while s.frames and s.frames[0][0]<=cursor:
            _,data=s.frames.popleft();s.size-=len(data)
        if wait and not s.frames and not s.ended:
            s.event.clear()
            try:await asyncio.wait_for(s.event.wait(),READ_TIMEOUT)
            except asyncio.TimeoutError:pass
        frames=[(seq,data) for seq,data in s.frames if seq>cursor]
        # Only response encoding is batched; retained frames and ACK cursors stay intact.
        output=b''.join(data for _,data in frames)
        return {'type':'output','cursor':frames[-1][0] if frames else cursor,
                'chunks':[base64.b64encode(output[start:start+OUTPUT_CHUNK_BYTES]).decode('ascii')
                          for start in range(0,len(output),OUTPUT_CHUNK_BYTES)],
                'closed':s.ended,'error':s.error,'sequence':s.sequence,
                **({'truncated':True} if frames and frames[0][0]>cursor+1 else {}),
                **({'retryable':True} if s.ended and s.retryable else {})}

    async def write(self,key,sequence,data,size,owner):
        s=self.get(key,owner)
        if type(sequence) is not int or sequence<1 or not isinstance(data,str) or len(data.encode('utf-8'))>16384:
            raise ActionError('终端输入无效或过长。')
        if size is not None:
            if not isinstance(size,dict) or set(size)!={'cols','rows'} or data:raise ActionError('终端尺寸请求无效。')
            payload=b'\4'+json.dumps(dimensions(size['cols'],size['rows']),separators=(',',':')).encode()
        else:payload=b'\0'+data.encode()
        digest=hashlib.sha256(payload).digest()
        async def send():
            async with s.write_lock:
                if s.ended:raise ActionError('终端已断开；不会重发输入，请重新连接。','terminal_expired',409)
                if sequence==s.sequence and digest==s.last_digest:return {'type':'ack','sequence':sequence}
                if sequence!=s.sequence+1:raise ActionError('输入顺序不一致，已拒绝重复执行。','terminal_sequence',409)
                s.sequence=sequence;s.last_digest=digest
                await s.socket.send(payload)
                return {'type':'ack','sequence':sequence}
        try:
            # Include time queued behind heartbeats/other writers in the deadline.
            return await asyncio.wait_for(send(),WRITE_TIMEOUT_SECONDS)
        except ActionError:raise
        except Exception:
            s.error='输入结果无法确认，已断开终端；不会自动重发命令。'
            s.retryable=True
            await s.close()
            raise ActionError(s.error,'terminal_disconnected',502) from None

    async def disconnect(self,key,owner):
        s=self.get(key,owner);await s.close();self.sessions.pop(key,None)
        return {'closed':True}

    async def close(self):
        tasks=[r['task'] for r in self.requests.values()]
        for job in tasks:job.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        self.requests.clear()
        await asyncio.gather(*(s.close() for s in self.sessions.values()))
        self.sessions.clear()
