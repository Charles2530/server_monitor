"use strict";
(() => {
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
      const dialogFailed = [...document.querySelectorAll('.monaco-dialog-box .dialog-message-text,.monaco-dialog-box .dialog-message-detail')]
        .some(element => visible(element) && element.textContent.trim() === 'Cannot reconnect. Please reload the window.');
      const host = document.getElementById('status.host');
      const disconnected = dialogFailed || !!(host && visible(host) && /Disconnected from /.test(host.textContent));
      window.parent.postMessage({type: 'ilab-editor-alive', id: event.data.id, disconnected}, parentOrigin);
    }
  });
  let codexState;
  function visible(element) {
    const style = element.ownerDocument.defaultView.getComputedStyle(element);
    const box = element.getBoundingClientRect();
    return style.visibility === 'visible' && style.opacity !== '0' && box.width > 0 && box.height > 0;
  }
  let historyPayload = null, historyState = 'idle', showUnclassified = false, historyTimer = null;
  function normalizeText(value) { return String(value || '').replace(/\s+/g, ' ').trim(); }
  function codexPanels() {
    const panels = [];
    for (const frame of document.querySelectorAll('iframe.webview')) {
      try {
        const url = new URL(frame.src, location.href);
        if (url.origin !== location.origin || url.searchParams.get('extensionId') !== 'openai.chatgpt' || !visible(frame)) continue;
        const activeFrame = frame.contentDocument?.querySelector('#active-frame');
        const panel = activeFrame && visible(activeFrame) ? activeFrame.contentDocument : null;
        if (panel) panels.push(panel);
      } catch (_) { /* Keep the editor usable when a webview is not accessible. */ }
    }
    return panels;
  }
  function historyRows(panel) {
    const legacy = [...panel.querySelectorAll('button')].filter(row =>
      [...row.querySelectorAll('button')].some(child => /archive chat/i.test(normalizeText(child.textContent)))
      && !/^archive chat$/i.test(normalizeText(row.textContent)));
    const current = [...panel.querySelectorAll('[role="button"][aria-label]')].filter(row =>
      [...row.querySelectorAll('button,[role="button"]')].some(child => /archive chat/i.test(child.getAttribute('aria-label') || child.textContent || ''))
      && !/^(new chat|view all|recent chats)/i.test(row.getAttribute('aria-label') || ''));
    return [...new Set([...legacy, ...current])];
  }
  function rowTitle(row) {
    const label = normalizeText(row.getAttribute('aria-label'));
    if (label) return label;
    const direct = [...row.childNodes].filter(node => node.nodeType === Node.TEXT_NODE).map(node => normalizeText(node.textContent)).filter(Boolean).join(' ');
    if (direct) return direct;
    return normalizeText(row.innerText).split(' Archive chat ')[0].split('\n')[0];
  }
  function rowThreadId(row) {
    for (const name of ['data-thread-id', 'data-conversation-id', 'data-session-id', 'data-chat-id']) {
      const value = row.getAttribute(name);
      if (value) return value;
    }
    return '';
  }
  function historyBanner(panel, visibleCount, rowCount) {
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
    } else if (!rowCount) {
      label.textContent = historyPayload?.records?.length ? '项目聊天过滤已准备，等待历史列表加载' : '当前项目暂无历史聊天'; toggle.hidden = true;
    } else {
      label.textContent = showUnclassified ? `显示全部聊天 · 当前项目 ${visibleCount} 条` : `当前项目聊天 · ${visibleCount} 条`;
      toggle.hidden = false; toggle.textContent = showUnclassified ? '仅当前项目' : '显示未归类';
    }
  }
  function applyProjectHistory() {
    if (!historyPayload) return;
    let reported = false;
    for (const panel of codexPanels()) {
      const rows = historyRows(panel);
      const allowedIds = new Set((historyPayload.records || []).map(row => row.thread_id).filter(Boolean));
      const allowedTitles = new Set((historyPayload.records || []).map(row => normalizeText(row.display_title)).filter(Boolean));
      const hasIds = rows.some(row => rowThreadId(row));
      let visibleCount = 0;
      for (const row of rows) {
        const id = rowThreadId(row), title = normalizeText(rowTitle(row));
        const keep = !!historyPayload.error || showUnclassified || (hasIds ? allowedIds.has(id) : allowedTitles.has(title));
        row.hidden = !keep;
        row.dataset.ilabHistoryMatch = keep ? '1' : '0';
        if (keep && !showUnclassified && !historyPayload.error) visibleCount += 1;
      }
      historyBanner(panel, visibleCount, rows.length);
      const state = historyPayload.error ? 'unavailable' : (visibleCount || !rows.length ? 'ready' : 'empty');
      if (embedded && state !== historyState) { historyState = state; window.parent.postMessage({type:'ilab-editor-codex-history-state', state}, parentOrigin); }
      reported = true;
    }
    return reported;
  }
  function scheduleProjectHistory() {
    clearTimeout(historyTimer); historyTimer = setTimeout(applyProjectHistory, 120);
  }
  if (embedded) window.addEventListener('message', event => {
    if (event.source !== window.parent || event.origin !== parentOrigin || event.data?.type !== 'ilab-codex-history') return;
    const records = Array.isArray(event.data.records) ? event.data.records.filter(row => row && typeof row.thread_id === 'string' && typeof row.display_title === 'string') : [];
    historyPayload = {workspace: typeof event.data.workspace === 'string' ? event.data.workspace : '', records, error: event.data.error || null};
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
    for (const panel of codexPanels()) {
      state = 'loading';
      continueWelcome(panel);
      if ([...panel.querySelectorAll('button,textarea,[role="textbox"],[contenteditable="true"]')].some(visible)) state = 'ready';
    }
    applyProjectHistory();
    if (embedded && state !== codexState) {
      codexState = state;
      window.parent.postMessage({type: 'ilab-editor-codex', state}, parentOrigin);
    }
  }
  setInterval(inspectCodex, 2000);
  document.addEventListener('visibilitychange', inspectCodex);
})();
