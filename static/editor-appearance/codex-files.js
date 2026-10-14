/* Runs in the VS Code webview shell, only for openai.chatgpt. No extension patching. */
(function installCodexFileStyles(style) {
  'use strict';
  if (new URL(location.href).searchParams.get('extensionId')?.toLowerCase() !== 'openai.chatgpt') return;
  const documents = new WeakMap();
  const frames = new WeakSet();
  const dropDocuments = new WeakSet();
  const scope = '[data-composer-attachments-row], [class*="_attachmentsDefault_"]';
  const candidates = '.group\\/composer-attachment, .group\\/file-attachment, button[data-composer-attachment-pill]';
  const fileIcons = Object.freeze({
    py: 'python', pyi: 'python', pyc: 'python',
    js: 'javascript', mjs: 'javascript', cjs: 'javascript',
    ts: 'typescript', mts: 'typescript', cts: 'typescript',
    jsx: 'react', tsx: 'react', json: 'json', jsonc: 'json', jsonl: 'json',
    md: 'markdown', mdx: 'markdown', rst: 'markdown', yml: 'yaml', yaml: 'yaml', toml: 'toml',
    sh: 'console', bash: 'console', zsh: 'console', fish: 'console',
    ini: 'settings', conf: 'settings', cfg: 'settings', env: 'settings',
    sql: 'database', db: 'database', sqlite: 'database', sqlite3: 'database',
    html: 'html', htm: 'html', css: 'css', scss: 'css', c: 'c', h: 'c', cpp: 'cpp', cc: 'cpp', hpp: 'cpp', cxx: 'cpp',
    cu: 'cuda', cuh: 'cuda', ipynb: 'jupyter', pdf: 'pdf', zip: 'zip', gz: 'zip', tar: 'zip', '7z': 'zip', log: 'log',
    png: 'image', jpg: 'image', jpeg: 'image', svg: 'image', gif: 'image', webp: 'image',
    csv: 'table', tsv: 'table', parquet: 'table',
    pt: 'pytorch', pth: 'pytorch', ckpt: 'pytorch', onnx: 'pytorch', safetensors: 'pytorch',
  });
  function fileIcon(filename) {
    const name = filename.replace(/[\\/]$/, '').toLowerCase();
    const fileNames = { dockerfile: 'docker', 'docker-compose.yml': 'docker', '.gitignore': 'git', '.gitattributes': 'git', makefile: 'settings' };
    return fileNames[name] || fileIcons[name.slice(name.lastIndexOf('.') + 1)] || '';
  }
  function clearFileIcon(card) {
    delete card.dataset.ilabFileIcon;
    card.style?.removeProperty('--ilab-file-icon');
  }
  function referenceDrop(event, target = event.target) {
    const transfer = event.dataTransfer;
    const types = Array.from(transfer?.types || []);
    // Local uploads and text keep their native behavior. VS Code's internal
    // URI list includes folders and every selected resource, unlike text/uri-list.
    if (types.includes('Files') || !types.some(type =>
      type === 'application/vnd.code.uri-list' || type === 'text/uri-list')) return;
    const input = target?.closest('[data-codex-composer-root]')
      ?.querySelector('[data-codex-composer][contenteditable="true"]');
    if (!input || input.closest('[inert], [aria-disabled="true"]')) return;
    const paths = new Set();
    if (event.type === 'drop') {
      const uris = transfer.getData('application/vnd.code.uri-list') || transfer.getData('text/uri-list');
      for (const value of uris.split(/\r?\n/)) {
        try {
          const uri = new URL(value);
          if (!['file:', 'vscode-remote:'].includes(uri.protocol)) continue;
          const path = decodeURIComponent(uri.pathname).replace(/\/+$/, '') || '/';
          if (path.startsWith('/') && !path.includes('\0')) paths.add(path);
        } catch (_) { /* Ignore comments, malformed escapes and non-resource drops. */ }
      }
      if (!paths.size) return;
    }
    event.preventDefault();
    event.stopImmediatePropagation();
    transfer.dropEffect = 'copy';
    if (event.type !== 'drop') return;
    input.focus();
    for (const path of paths) {
      // Same host message as chatgpt.addFileToThread; native Codex owns the
      // reference pills, deduplication, removal and prompt serialization.
      input.ownerDocument.defaultView.postMessage({ type: 'add-context-file',
        file: { label: path.split('/').pop() || '/', path, fsPath: path } }, location.origin);
    }
  }
  const outer = document.defaultView?.frameElement;
  function workbenchDrop(event) {
    // VS Code disables webview pointer events during Explorer drags. Resolve
    // the drop position through both frames, allowing for workbench scaling.
    if (outer.ownerDocument.defaultView.getComputedStyle(outer).visibility !== 'visible') return;
    let x = event.clientX, y = event.clientY;
    const frame = document.querySelector('iframe#active-frame');
    if (!frame) return;
    for (const item of [outer, frame]) {
      const box = item.getBoundingClientRect();
      if (!box.width || !box.height || x < box.left || y < box.top ||
        x >= box.right || y >= box.bottom) return;
      x = (x - box.left) * item.clientWidth / box.width;
      y = (y - box.top) * item.clientHeight / box.height;
    }
    // Hit-test with the iframe enabled so menus/notifications above it win.
    const pointerEvents = outer.style.pointerEvents;
    outer.style.pointerEvents = 'auto';
    const hit = outer.ownerDocument.elementFromPoint(event.clientX, event.clientY) === outer;
    outer.style.pointerEvents = pointerEvents;
    if (!hit) return;
    referenceDrop(event, frame.contentDocument?.elementFromPoint(x, y));
  }
  if (outer) {
    for (const name of ['dragover', 'drop']) outer.ownerDocument.addEventListener(name, workbenchDrop, true);
    document.defaultView.addEventListener('pagehide', () => {
      for (const name of ['dragover', 'drop']) outer.ownerDocument.removeEventListener(name, workbenchDrop, true);
    }, { once: true });
  }
  function referencePath(card, label) {
    // Read the native attachment component, including restored drafts. Do not
    // rewrite its label/prompt payload or the original open/remove controls.
    const key = Object.keys(card).find(name => name.startsWith('__reactFiber$'));
    let fiber = key && card[key];
    if (!card.isConnected || fiber?.stateNode !== card) return '';
    const ancestors = new Set(), mounted = [];
    let root = fiber;
    for (let depth = 0; root && depth < 256; depth++) {
      ancestors.add(root); ancestors.add(root.alternate);
      if (!root.return) break;
      root = root.return;
    }
    if (root.return) return '';
    // A reused subtree can still point back to the old root. Follow actual
    // child links from the current root, rather than trusting those returns.
    let node = root.stateNode?.current;
    for (let depth = 0; node && node.stateNode !== card && depth < 256; depth++) {
      mounted.push(node);
      node = node.child;
      while (node && !ancestors.has(node)) node = node.sibling;
    }
    if (node?.stateNode !== card) return '';
    for (const node of mounted.reverse().slice(0, 16)) {
      if (node.stateNode != null) break;
      const props = node.memoizedProps;
      if (props?.filename !== label.textContent.trim() || typeof props.onRemove !== 'function') continue;
      const path = props.folderPath || props.resourcePath;
      if (typeof path === 'string' && path.startsWith('/') && !/[\u0000-\u001f]/.test(path)) return path.replace(/\/+$/, '') || '/';
    }
    return '';
  }
  function attach(frame) {
    try {
      const doc = frame.contentDocument;
      if (!doc?.body || !doc.defaultView?.CSSStyleSheet || !('adoptedStyleSheets' in doc)) return;
      if (!dropDocuments.has(doc)) {
        for (const name of ['dragover', 'drop']) doc.addEventListener(name, referenceDrop, true);
        dropDocuments.add(doc);
      }
      const previous = documents.get(doc);
      if (previous?.body === doc.body && doc.adoptedStyleSheets.includes(previous.sheet)) return;
      previous?.observer.disconnect();
      const sheet = new doc.defaultView.CSSStyleSheet();
      sheet.replaceSync(style);
      doc.adoptedStyleSheets = [...doc.adoptedStyleSheets.filter(item => item !== previous?.sheet), sheet];
      function refresh() {
        for (const card of doc.querySelectorAll(candidates)) {
          const pill = card.matches('button[data-composer-attachment-pill]');
          const tile = card.classList.contains('group/composer-attachment');
          const label = card.querySelector(pill ? 'span.truncate' : '.truncate');
          const open = pill ? card : card.querySelector('button[aria-label]');
          const remove = card.lastElementChild;
          const surface = card.firstElementChild;
          const content = card.children[1];
          const shape = pill ? card.children.length === 3 && content.contains(label)
            : tile
            ? surface?.classList.contains('composer-attachment-surface') &&
              surface.children.length === 4 && surface.children[1].contains(label)
            : card.children.length === 3 && surface === open &&
              content?.classList.contains('pointer-events-none') &&
              content.children.length === 2 && content.contains(label);
          // Unknown layouts, uploads in progress, and actual previews stay native.
          const known = card.closest(scope) && label?.textContent.trim() && open &&
            (pill ? remove?.matches('[role="button"][aria-label]') : remove?.tagName === 'BUTTON') && remove !== open &&
            !card.querySelector('img, video, canvas, [role="progressbar"]') &&
            shape;
          if (!known) {
            if (label) delete label.dataset.ilabFileDirectory;
            delete card.dataset.ilabCodexFile;
            clearFileIcon(card);
            continue;
          }
          // Pills are already compact; decorate their name without restyling
          // the native button or its nested keyboard-accessible remove control.
          if (!pill) card.dataset.ilabCodexFile = tile ? 'tile' : 'card';
          const path = referencePath(card, label);
          const directory = path.slice(0, path.lastIndexOf('/'));
          if (path) label.dataset.ilabFileDirectory = directory.split('/').filter(Boolean).slice(-2).join('/') || '/';
          else delete label.dataset.ilabFileDirectory;
          const icon = fileIcon(label.textContent.trim());
          if (icon) {
            card.dataset.ilabFileIcon = icon;
            card.style?.setProperty('--ilab-file-icon', `url("/_ilab_appearance/extension/icons/${icon}.svg")`);
          } else clearFileIcon(card);
          if (!card.title || card.title === card.dataset.ilabFileTitle) {
            card.title = path || label.textContent.trim();
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
