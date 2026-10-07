# Mobile task workspace

The user accepted the proposed phone workflow: open on personal running tasks, work within one task, and retain context when returning from tools. Implement in an isolated checkout because the shared checkout contains concurrent task rename and visual work.

## Design
- Preserve all seven global navigation entries, including resource overview; opening overview starts at its own top, not the saved task list position.
- Running personal CCI cards open their terminal directly, while multi-instance/container targets still require explicit choice.
- On a fresh mobile root URL only, default to tasks. Explicit routes and desktop defaults are unchanged. Coarse-pointer landscape up to 1100px uses the same mobile detail/list behavior.
- Mobile task detail places existing monitor/log tabs and terminal action together below task identity. CCI editor is visibly labeled as desktop-only; the current mobile gateway does not support IDE origins. No fake editor navigation or gateway permission expansion.
- A validated, site-scoped session context remembers search, service filter, time range and tab. The URL carries selected task_id and task_view for reload, back/forward and return links. Deep links select only actual snapshot tasks. Missing tasks show a notice in the list rather than opening another task.
- Mobile terminal opens in the same page and returns to the exact task/tab. Existing desktop new-tab behavior stays intact. This preserves navigation context, not live shells across page destruction.
- Site changes discard task-specific URL parameters; same task IDs on different sites never inherit a tool target. Storage failure falls back to URL context.
- Existing stop/restart controls remain separated from the navigation row. Old APK-injected CSS must not cover or duplicate the new controls. No cloud writes in testing.

## Validation
Local fixture browser checks: bare mobile/desktop URL, explicit route, both sites, second task selection, logs/terminal/return, filter persistence, missing task, browser history, storage disabled, orientation, narrow/keyboard viewport, dark mode and duplicate APK injection. Existing mobile, terminal, CCI/ACP and navigation regressions remain required.
