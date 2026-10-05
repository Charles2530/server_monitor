# Terminal visual polish

User requested a more attractive terminal and publication to GitHub, following the approved mobile layout and input repair.

Design: use a graphite terminal surface, restrained mint highlights, consistent line icons and low-contrast keycaps. Preserve the compact single-row controls and horizontal shortcut scrolling. Present mobile tools in a rounded sheet with distinct tools, connection and help sections. Keep at least five terminal rows at 844 × 180; extra spacing is allowed only when vertical space is available.

Implementation: update terminal-mobile.css, icon markup in terminal.html, and xterm presentation colors only. Retain input, transport and backend behavior. Reuse mobile/desktop browser regressions, inspect portrait/landscape/menu screenshots, then commit, redeploy with existing LaunchDaemon workflow, verify running assets and push local main to origin/main. No cloud test commands or credential changes.

Validation: mobile input/recovery/layout smoke and both-site CCI/ACP desktop smoke pass. Visual checks cover portrait, landscape and the expanded tools sheet. The 844 × 180 test still exposes five terminal rows, including visualViewport shrink while innerHeight remains 780. Independent review found no blockers. Publish includes the preceding mobile-terminal repair commits already deployed locally.
