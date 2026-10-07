"""Browser regression checks for the IDE's project-history request lifecycle."""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright
from browser_support import browser_launch_options

ROOT = Path(__file__).resolve().parents[1]

BOOTSTRAP = """
window.testNow = 100000;
Date.now = () => window.testNow;
window.intervals = [];
window.setInterval = (fn, delay) => { intervals.push({fn, delay}); return intervals.length; };
window.testHidden = false;
Object.defineProperty(document, 'hidden', {get: () => window.testHidden});
window.testOnline = true;
Object.defineProperty(navigator, 'onLine', {get: () => window.testOnline});
window.requests = [];
window.historyResult = {site:'test', task_id:'CCI:test', workspace:'/real/project', records:[{thread_id:'1', display_title:'Old title'}]};
window.MonitorSite = {id:'test', fetch: async (url, options) => {
  requests.push({url, body:JSON.parse(options.body)});
  if (window.deferRequest) await new Promise(resolve => window.resolveRequest = resolve);
  if (window.failRequest) throw new Error('History unavailable');
  return {ok:true, status:200, json:async () => historyResult};
}};
"""

EXPOSE = """
  window.historyTest = {
    refresh: () => refreshHistory(generation, connection),
    current: () => connection,
    report: (state, count, revision = connection.historyPayload?.revision, workspace = connection.historyPayload?.workspace) => {
      window.dispatchEvent(new MessageEvent('message', {source:$('ide-frame').contentWindow, origin:connection.origin,
        data:{type:'ilab-editor-codex-history-state', state, count, revision, workspace}}));
    },
    setup: workspace => {
      generation += 1; controller = new AbortController(); busy = false; recoveryFailures = 0;
      connection = {instance:'worker', container:'main', port:9001, origin:location.origin, workspace,
        ready:true, codexPanel:'ready', historyPayload:null, entryExpired:false};
      return connection;
    },
    cancel,
    failRecovery: () => { recoveryFailures = 1; },
    startRecovery: () => { busy = true; },
    endRecovery: () => { busy = false; recoveryFailures = 0; },
    tick: () => { for (const timer of intervals) if (timer.delay === 15000) timer.fn(); }
  };
"""

CASES = {
    'successful fetch waits for rendered filtering before claiming success': """
      historyTest.setup('/real/project'); await historyTest.refresh();
      return historyTest.current().historyState === 'loading' && historyTest.current().historyCount === 0;
    """,
    'unavailable stays unavailable across refresh until verified render': """
      historyTest.setup('/real/project'); await historyTest.refresh();
      historyTest.report('unavailable', 0); testNow += 30001; await historyTest.refresh();
      if (historyTest.current().historyState !== 'unavailable') return false;
      historyTest.report('unavailable', 0);
      return historyTest.current().historyState === 'unavailable' && historyTest.current().historyCount === 0;
    """,
    'rendered ready and empty states replace unavailable with actual matches': """
      historyTest.setup('/real/project'); await historyTest.refresh();
      historyTest.report('unavailable', 0); historyTest.report('ready', 0);
      if (historyTest.current().historyState !== 'ready' || historyTest.current().historyCount !== 0) return false;
      historyTest.report('empty', 0); return historyTest.current().historyState === 'empty';
    """,
    'stale rendered state cannot replace a newer payload status': """
      historyTest.setup('/real/project'); await historyTest.refresh();
      const revision = historyTest.current().historyPayload?.revision;
      testNow += 30001; await historyTest.refresh(); historyTest.report('unavailable', 0, revision);
      return historyTest.current().historyState === 'loading';
    """,
    'canonical workspace accepted': """
      historyTest.setup('/link/project'); historyResult.truncated = true; await historyTest.refresh();
      return historyTest.current().historyPayload?.workspace === '/real/project' && historyTest.current().historyPayload.truncated === true;
    """,
    'initial workbench and Codex ready messages start history once': """
      historyTest.setup('/real/project'); historyTest.current().ready = false; delete historyTest.current().codexPanel;
      const message = data => window.dispatchEvent(new MessageEvent('message', {
        source:document.querySelector('#ide-frame').contentWindow, origin:location.origin, data
      }));
      message({type:'ilab-editor-ready', workspace:'/real/project'});
      if (requests.length) return false;
      message({type:'ilab-editor-codex', state:'ready'});
      message({type:'ilab-editor-codex', state:'ready'});
      await new Promise(resolve => setTimeout(resolve, 0));
      return requests.length === 1 && historyTest.current().historyPayload?.records.length === 1;
    """,
    'stale workspace success discarded': """
      const current = historyTest.setup('/real/project'); deferRequest = true;
      const pending = historyTest.refresh(); current.workspace = '/other';
      resolveRequest(); await pending; return current.historyPayload === null;
    """,
    'stale workspace failure discarded': """
      const current = historyTest.setup('/real/project'); deferRequest = true; failRequest = true;
      const pending = historyTest.refresh(); current.workspace = '/other';
      resolveRequest(); await pending; return current.historyPayload === null;
    """,
    'stale connection discarded': """
      historyTest.setup('/real/project'); deferRequest = true;
      const pending = historyTest.refresh(); historyTest.setup('/real/project');
      resolveRequest(); await pending; return historyTest.current().historyPayload === null;
    """,
    'stale generation discarded': """
      const current = historyTest.setup('/real/project'); deferRequest = true;
      const pending = historyTest.refresh(); historyTest.cancel();
      resolveRequest(); await pending; return current.historyPayload === null;
    """,
    'concurrent requests coalesced': """
      historyTest.setup('/real/project'); deferRequest = true;
      const pending = Array.from({length:20}, () => historyTest.refresh());
      const count = requests.length; resolveRequest(); await Promise.all(pending);
      return count === 1;
    """,
    'cooldown and active polling update new and renamed chats': """
      historyTest.setup('/real/project'); await historyTest.refresh();
      for (let i = 0; i < 20; i++) await historyTest.refresh();
      if (requests.length !== 1) return false;
      historyResult.records = [{thread_id:'1', display_title:'Renamed title'}, {thread_id:'2', display_title:'New chat'}];
      testNow += 15001; historyTest.tick(); await new Promise(resolve => setTimeout(resolve, 0));
      if (requests.length !== 1) return false;
      testNow += 15000; historyTest.tick(); await new Promise(resolve => setTimeout(resolve, 0));
      return requests.length === 2 && historyTest.current().historyPayload.records[1]?.display_title === 'New chat'
        && historyTest.current().historyPayload.records[0]?.display_title === 'Renamed title';
    """,
    'hidden and offline lifecycle pauses polling': """
      historyTest.setup('/real/project'); await historyTest.refresh();
      testNow += 30001; testHidden = true; historyTest.tick(); await historyTest.refresh();
      if (requests.length !== 1) return false;
      testHidden = false; testOnline = false; historyTest.tick(); await historyTest.refresh();
      if (requests.length !== 1 || historyTest.current().historyPayload?.error) return false;
      testOnline = true; historyTest.tick(); await new Promise(resolve => setTimeout(resolve, 0));
      return requests.length === 2;
    """,
    'disconnected and recovering editor does not poll': """
      historyTest.setup('/real/project'); historyTest.failRecovery(); await historyTest.refresh();
      if (requests.length) return false;
      historyTest.endRecovery(); historyTest.startRecovery(); await historyTest.refresh();
      if (requests.length) return false;
      historyTest.endRecovery(); historyTest.current().entryExpired = true; await historyTest.refresh();
      return requests.length === 0;
    """,
    'hidden Codex panel and preserved editor do not poll': """
      historyTest.setup('/real/project'); historyTest.current().codexPanel = 'hidden'; await historyTest.refresh();
      if (requests.length) return false;
      historyTest.current().codexPanel = 'ready';
      const select = document.createElement('select'); select.id = 'ide-preserved-select';
      select.innerHTML = '<option>Current</option><option>Previous</option>'; select.selectedIndex = 1;
      document.body.append(select); await historyTest.refresh(); return requests.length === 0;
    """,
    'only ready Codex panels poll': """
      historyTest.setup('/real/project'); delete historyTest.current().codexPanel; await historyTest.refresh();
      historyTest.current().codexPanel = 'loading'; await historyTest.refresh();
      return requests.length === 0;
    """,
    'recovery cadence does not starve history polling': """
      historyTest.setup('/real/project'); await historyTest.refresh();
      testNow += 30001; historyTest.startRecovery(); historyTest.tick();
      await new Promise(resolve => setTimeout(resolve, 0));
      if (requests.length !== 1) return false;
      historyTest.endRecovery(); testNow += 15000; historyTest.tick();
      await new Promise(resolve => setTimeout(resolve, 0)); return requests.length === 2;
    """,
    'pagehide cancellation does not start a request': """
      historyTest.setup('/real/project'); historyTest.cancel(); await historyTest.refresh();
      return requests.length === 0 && historyTest.current().historyPayload === null;
    """,
    'error retains native list and respects cooldown': """
      historyTest.setup('/real/project'); failRequest = true; await historyTest.refresh();
      await historyTest.refresh();
      return historyTest.current().historyPayload?.error === 'history_unavailable' && requests.length === 1;
    """,
}


async def main():
    source = (ROOT / 'static/ide.js').read_text()
    assert source.endswith('  load();\n})();\n')
    source = source.replace('  load();\n})();\n', EXPOSE + '\n})();\n')
    ids = ['ide-focus', 'ide-back', 'ide-task', 'ide-status', 'ide-setup-status', 'ide-instance',
           'ide-container', 'ide-picker', 'ide-open', 'ide-retry', 'ide-progress', 'ide-progress-bar',
           'ide-progress-value', 'ide-progress-detail', 'ide-progress-stage']
    html = ''.join(f'<div id="{name}"></div>' for name in ids) + '<iframe id="ide-frame"></iframe>'
    failures = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**browser_launch_options())
        for name, script in CASES.items():
            page = await browser.new_page()
            await page.route('**/*', lambda route: route.fulfill(content_type='text/html', body=html))
            await page.goto('http://localhost:9000/ide?task_id=CCI:test')
            await page.evaluate(BOOTSTRAP)
            await page.add_script_tag(content=source)
            # A buggy implementation can leave duplicate deferred requests pending;
            # resolve all fetches together so the bound assertion still reports.
            await page.evaluate("""() => {
              let resolver;
              Object.defineProperty(window, 'resolveRequest', {get: () => resolver, set: next => {
                const previous = resolver; resolver = () => { previous?.(); next(); };
              }});
            }""")
            passed = await page.evaluate('async () => {' + script + '}')
            print(('PASS ' if passed else 'FAIL ') + name)
            if not passed:
                failures.append(name)
            await page.close()
        await browser.close()
    assert not failures, failures


if __name__ == '__main__':
    asyncio.run(main())
