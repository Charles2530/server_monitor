# Mobile terminal space optimization

User wants more usable terminal space on phones after the visual polish.

At a compact visible height of 500px or below, merge the existing top controls and bottom shortcut strip into one bottom row. Reserve a fixed area for keyboard, connection indicator and More; scroll shortcuts horizontally in the remaining width. The screen occupies the full upper area, without floating controls over terminal cells. Show the active target in the More sheet. Restore the usual two-bar layout when space is available or on desktop. Preserve font size, 36px key hit targets, composition handling and connection ownership.

Validation: raise actual-xterm regression thresholds to 18 rows at393×350, 20 at844×390 and8 at844×180; assert disjoint screen/shortcut/control bounds and visualViewport-only shrink with innerHeight780. Run mobile and existing desktop terminal smoke, inspect screenshots, review and update live static assets without restarting the monitor. Existing authorization to sync these terminal changes to GitHub applies.

Result: the old layout failed the new 393×350 minimum (15 vs18 rows). Updated layout passes with18,20,8 rows respectively, up from15,17,5; the smallest screen content area grows from100px to143px. Mobile/browser input and recovery checks, two-site CCI/ACP smoke, actual local PTY/tmux regression and independent review pass. No physical phone was connected; measurements are browser simulations.
