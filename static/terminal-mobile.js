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
      document.getElementById("terminal-menu-target").textContent = document.getElementById("terminal-target-name").textContent;
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
    // Android 229 keydowns must not also schedule xterm's textarea-diff sender.
    // Committed text below has one owner: input events, or xterm's composition helper.
    this.imeKey = false;
    term.attachCustomKeyEventHandler(event => {
      if (this.query.matches && event.keyCode === 229) {
        if (event.type === "keydown") this.imeKey = true;
        return false;
      }
      if (event.type === "keydown") this.imeKey = false;
      return true;
    });
    const edits = { deleteContentBackward: "\x7f", deleteContentForward: "\x1b[3~", deleteWordBackward: "\x17", insertLineBreak: "\r", insertParagraph: "\r" };
    const sendEdit = data => {
      if (!this.isAlive()) return;
      const send = () => { if (this.isAlive()) term.input(data, true); };
      // Let xterm commit the candidate before Enter/Delete reaches the PTY.
      if (this.compositionEnding) setTimeout(send, 0); else send();
    };
    term.element.addEventListener("beforeinput", event => {
      if (event.target !== term.textarea || !this.query.matches || !event.cancelable || (this.composing && !this.compositionEnding)) return;
      if (Object.hasOwn(edits, event.inputType)) {
        event.preventDefault(); event.stopPropagation();
        this.imeKey = false; sendEdit(edits[event.inputType]);
      }
    }, true);
    term.element.addEventListener("input", event => {
      if (event.target !== term.textarea || !this.query.matches) return;
      if (this.compositionEnding && Object.hasOwn(edits, event.inputType)) {
        // input-only edits already changed the DOM. Let xterm commit the candidate
        // snapshot first, then apply this distinct Enter/Delete once to the PTY.
        event.stopPropagation(); term.textarea.value = this.compositionValue;
        sendEdit(edits[event.inputType]); return;
      }
      if (this.composing) {
        // xterm's compositionend task reads the committed textarea once.
        if (this.compositionEnding) this.compositionValue = term.textarea.value;
        event.stopPropagation(); return;
      }
      if (event.inputType === "insertText" && this.imeKey) {
        event.stopPropagation(); this.imeKey = false;
        if (this.isAlive() && event.data) term.input(event.data, true);
      } else if (Object.hasOwn(edits, event.inputType)) {
        event.stopPropagation(); this.imeKey = false; sendEdit(edits[event.inputType]);
      } else if (event.inputType === "insertFromPaste") {
        event.stopPropagation(); this.imeKey = false; this.resetCtrl();
        if (this.isAlive() && event.data) term.paste(event.data);
      }
    }, true);
    term.element.addEventListener("paste", () => this.resetCtrl(), true);
    term.textarea.addEventListener("compositionstart", () => {
      clearTimeout(this.compositionTimer);
      this.composing = true; this.compositionEnding = false; this.imeKey = false;
    });
    term.textarea.addEventListener("compositionend", () => {
      // xterm commits composition in its own end-of-event task; don't overtake it with Enter.
      this.compositionValue = term.textarea.value;
      this.compositionEnding = true; this.imeKey = false;
      this.compositionTimer = setTimeout(() => { this.composing = false; this.compositionEnding = false; }, 0);
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
    const indicator = document.getElementById("terminal-mobile-status");
    indicator.setAttribute("aria-label", enabled ? "已连接" : "未连接");
    indicator.title = enabled ? "已连接" : "未连接";
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
  resetCtrl() { this.ctrl = false; this.ctrlButton.setAttribute("aria-pressed", "false"); }
  input(data) {
    if (!this.ctrl) return data;
    this.resetCtrl();
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
      const height = viewport?.height || innerHeight;
      document.body.classList.toggle("terminal-space-saving", compact && height <= 500);
      document.documentElement.style.setProperty("--terminal-viewport-height", `${height}px`);
      document.documentElement.style.setProperty("--terminal-viewport-top", `${viewport?.offsetTop || 0}px`);
    }
  }
};
