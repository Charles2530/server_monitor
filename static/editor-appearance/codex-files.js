/* Runs in the VS Code webview shell, only for openai.chatgpt. No extension patching. */
(function installCodexFileStyles(style) {
  'use strict';
  if (new URL(location.href).searchParams.get('extensionId')?.toLowerCase() !== 'openai.chatgpt') return;
  const documents = new WeakMap();
  const frames = new WeakSet();
  const scope = '[data-composer-attachments-row], [class*="_attachmentsDefault_"]';
  const candidates = '.group\\/composer-attachment, .group\\/file-attachment';
  function attach(frame) {
    try {
      const doc = frame.contentDocument;
      if (!doc?.body || !doc.defaultView?.CSSStyleSheet || !('adoptedStyleSheets' in doc)) return;
      const previous = documents.get(doc);
      if (previous?.body === doc.body && doc.adoptedStyleSheets.includes(previous.sheet)) return;
      previous?.observer.disconnect();
      const sheet = new doc.defaultView.CSSStyleSheet();
      sheet.replaceSync(style);
      doc.adoptedStyleSheets = [...doc.adoptedStyleSheets.filter(item => item !== previous?.sheet), sheet];
      function refresh() {
        for (const card of doc.querySelectorAll(candidates)) {
          const tile = card.classList.contains('group/composer-attachment');
          const label = card.querySelector('.truncate');
          const open = card.querySelector('button[aria-label]');
          const remove = card.lastElementChild;
          const surface = card.firstElementChild;
          const content = card.children[1];
          const shape = tile
            ? surface?.classList.contains('composer-attachment-surface') &&
              surface.children.length === 4 && surface.children[1].contains(label)
            : card.children.length === 3 && surface === open &&
              content?.classList.contains('pointer-events-none') &&
              content.children.length === 2 && content.contains(label);
          // Unknown layouts, uploads in progress, and actual previews stay native.
          const known = card.closest(scope) && label?.textContent.trim() && open &&
            remove?.tagName === 'BUTTON' && remove !== open &&
            !card.querySelector('img, video, canvas, [role="progressbar"]') &&
            shape;
          if (!known) {
            delete card.dataset.ilabCodexFile;
            continue;
          }
          card.dataset.ilabCodexFile = tile ? 'tile' : 'card';
          if (!card.title || card.title === card.dataset.ilabFileTitle) {
            card.title = label.textContent.trim();
            card.dataset.ilabFileTitle = card.title;
          }
        }
      }
      refresh();
      // Batch React updates; never change native controls, events, or attachment data.
      let scheduled = false;
      const relevant = `${scope}, ${candidates}`;
      const observer = new MutationObserver(changes => {
        if (scheduled) return;
        // Streaming chat text does not change attachment layout. Inspect only
        // changed subtrees before scheduling the existing attachment scan.
        const changed = changes.some(change => {
          const target = change.target.nodeType === 1 ? change.target : change.target.parentElement;
          return target?.closest(relevant) || [...change.addedNodes, ...change.removedNodes].some(node =>
            node.nodeType === 1 && (node.matches(relevant) || node.querySelector(relevant)));
        });
        if (!changed) return;
        scheduled = true;
        queueMicrotask(() => { scheduled = false; refresh(); });
      });
      observer.observe(doc.body, { childList: true, subtree: true, characterData: true });
      documents.set(doc, { sheet, observer, body: doc.body });
    } catch (_) {
      // Inaccessible/replaced frames must not interfere with the editor.
    }
  }
  function scan() {
    for (const frame of document.querySelectorAll('iframe#active-frame, iframe#pending-frame')) {
      if (!frames.has(frame)) {
        frames.add(frame);
        frame.addEventListener('load', () => attach(frame));
      }
      attach(frame);
    }
  }
  new MutationObserver(scan).observe(document.documentElement, {
    childList: true, subtree: true, attributes: true, attributeFilter: ['id'],
  });
  scan();
})(/*__ILAB_CODEX_FILE_STYLE__*/);
