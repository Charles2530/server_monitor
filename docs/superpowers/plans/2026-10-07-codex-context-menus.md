# Codex context menus

Goal: Add files, folders and selected code to the current Codex chat from VS Code context menus.

Design: Reuse the installed Codex commands `chatgpt.addFileToThread` (URI, including directory path references) and `chatgpt.addToThread` (active selection line range). Add only the missing Explorer menu to the existing declarative iLab workspace extension, supporting file and remote URI schemes. Keep Codex's existing editor selection and tab menus: code-server already makes them available for remote files, and adding copies was observed to duplicate them. No directory traversal or automatic message submission. Keep appearance defaults unchanged.

Implementation/validation:
- Add bundle regression assertions for version agreement, Explorer file/folder menu availability and absence of duplicate editor/tab menus; observe failure before changes.
- Bump the bundled extension to 1.3.1 and package metadata.
- Run bundle tests and full suite.
- Install the bundle in the current CCI editor, check path references and selected-code line references without sending a chat message.
- Sync the changed runtime bundle, restart the existing monitor service and push only related files to GitHub.

Validation results:
- Full Python suite: 858 passed; five existing websocket deprecation warnings.
- Live container: appearance bundle 1.3.1 installation confirmed.
- Browser: editor context menu contains one Add to Codex Thread action; selecting README content adds a line-1 reference to the empty Codex composer. Test reference removed; no message sent.
- Explorer right-click could not be opened by the supported browser automation, so file/folder Explorer end-to-end interaction remains unverified. Menu packaging and directory eligibility are covered by the regression test; the installed native handler forwards directory paths without reading contents.
- Installation uncovered an empty code-server extensions/.obsolete file. Treat empty/whitespace contents as an empty map to avoid false installation failures and repeated installs. Added failing-then-passing idempotency cases.
- Runtime files synchronized and service gracefully restarted; homepage returns HTTP 200.
