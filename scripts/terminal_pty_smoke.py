"""Exercise the real bridge, xterm and tmux with an isolated local PTY, no cloud jobs."""
import asyncio,fcntl,json,os,pty,secrets,shutil,socket,struct,subprocess,sys,tempfile,termios
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uvicorn
from playwright.async_api import async_playwright,expect
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from server.main import create_app
from server.store import Store
from server.service import Monitor
from server.terminal_bridge import TerminalBridge
from server.sites import MAOSHANWANG

class PTYSocket:
    def __init__(self):
        self.master,slave=pty.openpty();self.queue=asyncio.Queue();self.closed=False;self.size=None
        def terminal():
            os.setsid();fcntl.ioctl(slave,termios.TIOCSCTTY,0)
        self.process=subprocess.Popen(['/bin/bash','--noprofile','--norc','-i'],stdin=slave,stdout=slave,stderr=slave,
            env={**os.environ,'TERM':'xterm-256color','PS1':'ILAB$ ','HISTFILE':'/dev/null'},preexec_fn=terminal)
        os.close(slave);asyncio.get_running_loop().add_reader(self.master,self.readable)
    def readable(self):
        try:data=os.read(self.master,65536)
        except OSError:data=b''
        if data:self.queue.put_nowait(b'\1'+data)
        else:asyncio.get_running_loop().remove_reader(self.master);self.queue.put_nowait(None)
    def __aiter__(self):return self
    async def __anext__(self):
        data=await self.queue.get()
        if data is None:raise StopAsyncIteration
        return data
    async def send(self,data):
        if data[0]==4:
            size=json.loads(data[1:]);self.size={'cols':size['Width'],'rows':size['Height']}
            fcntl.ioctl(self.master,termios.TIOCSWINSZ,struct.pack('HHHH',size['Height'],size['Width'],0,0))
        elif data[0]==0 and len(data)>1:os.write(self.master,data[1:])
    async def close(self):
        if self.closed:return
        self.closed=True;asyncio.get_running_loop().remove_reader(self.master);os.close(self.master)
        self.queue.put_nowait(None)
        if self.process.poll() is None:self.process.terminate()
        await asyncio.to_thread(self.process.wait)

async def until(predicate):
    for _ in range(100):
        if await predicate():return
        await asyncio.sleep(.05)
    raise AssertionError('Terminal/PTY dimensions did not converge')

async def main():
    tmux=shutil.which('tmux')
    if not tmux:raise SystemExit('Install tmux to run this optional full-screen PTY check.')
    opening=asyncio.Event();release_open=asyncio.Event()
    shells=[];tmux_name='ilab-smoke-'+secrets.token_hex(6)
    async def dial(*args,**kwargs):
        opening.set();await release_open.wait()
        shell=PTYSocket();shells.append(shell);return shell
    target={'site':'maoshanwang','task_id':'CCI:test','name':'charles terminal regression',
        'targets':[{'instance':'pod','container':'main','url':MAOSHANWANG.console+'/cci/terminal?workspace=/workspace&app_name=test&container_instance_name=pod&container_name=main'}]}
    with tempfile.TemporaryDirectory() as tmp:
        app=create_app(Monitor(Store(Path(tmp)/'db'),None),background=False)
        app.state.runtimes['maoshanwang'].terminal_bridge=TerminalBridge(MAOSHANWANG,SimpleNamespace(targets=AsyncMock(return_value=target)),None,
            session_loader=lambda:{'headers':{'authorization':'Bearer fake'}},dial=dial)
        listener=socket.socket();listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,log_level='critical',access_log=False))
        job=asyncio.create_task(server.serve(sockets=[listener]))
        while not server.started:await asyncio.sleep(.02)
        errors=[]
        try:
            async with async_playwright() as p:
                browser=await p.chromium.launch(channel='chrome',headless=True)
                page=await browser.new_page(viewport={'width':1440,'height':1000})
                page.on('pageerror',lambda e:errors.append(str(e)))
                await page.add_init_script("""Object.defineProperty(window,'Terminal',{configurable:true,set(Base){delete window.Terminal;window.Terminal=class extends Base {constructor(...args){super(...args);window.__testTerminal=this;}};}});""")
                await page.add_init_script("""window.__terminalSockets=[];const NativeSocket=WebSocket;
                  window.WebSocket=class extends NativeSocket {constructor(...args){super(...args);window.__terminalSockets.push(this);
                    this.addEventListener('message',event=>{if(window.__dropInputAck && JSON.parse(event.data).type==='ack'){
                      window.__dropInputAck=false;event.stopImmediatePropagation();this.close();}});
                  }};""")
                await page.goto(f'http://127.0.0.1:{port}/terminal?task_id=CCI:test')
                await opening.wait()
                await page.set_viewport_size({'width':1250,'height':800})
                await page.wait_for_timeout(100)
                release_open.set()
                await expect(page.locator('#terminal-led')).to_have_class('terminal-led connected')
                async def dimensions():return await page.evaluate('({cols:__testTerminal.cols,rows:__testTerminal.rows})')
                async def same_size():return shells[-1].size==await dimensions()
                await until(same_size)
                async def command(command):
                    await page.locator('.xterm-helper-textarea').press_sequentially(command)
                    await page.locator('.xterm-helper-textarea').press('Enter')
                await command("printf '\\036'; stty size; printf '\\037\\n'")
                size=await dimensions()
                await expect(page.locator('.xterm-rows')).to_contain_text(f"{size['rows']} {size['cols']}")
                await command(f'{tmux} -L {tmux_name} -f /dev/null new-session -s smoke /bin/bash --noprofile --norc')
                async def in_tmux():return await page.evaluate("__testTerminal.buffer.active.type==='alternate'")
                await until(in_tmux)
                # A status line on the last xterm row proves full-height rendering.
                async def status_bottom():return 'smoke' in await page.evaluate("__testTerminal.buffer.active.getLine(__testTerminal.rows-1).translateToString()")
                await until(status_bottom)
                # Drop only the browser transport while tmux is attached. Resume
                # the same PTY, alternate screen and rendered output, not a new shell.
                original_shell=shells[-1]
                # Freeze all page JavaScript beyond the former 45 s application
                # ping deadline; native transport and the cloud PTY must survive.
                cdp=await page.context.new_cdp_session(page)
                await cdp.send('Page.setWebLifecycleState',{'state':'frozen'})
                await asyncio.sleep(47)
                assert not original_shell.closed
                await cdp.send('Page.setWebLifecycleState',{'state':'active'})
                await page.evaluate("window.dispatchEvent(new Event('focus'))")
                await until(in_tmux);await until(status_bottom)
                await page.evaluate("__terminalSockets.at(-1).close()")
                await expect(page.locator('#terminal-status')).to_contain_text('已恢复',timeout=15000)
                assert shells==[original_shell] and not original_shell.closed
                await until(in_tmux);await until(status_bottom)
                await command("printf '\\nRECONNECTED_OK\\n'")
                await expect(page.locator('.xterm-rows')).to_contain_text('RECONNECTED_OK')
                # The cloud accepted this input but the browser loses its ack.
                # Recovery must synchronize sequence without replaying the command.
                await page.locator('.xterm-helper-textarea').press_sequentially('REPLAY_COUNT=$(( ${REPLAY_COUNT:-0} + 1 ))')
                await page.wait_for_timeout(200)
                socket_count=await page.evaluate('__terminalSockets.length')
                await page.evaluate('window.__dropInputAck=true')
                await page.locator('.xterm-helper-textarea').press('Enter')
                await page.wait_for_function('n => __terminalSockets.length > n',arg=socket_count)
                await expect(page.locator('#terminal-led')).to_have_class('terminal-led connected')
                await command("printf '\\nCOUNT_%s_END\\n' \"$REPLAY_COUNT\"")
                await expect(page.locator('.xterm-rows')).to_contain_text('COUNT_1_END')
                assert shells==[original_shell] and not original_shell.closed
                for viewport in ({'width':1024,'height':720},{'width':5120,'height':1440},{'width':1600,'height':1050}):
                    await page.set_viewport_size(viewport);await until(same_size);await until(status_bottom)
                    current=await dimensions();assert current['cols']<=500 and current['rows']<=200
                await page.locator('#terminal-font-up').click();await until(same_size);await until(status_bottom)
                await page.locator('#terminal-fullscreen').click();await until(same_size);await until(status_bottom)
                await page.evaluate('document.exitFullscreen()');await until(same_size);await until(status_bottom)
                # Wrap beyond one line, including double-width Unicode, and preserve cursor alignment.
                await command("printf '\\n%s\\n' '"+'中文-'+('1234567890'*25)+"-END'")
                async def output_complete():
                    return await page.evaluate("Array.from({length:__testTerminal.rows},(_,i)=>__testTerminal.buffer.active.getLine(i).translateToString(true)).join('').includes('中文-'+ '1234567890'.repeat(25)+'-END')")
                await until(output_complete)
                geometry=await page.evaluate("""(() => {
                  const box = el => {const r=el.getBoundingClientRect();return {top:r.top,bottom:r.bottom,height:r.height};};
                  return {screen:box(document.getElementById('terminal-screen')),xterm:box(document.querySelector('.xterm-screen')),last:box(document.querySelector('.xterm-rows').lastElementChild),lineHeight:getComputedStyle(document.querySelector('.xterm-rows').lastElementChild).lineHeight,rows:__testTerminal.rows,cell:__testTerminal._core._renderService.dimensions.css.cell};
                })()""")
                print(json.dumps(geometry),flush=True)
                assert geometry['last']['bottom']<=geometry['screen']['bottom'],geometry
                await page.screenshot(path=str(ROOT/'data/terminal-tmux-desktop.png'))
                # Leave only this isolated tmux and verify normal-buffer restoration.
                await page.locator('.xterm-helper-textarea').press('Control+b');await page.locator('.xterm-helper-textarea').press('d')
                async def normal():return await page.evaluate("__testTerminal.buffer.active.type==='normal'")
                await until(normal)
                await page.set_viewport_size({'width':390,'height':844});await until(same_size)
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                await page.screenshot(path=str(ROOT/'data/terminal-tmux-mobile.png'))
                for viewport in ({'width':844,'height':390},{'width':390,'height':430}):
                    await page.set_viewport_size(viewport);await until(same_size)
                    for key in ('interrupt','tab','escape'):
                        await expect(page.locator(f'[data-terminal-key={key}]')).to_be_visible()
                    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                # Manual recovery keeps the shell while latest dimensions win.
                await page.locator('#terminal-retry').click()
                await page.set_viewport_size({'width':1250,'height':800})
                await expect(page.locator('#terminal-led')).to_have_class('terminal-led connected')
                await until(same_size)
                await page.locator('#terminal-disconnect').click()
                # Existing APKs use HTTP polling instead of WebSockets. A failed
                # read must also resume the same PTY and synchronize input sequence.
                mobile=await browser.new_page(viewport={'width':390,'height':844})
                mobile.on('pageerror',lambda e:errors.append(str(e)))
                await mobile.add_init_script("""const NativeSocket=WebSocket;window.WebSocket=class extends NativeSocket{
                  constructor(url){super(url.replace('/api/terminal/socket','/api/terminal/unavailable'));}};""")
                await mobile.goto(f'http://127.0.0.1:{port}/terminal?task_id=CCI:test')
                await expect(mobile.locator('#terminal-led')).to_have_class('terminal-led connected')
                await expect(mobile.locator('#terminal-transport')).to_have_text('兼容连接')
                mobile_shell=shells[-1];shell_count=len(shells)
                failed=[False]
                async def fail_read_once(route):
                    if not failed[0]:
                        failed[0]=True
                        await route.fulfill(status=503,json={'error':'temporary test interruption'})
                    else:await route.continue_()
                await mobile.route('**/api/terminal/read*',fail_read_once)
                await mobile.locator('.xterm-helper-textarea').press_sequentially("HTTP_MARKER=retained; printf '\\nHTTP_READY\\n'")
                await mobile.locator('.xterm-helper-textarea').press('Enter')
                await expect(mobile.locator('#terminal-status')).to_contain_text('已恢复',timeout=40000)
                await mobile.locator('.xterm-helper-textarea').press_sequentially('echo HTTP_$HTTP_MARKER')
                await mobile.locator('.xterm-helper-textarea').press('Enter')
                await expect(mobile.locator('.xterm-rows')).to_contain_text('HTTP_retained')
                assert len(shells)==shell_count and not mobile_shell.closed
                await mobile.locator('#terminal-disconnect').click()
                assert not errors,errors
                await browser.close()
                print('PTY/tmux passed: initial size, Unicode wrapping, alternate screen, resize, font, fullscreen, detach, mobile, frozen-page keepalive, same-shell transport recovery, lost input ack, HTTP fallback recovery.')
        finally:
            subprocess.run([tmux,'-L',tmux_name,'kill-server'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            server.should_exit=True;await job
            for shell in shells:await shell.close()

if __name__=='__main__':asyncio.run(main())
