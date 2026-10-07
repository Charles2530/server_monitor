"use strict";
(() => {
  const parentOrigin = document.currentScript?.dataset.parentOrigin;
  const embedded = parentOrigin && window.parent !== window;
  function ready() {
    if (!embedded) return true;
    const workbench = document.querySelector(".monaco-workbench");
    if (!workbench || !workbench.getClientRects().length) return false;
    window.parent.postMessage({ type: "ilab-editor-ready" }, parentOrigin);
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
    for (const frame of document.querySelectorAll('iframe.webview')) {
      try {
        const url = new URL(frame.src, location.href);
        if (url.origin !== location.origin || url.searchParams.get('extensionId') !== 'openai.chatgpt' || !visible(frame)) continue;
        state = 'loading';
        const activeFrame = frame.contentDocument?.querySelector('#active-frame');
        const panel = activeFrame && visible(activeFrame) ? activeFrame.contentDocument : null;
        // A webview host (or splash logo) is not a loaded Codex interface.
        // Status uses control presence; welcome matching is limited to its static notice.
        if (panel) continueWelcome(panel);
        if (panel && [...panel.querySelectorAll('button,textarea,[role="textbox"],[contenteditable="true"]')].some(visible)) state = 'ready';
      } catch (_) { /* Inaccessible webviews remain loading; never bypass isolation. */ }
    }
    if (embedded && state !== codexState) {
      codexState = state;
      window.parent.postMessage({type: 'ilab-editor-codex', state}, parentOrigin);
    }
  }
  setInterval(inspectCodex, 2000);
  document.addEventListener('visibilitychange', inspectCodex);
})();
