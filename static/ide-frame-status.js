"use strict";
(() => {
  const parentOrigin = document.currentScript?.dataset.parentOrigin;
  if (!parentOrigin || window.parent === window) return;
  function ready() {
    const workbench = document.querySelector(".monaco-workbench");
    if (!workbench || !workbench.getClientRects().length) return false;
    window.parent.postMessage({ type: "ilab-editor-ready" }, parentOrigin);
    return true;
  }
  const observer = new MutationObserver(() => { if (ready()) observer.disconnect(); });
  if (!ready()) observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["class", "style"] });
})();
