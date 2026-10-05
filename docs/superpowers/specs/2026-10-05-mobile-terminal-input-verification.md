# Mobile terminal input audit — 2026-10-05

The report concerns the APK's embedded terminal, not the official cloud console. Earlier tests covered desktop-style typing and one CDP composition flow, but missed WebView event orders that expose xterm 5.5 input duplication.

## Reproduction and cause

With the bundled xterm, `keydown(229) → keyup(229) → input(insertText,a)` emitted two `a` chunks. `compositionend(a) → input(insertText,a)` also emitted two chunks: the regular input handler and a deferred textarea/composition sender both committed the same edit. Input-only Backspace and Enter emitted nothing.

The new app regression initially failed because the outbound API data was `aa`, expected `a`. This identifies duplication before the network/server. The server already sequences writes and refuses out-of-order execution; no additional replay mechanism was found in the APK proxy or existing transport recovery.

## Repair

Use public xterm hooks to prevent its 229 textarea-diff fallback on compact/touch clients and send that text through input events once. Keep native composition handling for candidate text and suppress the competing input path while composition owns it. Handle soft Enter/deletion in beforeinput when cancelable and in input otherwise. When an input-only edit follows compositionend, restore the committed candidate snapshot for xterm's deferred finalizer, then send the separate edit once. This avoids both losing the candidate and counting a DOM newline twice.

No equal-text or elapsed-time deduplication is used. Genuine repeated letters remain intact. IME candidate presentation and desktop keyboard handling retain the existing xterm implementation.

Additional audit fixes: shortcuts consume one-shot Ctrl; clipboard paste stays literal when Ctrl was armed; successful tool-panel paste closes the modal before focusing the terminal.

## Verification

- 21 soft-keyboard event traces cover both 229 orders, direct input, Chinese/emoji, candidate cancellation/editing, confirmation followed by Enter/Delete with and without beforeinput, Backspace/forward delete/word deletion, Enter/paragraph and paste.
- Additional assertions cover `aaabookkeeper`, hardware Backspace/Enter, bracketed paste, Ctrl→Esc→c, literal one-character clipboard paste, copy selection, restored focus, and real synthetic touch-scroll without accidental key emission.
- The gesture fixture enlarges Chrome's native host window while preserving the emulated CSS viewport. A plain overflow control showed that the previously undersized host clipped CDP gesture coordinates; production scrolling code was not changed.
- Mobile browser and both-site CCI/ACP smoke pass. Layout remains 8 rows at844×180, 18 at393×350 and20 at844×390.
- Local PTY/tmux regression passes. Simulated Android229 and Chinese composition events traverse the actual HTTP bridge and local shell; repeated-letter and Unicode marker output each appears exactly once.
- Full project suite: 501 passed, five existing WebSocket deprecation warnings. Independent review caught the composition-ending edit edge case; corrected traces pass.

No physical Android device was connected. DOM traces, CDP composition and local PTY checks do not replace the user's specific phone/IME validation. No real cloud task creation, command execution, stop or restart was used. Changes are served by the Mac; existing APK and pairing remain compatible.
