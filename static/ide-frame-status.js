"use strict";
(() => {
  // Refresh the bundled workbench styles independently of the font cache.
  const appearanceStyle = document.querySelector('link[href="/_ilab_appearance/fonts.css"]');
  if (appearanceStyle) appearanceStyle.href += '?v=20261012-2';
  const parentOrigin = document.currentScript?.dataset.parentOrigin;
  const embedded = parentOrigin && window.parent !== window;
  function openedWorkspace() {
    try {
      const value = new URL(location.href).searchParams.get('folder');
      return typeof value === 'string' && value.startsWith('/') && value.length <= 4096 ? value : null;
    } catch (_) { return null; }
  }
  function ready() {
    if (!embedded) return true;
    const workbench = document.querySelector(".monaco-workbench");
    if (!workbench || !workbench.getClientRects().length) return false;
    window.parent.postMessage({ type: "ilab-editor-ready", workspace: openedWorkspace() }, parentOrigin);
    return true;
  }
  const observer = new MutationObserver(() => { if (ready()) observer.disconnect(); });
  if (!ready()) observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["class", "style"] });
  if (embedded) window.addEventListener('message', event => {
    if (event.source !== window.parent || event.origin !== parentOrigin || event.data?.type !== 'ilab-editor-probe' || typeof event.data.id !== 'string' || event.data.id.length > 100) return;
    const workbench = document.querySelector('.monaco-workbench');
    if (workbench && workbench.getClientRects().length) {
      // Fixed code-server UI: a permanent remote failure leaves the workbench
      // mounted but stops its reconnect loop. Rendering alone is not recovery.
      const messages = [...document.querySelectorAll('.monaco-dialog-box .dialog-message-text,.monaco-dialog-box .dialog-message-detail')]
        .filter(visible).map(element => element.textContent.trim());
      const host = document.getElementById('status.host');
      const hostText = host && visible(host) ? host.textContent : '';
      const disconnected = messages.some(text => ['Cannot reconnect. Please reload the window.', '无法重新连接。请重新加载窗口。'].includes(text))
        || /Disconnected from |已与 .+ 断开连接/.test(hostText);
      const reconnecting = !disconnected && (messages.some(text => /^(?:Disconnected\. )?Attempting to reconnect|^(?:已断开连接。)?正在尝试(?:在 \d+ 秒内)?重新连接/.test(text))
        || /Reconnecting to |正在重新连接到 /.test(hostText));
      window.parent.postMessage({type: 'ilab-editor-alive', id: event.data.id, disconnected, reconnecting}, parentOrigin);
    }
  });
  let codexState;
  function visible(element) {
    const style = element.ownerDocument.defaultView.getComputedStyle(element);
    const box = element.getBoundingClientRect();
    return style.visibility === 'visible' && style.opacity !== '0' && box.width > 0 && box.height > 0;
  }
  let historyPayload = null, showUnclassified = false, historyTimer = null, historyRevision = 0;
  const observedHistoryPanels = new WeakSet(), historySignatures = new WeakMap();
  function normalizeText(value) { return String(value || '').replace(/\s+/g, ' ').trim(); }
  function codexPanels(includeLoading = false) {
    const panels = [];
    for (const frame of document.querySelectorAll('iframe.webview')) {
      try {
        const url = new URL(frame.src, location.href);
        if (url.origin !== location.origin || url.searchParams.get('extensionId') !== 'openai.chatgpt' || !visible(frame)) continue;
        const shell = frame.contentDocument;
        if (shell?.head && !shell.getElementById('ilab-codex-files')) {
          const script = shell.createElement('script');
          script.id = 'ilab-codex-files'; script.src = '/_ilab_appearance/codex-files.js';
          shell.head.append(script);
          shell.addEventListener('load', scheduleProjectHistory, true);
        }
        const activeFrame = shell?.querySelector('#active-frame');
        const panel = activeFrame && visible(activeFrame) ? activeFrame.contentDocument : null;
        if (panel || includeLoading) panels.push(panel);
      } catch (_) { /* Keep the editor usable when a webview is not accessible. */ }
    }
    return panels;
  }
  function historyRows(panel) {
    const isArchive = element => /^(archive chat|归档聊天)$/i.test(
      normalizeText(element.getAttribute('aria-label') || element.textContent));
    const legacy = [...panel.querySelectorAll('button')].filter(row =>
      [...row.querySelectorAll('button')].some(isArchive) && !isArchive(row));
    const current = [...panel.querySelectorAll('[role="button"][aria-label]')].filter(row =>
      [...row.querySelectorAll('button,[role="button"]')].some(isArchive));
    return [...new Set([...legacy, ...current])];
  }
  function rowThreadId(row) {
    for (const name of ['data-thread-id', 'data-conversation-id', 'data-session-id', 'data-chat-id']) {
      const value = row.getAttribute(name);
      if (value) return value;
    }
    // Codex 26.5908.31748's localTaskRow keeps conversationId on its React
    // component instead of the DOM. Read only that row's component ancestors;
    // titles, React keys and surrounding list/page IDs are not identities.
    try {
      const key = Object.keys(row).find(name => name.startsWith('__reactFiber$'));
      const fiber = key && row[key];
      if (!row.isConnected || fiber?.stateNode !== row) return '';
      for (let node = fiber.return, depth = 0; node && depth < 16; node = node.return, depth++) {
        if (node.stateNode != null) break;
        const props = node.memoizedProps;
        if (props && Object.hasOwn(props, 'conversationId')) {
          const id = props.conversationId;
          // React can share children between old/current trees. Never infer
          // the committed owner from return/root pointers or use a stale ID.
          const other = node.alternate?.memoizedProps;
          if (node.alternate && (!other || !Object.hasOwn(other, 'conversationId')
              || other.conversationId !== id || (other.hostId ?? 'local') !== (props.hostId ?? 'local'))) return '';
          return (props.hostId == null || props.hostId === 'local')
            && typeof id === 'string' && /^[A-Za-z0-9][A-Za-z0-9-]{0,127}$/.test(id) ? id : '';
        }
      }
    } catch (_) { /* Unknown component layouts retain their native rows. */ }
    return '';
  }
  function historyBanner(panel, visibleCount, rowCount, unknownCount) {
    let banner = panel.querySelector('[data-ilab-history-filter]');
    if (!banner) {
      banner = panel.createElement ? panel.createElement('div') : document.createElement('div');
      banner.dataset.ilabHistoryFilter = '';
      banner.style.cssText = 'display:flex;align-items:center;gap:8px;padding:7px 10px;margin:6px 8px;border:1px solid color-mix(in srgb,currentColor 18%,transparent);border-radius:7px;font:12px/1.4 system-ui,sans-serif;opacity:.86';
      const label = banner.ownerDocument.createElement('span'); label.dataset.ilabHistoryLabel = ''; banner.append(label);
      const toggle = banner.ownerDocument.createElement('button'); toggle.type = 'button'; toggle.dataset.ilabHistoryToggle = ''; toggle.style.cssText = 'margin-left:auto;border:0;background:transparent;color:inherit;cursor:pointer;font:inherit;text-decoration:underline';
      toggle.addEventListener('click', () => { showUnclassified = !showUnclassified; applyProjectHistory(); });
      banner.append(toggle);
      panel.body?.prepend(banner);
    }
    const label = banner.querySelector('[data-ilab-history-label]');
    const toggle = banner.querySelector('[data-ilab-history-toggle]');
    if (historyPayload?.error) {
      label.textContent = '项目聊天过滤暂不可用，已保留原生列表'; toggle.hidden = true;
    } else if (rowCount && unknownCount === rowCount) {
      label.textContent = '聊天缺少稳定标识，无法可靠过滤，已保留原生列表'; toggle.hidden = true;
    } else if (!rowCount) {
      label.textContent = historyPayload?.records?.length ? '项目聊天过滤已准备，等待历史列表加载' : '当前项目暂无历史聊天'; toggle.hidden = true;
    } else {
      label.textContent = showUnclassified ? `显示全部聊天 · 当前项目 ${visibleCount} 条` : `当前项目聊天 · ${visibleCount} 条`;
      if (unknownCount) label.textContent += ` · ${unknownCount} 条聊天缺少标识，已保留`;
      toggle.hidden = false; toggle.textContent = showUnclassified ? '仅当前项目' : '显示未归类';
    }
    if (historyPayload?.truncated && !historyPayload.error) label.textContent += ' · 仅加载部分历史';
  }
  function observeHistoryPanel(panel) {
    if (observedHistoryPanels.has(panel)) return;
    const root = panel.body || panel.documentElement;
    if (!root) return;
    observedHistoryPanels.add(panel);
    const style = panel.createElement('style');
    style.textContent = '[data-ilab-history-match="0"] { display: none !important; }';
    (panel.head || root).append(style);
    const controls = 'button,[role="button"],textarea,[role="textbox"],[contenteditable="true"]';
    new MutationObserver(changes => {
      const changed = changes.some(change => {
        const target = change.target.nodeType === 1 ? change.target : change.target.parentElement;
        if (target?.closest('[data-ilab-history-filter]')) return false;
        return target?.closest('button,[role="button"],[data-ilab-history-match]')
          || [...change.addedNodes, ...change.removedNodes].some(node => node.nodeType === 1
            && (node.matches(controls) || node.querySelector(controls)));
      });
      if (changed) { historySignatures.delete(panel); scheduleProjectHistory(); }
    }).observe(root, {childList: true, subtree: true, characterData: true,
      attributes: true, attributeFilter: ['aria-label', 'data-thread-id', 'data-conversation-id', 'data-session-id', 'data-chat-id']});
  }
  function applyProjectHistory(panels = codexPanels()) {
    if (!historyPayload || document.hidden) return;
    let reported = false;
    for (const panel of panels) {
      observeHistoryPanel(panel);
      const previous = historySignatures.get(panel);
      const rows = previous?.rows || historyRows(panel);
      const ids = rows.map(rowThreadId);
      if (previous?.revision === historyRevision && previous.all === showUnclassified
          && ids.every((id, index) => id === previous.ids[index])) { reported = true; continue; }
      historySignatures.set(panel, {revision: historyRevision, all: showUnclassified, rows, ids});
      const allowedIds = new Set((historyPayload.records || []).map(row => row.thread_id).filter(Boolean));
      const matchedIds = new Set();
      let unknownCount = 0;
      for (const [index, row] of rows.entries()) {
        const id = ids[index];
        const keep = !!historyPayload.error || showUnclassified || !id || allowedIds.has(id);
        // Keep native hidden/style state intact; only our scoped marker hides rows.
        row.dataset.ilabHistoryMatch = keep ? '1' : '0';
        if (!id) unknownCount += 1;
        else if (allowedIds.has(id) && !historyPayload.error) matchedIds.add(id);
      }
      const visibleCount = matchedIds.size;
      historyBanner(panel, visibleCount, rows.length, unknownCount);
      const state = historyPayload.error || (rows.length && unknownCount === rows.length) ? 'unavailable'
        : !rows.length && allowedIds.size ? 'loading' : visibleCount ? 'ready' : 'empty';
      if (embedded) window.parent.postMessage({type:'ilab-editor-codex-history-state', state, count: visibleCount,
        workspace: historyPayload.workspace, revision: historyPayload.revision, supported: rows.length ? rows.length > unknownCount : undefined}, parentOrigin);
      reported = true;
    }
    return reported;
  }
  function scheduleProjectHistory() {
    if (historyTimer !== null) return;
    historyTimer = setTimeout(() => { historyTimer = null; inspectCodex(); }, 180);
  }
  if (embedded) window.addEventListener('message', event => {
    if (event.source !== window.parent || event.origin !== parentOrigin || event.data?.type !== 'ilab-codex-history') return;
    const records = Array.isArray(event.data.records) ? event.data.records.filter(row => row && typeof row.thread_id === 'string' && typeof row.display_title === 'string') : [];
    historyPayload = {workspace: typeof event.data.workspace === 'string' ? event.data.workspace : '', records, truncated: event.data.truncated === true, error: event.data.error || null, revision: event.data.revision};
    historyRevision += 1;
    scheduleProjectHistory();
  });
  const continuedPanels = new WeakSet();
  const welcomeText = [
    'Decide how much autonomy you want to grant',
    'For more details, see the Codex docs',
    'Codex can make mistakes',
    'Review the code it writes and commands it runs',
    'Powered by your ChatGPT account',
    'Uses your plan’s rate limits and training data preferences',
    'Continue',
  ].join('').replace(/\s+/g, '');
  function continueWelcome(panel) {
    if (continuedPanels.has(panel)) return;
    // Match only the informational welcome shipped with pinned Codex 26.5908.31748.
    // A changed page, prompt, permission choice or dialog must remain manual.
    if (panel.querySelector('input,textarea,select,form,[contenteditable="true"],[role="textbox"],[role="dialog"],[role="alertdialog"],[role="checkbox"],[role="radio"],[role="switch"]')) return;
    const buttons = [...panel.querySelectorAll('button')];
    if (buttons.length !== 1) return;
    const button = buttons[0];
    if (button.textContent.trim() !== 'Continue' || button.disabled || button.getAttribute('aria-disabled') === 'true' || !visible(button)) return;
    const links = [...panel.querySelectorAll('a[href]')];
    if (links.length !== 2 || links[0].href !== 'https://developers.openai.com/codex/ide' || links[1].href !== 'https://chatgpt.com/#settings/DataControls') return;
    if (panel.querySelectorAll('ul > li').length !== 3 || panel.body.innerText.replace(/\s+/g, '') !== welcomeText) return;
    // Do not repeat a click while the extension is persisting its welcome state.
    continuedPanels.add(panel);
    button.click();
  }
  function inspectCodex() {
    if (document.hidden) return;
    let state = 'hidden';
    const panels = codexPanels(true), readyPanels = [];
    for (const panel of panels) {
      state = 'loading';
      if (!panel) continue;
      observeHistoryPanel(panel);
      continueWelcome(panel);
      if ([...panel.querySelectorAll('button,textarea,[role="textbox"],[contenteditable="true"]')]
        .some(element => !element.closest('[data-ilab-history-filter]') && visible(element))) {
        state = 'ready'; readyPanels.push(panel);
      }
    }
    // Early history must not add our controls before the native welcome renders.
    applyProjectHistory(readyPanels);
    if (embedded && state !== codexState) {
      codexState = state;
      window.parent.postMessage({type: 'ilab-editor-codex', state}, parentOrigin);
    }
  }
  inspectCodex();
  setInterval(inspectCodex, 4000);
  document.addEventListener('load', scheduleProjectHistory, true);
  document.addEventListener('click', scheduleProjectHistory);
  document.addEventListener('visibilitychange', inspectCodex);
})();
