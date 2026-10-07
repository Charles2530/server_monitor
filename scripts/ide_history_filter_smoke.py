"""Browser fixture for project-scoped Codex history filtering."""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright, expect, TimeoutError as PlaywrightTimeoutError
from browser_support import browser_launch_options

ROOT = Path(__file__).resolve().parents[1]


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**browser_launch_options())
        page = await browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        async def handle_route(route):
            request = route.request
            req = request
            path = req.url.split('/', 3)[-1].split('?')[0]
            if path == 'status.js':
                body = (ROOT / 'static/ide-frame-status.js').read_text()
            elif path == 'wrapper':
                body = '''<script>window.codexStates = []; window.historyStates = []; addEventListener('message', event => {if(event.data?.type === 'ilab-editor-codex') codexStates.push(event.data.state); if(event.data?.type === 'ilab-editor-codex-history-state') historyStates.push(event.data);});</script><iframe id="ide-frame" src="/editor" style="width:900px;height:700px"></iframe>'''
            elif path == 'editor':
                body = '''<script src="/status.js" data-parent-origin="http://editor.test"></script>
                    <main class="monaco-workbench" style="width:900px;height:700px">
                    <iframe class="webview" src="/pre?extensionId=openai.chatgpt" style="width:800px;height:600px"></iframe></main>'''
            elif path == 'pre':
                body = '<iframe id="active-frame" src="/panel" style="width:700px;height:500px"></iframe>'
            elif path == 'panel':
                body = '''<main><h1>Chats</h1><div id="rows"></div><script>
                    for (const [id, title] of [['1', 'Project history'], ['other-project', 'Unrelated history'], ['2', 'Nested project history']]) {
                      const row = document.createElement('button'); row.dataset.testRow = '1'; row.dataset.threadId = id;
                      row.append(document.createTextNode(title));
                      const archive = document.createElement('button'); archive.textContent = 'Archive chat'; row.append(archive);
                      document.querySelector('#rows').append(row);
                    }
                </script></main>'''
            else:
                body = ''
            await route.fulfill(content_type='text/html; charset=utf-8', body=body)

        await page.route('**/*', handle_route)
        await page.goto('http://editor.test/wrapper')
        editor = page.frame_locator('#ide-frame')
        panel = editor.frame_locator('iframe.webview').frame_locator('#active-frame')
        await expect(panel.locator('[data-test-row]').first).to_be_attached()
        await page.locator('#ide-frame').evaluate("(frame) => frame.contentWindow.postMessage({type:'ilab-codex-history', workspace:'/workspace/project', records:[{thread_id:'1', display_title:'Project history', cwd:'/workspace/project', updated_at:2},{thread_id:'2', display_title:'Nested project history', cwd:'/workspace/project/src', updated_at:1}]}, 'http://editor.test')")
        await expect(panel.locator('[data-test-row]').nth(0)).to_be_visible()
        await expect(panel.locator('[data-test-row]').nth(1)).to_be_hidden()
        await expect(panel.locator('[data-test-row]').nth(2)).to_be_visible()
        await expect(panel.locator('[data-ilab-history-filter]')).to_contain_text('当前项目聊天')
        await panel.locator('[data-ilab-history-toggle]').click()
        await expect(panel.locator('[data-test-row]').nth(1)).to_be_visible()
        await panel.locator('[data-ilab-history-toggle]').click()
        failures = []

        async def check(name, action):
            try:
                await action()
                print('PASS ' + name)
            except (AssertionError, PlaywrightTimeoutError) as error:
                failures.append(name)
                print('FAIL ' + name + ': ' + str(error).splitlines()[0])

        async def replacement():
            await panel.locator('[data-test-row]').nth(1).evaluate('row => { const replacement = row.cloneNode(true); replacement.hidden = false; replacement.removeAttribute("data-ilab-history-match"); row.replaceWith(replacement); }')
            await expect(panel.locator('[data-test-row]').nth(1)).to_be_hidden(timeout=1500)

        async def flex_override():
            await panel.locator('body').evaluate('body => { const style = document.createElement("style"); style.textContent = "[data-test-row] { display:flex }"; body.append(style); }')
            await expect(panel.locator('[data-test-row]').nth(1)).to_be_hidden(timeout=1500)

        async def native_hidden():
            await panel.locator('[data-test-row]').nth(0).evaluate('row => { row.hidden = true; row.style.display = "none"; }')
            await page.locator('#ide-frame').evaluate("frame => frame.contentWindow.postMessage({type:'ilab-codex-history', workspace:'/workspace/project', records:[], error:'history_unavailable'}, 'http://editor.test')")
            await expect(panel.locator('[data-ilab-history-filter]')).to_contain_text('暂不可用')
            await expect(panel.locator('[data-test-row]').nth(1)).to_be_visible()
            await expect(panel.locator('[data-test-row]').nth(0)).to_have_attribute('hidden', '', timeout=1500)
            await expect(panel.locator('[data-test-row]').nth(0)).to_have_css('display', 'none')

        async def renamed_and_new():
            await panel.locator('[data-test-row]').nth(0).evaluate('row => { row.hidden = false; row.style.removeProperty("display"); row.firstChild.textContent = "Renamed history"; }')
            await panel.locator('#rows').evaluate('rows => { const row = document.createElement("div"); row.setAttribute("role", "button"); row.setAttribute("aria-label", "New history"); row.dataset.testRow = "1"; row.dataset.threadId = "4"; row.textContent = "New history"; const archive = document.createElement("button"); archive.setAttribute("aria-label", "Archive chat"); archive.textContent = "Archive chat"; row.append(archive); rows.append(row); }')
            await page.locator('#ide-frame').evaluate("frame => frame.contentWindow.postMessage({type:'ilab-codex-history', workspace:'/workspace/project', records:[{thread_id:'1', display_title:'Renamed history'}, {thread_id:'4', display_title:'New history'}], truncated:true}, 'http://editor.test')")
            await expect(panel.locator('[data-test-row]').nth(0)).to_be_visible()
            await expect(panel.locator('[data-test-row]').nth(3)).to_be_visible()
            await expect(panel.locator('[data-test-row]').nth(1)).to_be_hidden()
            await expect(panel.locator('[data-ilab-history-filter]')).to_contain_text('部分历史', timeout=1500)

        async def aria_rename():
            await panel.locator('[data-test-row]').nth(3).evaluate('row => row.setAttribute("aria-label", "Unclassified rename")')
            await expect(panel.locator('[data-test-row]').nth(3)).to_be_visible()
            await panel.locator('[data-test-row]').nth(3).evaluate('row => row.dataset.threadId = "other-new-project"')
            await expect(panel.locator('[data-test-row]').nth(3)).to_be_hidden(timeout=1500)

        async def same_title_ids():
            await panel.locator('[data-test-row]').nth(1).evaluate('row => row.firstChild.textContent = "Renamed history"')
            await expect(panel.locator('[data-test-row]').nth(0)).to_be_visible()
            await expect(panel.locator('[data-test-row]').nth(1)).to_be_hidden()

        async def no_ids():
            await panel.locator('[data-test-row]').evaluate_all('rows => rows.forEach(row => row.removeAttribute("data-thread-id"))')
            for row in await panel.locator('[data-test-row]').all():
                await expect(row).to_be_visible(timeout=1500)
            await expect(panel.locator('[data-ilab-history-filter]')).to_contain_text('无法可靠过滤', timeout=1500)
            await expect(panel.locator('[data-ilab-history-toggle]')).to_be_hidden()

        async def repeated_unavailable():
            await page.evaluate('historyStates.length = 0')
            for revision in [100, 101]:
                await page.locator('#ide-frame').evaluate("(frame, revision) => frame.contentWindow.postMessage({type:'ilab-codex-history', workspace:'/workspace/project', revision, records:[{thread_id:'1', display_title:'Renamed history'}]}, 'http://editor.test')", revision)
                await page.wait_for_function("revision => historyStates.some(state => state.revision === revision && state.state === 'unavailable' && state.count === 0)", arg=revision, timeout=1500)

        async def mixed_ids():
            await panel.locator('[data-test-row]').nth(0).evaluate('row => row.dataset.threadId = "1"')
            await panel.locator('[data-test-row]').nth(1).evaluate('row => row.dataset.threadId = "other-project"')
            await expect(panel.locator('[data-test-row]').nth(0)).to_be_visible()
            await expect(panel.locator('[data-test-row]').nth(1)).to_be_hidden(timeout=1500)
            await expect(panel.locator('[data-test-row]').nth(2)).to_be_visible(timeout=1500)
            await expect(panel.locator('[data-test-row]').nth(3)).to_be_visible(timeout=1500)
            await expect(panel.locator('[data-ilab-history-filter]')).to_contain_text('缺少标识', timeout=1500)

        async def banner_not_readiness():
            await panel.locator('[data-test-row]').evaluate_all('rows => rows.forEach(row => row.style.visibility = "hidden")')
            await expect(panel.locator('[data-ilab-history-toggle]')).to_be_visible()
            await page.wait_for_function("codexStates.at(-1) === 'loading'", timeout=6500)

        async def loading_inner():
            await panel.locator('[data-test-row]').evaluate_all('rows => rows.forEach(row => row.style.visibility = "visible")')
            await page.wait_for_function("codexStates.at(-1) === 'ready'", timeout=6500)
            await editor.frame_locator('iframe.webview').locator('#active-frame').evaluate('frame => frame.style.visibility = "hidden"')
            await page.wait_for_function("codexStates.at(-1) === 'loading'", timeout=6500)

        await check('replacement nodes are filtered', replacement)
        await check('filter overrides native flex layout', flex_override)
        await check('error restores list without clearing native hidden state', native_hidden)
        await check('new and renamed chats apply updated payload and truncation', renamed_and_new)
        await check('renames retain identity and ID changes trigger filtering', aria_rename)
        await check('same title with different IDs remains correctly filtered', same_title_ids)
        await check('unidentified same-title rows retain native list with warning', no_ids)
        await check('each unidentified payload reports unavailable with zero matches', repeated_unavailable)
        await check('mixed ID rows keep unknown rows and filter known IDs', mixed_ids)
        await check('injected history toggle does not mark Codex ready', banner_not_readiness)
        await check('visible Codex host with hidden inner frame stays loading', loading_inner)
        assert not errors, errors
        assert not failures, failures
        await browser.close()
        print('PASS project history filtering')


if __name__ == '__main__':
    asyncio.run(main())
