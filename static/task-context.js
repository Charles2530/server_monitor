"use strict";
// Navigation state only. Site identity always comes from the current document.
(() => {
  const mobile = matchMedia('(max-width:760px), (pointer:coarse) and (max-width:1100px)');
  const site = window.MonitorSite?.id || 'maoshanwang';
  const key = `ilab-task-context:${site}`;
  const taskId = value => typeof value === 'string' && /^(CCI|ACP):[^\r\n]{1,300}$/.test(value) ? value : null;
  function saved() {
    try {
      const value = JSON.parse(sessionStorage.getItem(key));
      if (value && Number.isFinite(value.savedAt) && Date.now() - value.savedAt >= 0 && Date.now() - value.savedAt < 8 * 3600000) return value;
    } catch { /* Private browsing can disable storage. URL navigation still works. */ }
    return {};
  }
  function read() {
    const value = mobile.matches ? saved() : {}, query = new URL(location.href).searchParams;
    return { taskId: taskId(query.get('task_id')), selected: taskId(value.selected),
      search: typeof value.search === 'string' ? value.search.slice(0,200) : '',
      kind: ['CCI','ACP'].includes(value.kind) ? value.kind : 'all',
      hours: [1,24,168].includes(value.hours) ? value.hours : 1,
      tab: query.get('task_view') === 'logs' ? 'logs' : 'monitor',
      scroll: Number.isFinite(value.scroll) ? Math.max(0,value.scroll) : 0 };
  }
  function remember(value) {
    if (!mobile.matches) return;
    try { sessionStorage.setItem(key, JSON.stringify({...saved(),...value,savedAt:Date.now()})); } catch { /* Optional UI persistence. */ }
  }
  function returnUrl(id, view) {
    const query = new URLSearchParams({site});
    if (taskId(id)) { query.set('task_id',id); query.set('task_view',view === 'logs' ? 'logs' : 'monitor'); }
    return `/?${query}#tasks`;
  }
  window.MonitorTaskContext = Object.freeze({mobile,read,remember,returnUrl});
})();
