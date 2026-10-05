"use strict";
// Touch presentation and keyboard controls; transport and session ownership stay in terminal.js.
window.TerminalMobile = class {
  constructor({ getTerm, isAlive, refit }) {
    this.getTerm = getTerm; this.isAlive = isAlive; this.refit = refit;
    this.composing = false; this.ctrl = false; this.compact = false;
    this.menu = document.getElementById("terminal-menu");
    this.panels = ["terminal-tools", "terminal-setup", "terminal-hint", "terminal-official"].map(id => {
      const node = document.getElementById(id), marker = document.createComment(id);
      node.before(marker); return { node, marker };
    });
    this.query = matchMedia("(pointer: coarse), (max-width: 760px)");
    this.query.addEventListener("change", refit);
    document.getElementById("terminal-more").addEventListener("click", () => {
      if (this.composing) { this.compositionNotice(); return; }
      this.menu.showModal();
    });
    document.getElementById("terminal-menu-close").addEventListener("click", () => this.menu.close());
    this.menu.addEventListener("click", e => { if (e.target === this.menu) this.menu.close(); });
    document.getElementById("terminal-mobile-keyboard").addEventListener("click", () => {
      const term = this.getTerm();
      if (!term || !this.isAlive()) return;
      if (document.activeElement === term.textarea) term.blur(); else term.focus();
    });
    document.getElementById("terminal-mobile-keyboard").addEventListener("pointerdown", e => e.preventDefault());
    this.ctrlButton = document.querySelector('[data-terminal-key="ctrl"]');
    this.ctrlButton.addEventListener("click", () => {
      if (!this.allowShortcut()) return;
      this.ctrl = !this.ctrl; this.ctrlButton.setAttribute("aria-pressed", String(this.ctrl));
    });
    // Avoid blurring an active IME/soft keyboard between pointerdown and click.
    document.querySelectorAll("[data-terminal-key]").forEach(button => {
      button.addEventListener("pointerdown", e => { if (e.button === 0) e.preventDefault(); });
    });
  }
  bind(term) {
    term.textarea.addEventListener("compositionstart", () => { this.composing = true; });
    term.textarea.addEventListener("compositionend", () => {
      // xterm commits composition in its own end-of-event task; don't overtake it with Enter.
      setTimeout(() => { this.composing = false; }, 0);
    });
    term.textarea.addEventListener("focus", () => this.keyboardState(true));
    term.textarea.addEventListener("blur", () => this.keyboardState(false));
    this.updateControls();
  }
  keyboardState(focused) {
    document.getElementById("terminal-mobile-keyboard").setAttribute("aria-pressed", String(focused));
  }
  updateControls() {
    const enabled = this.isAlive();
    document.querySelectorAll("[data-terminal-key]").forEach(button => { button.disabled = !enabled; });
    document.getElementById("terminal-mobile-keyboard").disabled = !enabled;
    if (!enabled) { this.ctrl = false; this.ctrlButton.setAttribute("aria-pressed", "false"); }
  }
  compositionNotice() {
    document.getElementById("terminal-hint").textContent = "请先确认输入法候选，再使用终端快捷键。";
    document.getElementById("terminal-input-notice").textContent = "请先确认输入法候选";
    clearTimeout(this.noticeTimer);
    this.noticeTimer = setTimeout(() => { document.getElementById("terminal-input-notice").textContent = ""; }, 2500);
  }
  allowShortcut() {
    if (!this.isAlive()) return false;
    if (this.composing) { this.compositionNotice(); return false; }
    return true;
  }
  input(data) {
    if (!this.ctrl) return data;
    this.ctrl = false; this.ctrlButton.setAttribute("aria-pressed", "false");
    if (/^[a-z]$/i.test(data)) return String.fromCharCode(data.toUpperCase().charCodeAt(0) - 64);
    // Conventional terminal aliases; masking arbitrary punctuation can send XON/XOFF.
    const controls = { " ": "\x00", "@": "\x00", "3": "\x1b", "[": "\x1b", "4": "\x1c", "\\": "\x1c",
      "5": "\x1d", "]": "\x1d", "6": "\x1e", "^": "\x1e", "7": "\x1f", "_": "\x1f", "8": "\x7f", "?": "\x7f" };
    return Object.hasOwn(controls, data) ? controls[data] : data;
  }
  updateViewport() {
    const active = !document.getElementById("terminal-workspace").hidden;
    const compact = this.query.matches && active;
    if (compact !== this.compact) {
      this.compact = compact;
      if (this.menu.open) this.menu.close();
      for (const { node, marker } of this.panels) {
        if (compact) document.getElementById("terminal-menu-content").append(node);
        else marker.after(node);
      }
      document.body.classList.toggle("terminal-compact", compact);
    }
    const viewport = window.visualViewport;
    // Pinch zoom should magnify the existing terminal, not resize the remote PTY.
    if (!viewport || Math.abs(viewport.scale - 1) < .05) {
      document.documentElement.style.setProperty("--terminal-viewport-height", `${viewport?.height || innerHeight}px`);
      document.documentElement.style.setProperty("--terminal-viewport-top", `${viewport?.offsetTop || 0}px`);
    }
  }
};
