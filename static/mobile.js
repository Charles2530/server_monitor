"use strict";

/* Works both as a normal page script and as an Android-bundled enhancement. */
(() => {
  if (window.ilabMobileReady || !document.querySelector('.sidebar') || !document.getElementById('main')) return;
  window.ilabMobileReady = true;
  const context = window.MonitorTaskContext;
  const phone = context?.mobile || window.matchMedia('(max-width: 760px)');
  const tasks = document.getElementById('tasks-view');
  const detail = document.getElementById('detail-panel');
  const list = document.getElementById('task-list');
  const back = document.createElement('button');
  back.id = 'mobile-task-back'; back.type = 'button';
  back.className = 'button button-secondary mobile-task-back';
  back.textContent = '‹ 返回任务列表';
  if (detail) detail.prepend(back);

  function labelRows(table) {
    const labels = Array.from(table.querySelectorAll('thead th'), cell => cell.textContent.trim().replace(/\s+/g, ' '));
    for (const row of table.querySelectorAll('tbody tr')) {
      if (row.cells.length !== labels.length) continue;
      Array.from(row.cells).forEach((cell, index) => {
        if (cell.dataset.label !== labels[index]) cell.dataset.label = labels[index];
      });
    }
  }
  for (const table of document.querySelectorAll('.resource-table')) {
    labelRows(table);
    const body = table.querySelector('tbody');
    if (body) new MutationObserver(() => labelRows(table)).observe(body, { childList: true });
  }

  let selected = null, snapshot = null;
  const tabs = document.querySelector('.detail-tabs'), terminal = document.getElementById('cci-terminal-selected');
  const tabsMarker = document.createComment('task-tabs'), terminalMarker = document.createComment('task-terminal');
  tabs.before(tabsMarker); terminal.before(terminalMarker);
  const tools = document.createElement('div'); tools.id = 'mobile-task-tools'; tools.hidden = true;
  const editor = document.createElement('span'); editor.id = 'mobile-editor-note'; editor.textContent = '编辑器 · 电脑';
  editor.title = 'VS Code 编辑器请在电脑上打开此任务';
  detail.insertBefore(tools, tabs);
  const notice = document.createElement('p'); notice.id = 'mobile-task-notice'; notice.hidden = true; notice.setAttribute('role','status');
  tasks.prepend(notice);
  const tab = () => document.getElementById('detail-tab-logs').getAttribute('aria-selected') === 'true' ? 'logs' : 'monitor';
  function resizeCharts() { window.dispatchEvent(new Event('resize')); }
  function locationState(id, view, push = false) {
    if (!context) return;
    const url = new URL(location.href);
    if (id) { url.searchParams.set('task_id',id); url.searchParams.set('task_view',view); url.hash = 'tasks'; }
    else { url.searchParams.delete('task_id'); url.searchParams.delete('task_view'); }
    if (url.href !== location.href) history[push ? 'pushState' : 'replaceState']({...history.state,mobileTask:push || !!history.state?.mobileTask},'',url);
  }
  function toolLinks() {
    if (!phone.matches || !selected || terminal.hidden) return;
    const query = new URLSearchParams({site:window.MonitorSite?.id || 'maoshanwang',task_id:selected.id,task_view:tab()});
    terminal.href = `/terminal?${query}`; terminal.target = '_self';
    editor.hidden = selected.kind !== 'CCI';
  }
  function arrangeTools() {
    tools.hidden = !phone.matches;
    if (phone.matches) { tools.append(tabs,terminal,editor); }
    else { tabsMarker.after(tabs); terminalMarker.after(terminal); terminal.target = '_blank'; }
    toolLinks();
  }
  function showDetail(push = false, view = null) {
    if (!phone.matches || !selected || detail.hidden) return;
    if (push) context?.remember({scroll:window.scrollY});
    notice.hidden = true;
    tasks.classList.add('mobile-show-detail');
    if (view) document.getElementById(`detail-tab-${view}`).click();
    locationState(selected.id,tab(),push);
    toolLinks();
    document.dispatchEvent(new CustomEvent('monitor-detail-visibility'));
    requestAnimationFrame(() => {
      detail.scrollIntoView({ block: 'start', behavior: 'instant' });
      back.focus({ preventScroll: true }); resizeCharts();
    });
  }
  function showList(update = true) {
    tasks.classList.remove('mobile-show-detail');
    if (update) locationState(null);
    document.dispatchEvent(new CustomEvent('monitor-detail-visibility'));
    if (phone.matches) document.getElementById('detail-tab-monitor').click();
    if (phone.matches && location.hash === '#tasks') {
      requestAnimationFrame(() => {
        window.scrollTo({top:context?.read().scroll || 0,behavior:'instant'});
        list.querySelector('.task-card.selected')?.focus({preventScroll:true});
      });
    }
  }
  function restoreLocation() {
    if (!phone.matches || location.hash !== '#tasks' || !context) return;
    const state = context.read();
    if (!state.taskId) { if (tasks.classList.contains('mobile-show-detail')) showList(false); return; }
    if (snapshot?.tasks?.some(task => task.id === state.taskId)) {
      document.dispatchEvent(new CustomEvent('monitor-task-request',{detail:state.taskId}));
      showDetail(false,state.tab);
    } else if (snapshot) {
      const known = snapshot.connected && snapshot.source_status?.[state.taskId.split(':')[0]]?.status === 'ok';
      showList(known);
      notice.textContent = known ? '这个任务已不在运行列表中，请从当前任务中重新选择。' : '暂时无法确认这个任务的状态，采集恢复后会自动重试。';
      notice.hidden = false;
    }
  }
  back.addEventListener('click', () => {
    if (context && history.state?.mobileTask) history.back(); else showList();
  });
  document.addEventListener('click', event => {
    if (event.target.closest('.task-card, .overview-task')) requestAnimationFrame(() => showDetail(true));
    if (event.target.closest('.nav-item[href="#tasks"]')) showList();
  });
  terminal.addEventListener('click', toolLinks);
  document.addEventListener('monitor-selection', event => {
    selected = event.detail; toolLinks();
    if (!selected) tasks.classList.remove('mobile-show-detail');
  });
  document.addEventListener('monitor-detail-tab', () => {
    if (phone.matches && selected && tasks.classList.contains('mobile-show-detail')) locationState(selected.id,tab());
    toolLinks();
  });
  document.addEventListener('monitor-snapshot', event => {
    snapshot = event.detail.snapshot;
    // Poll renders its selection after this event. Restore only once that render finishes.
    if (phone.matches && context?.read().taskId) setTimeout(() => {
      const state = context.read();
      if (!tasks.classList.contains('mobile-show-detail') || !snapshot?.tasks?.some(task => task.id === state.taskId)) restoreLocation();
    },0);
  });
  window.addEventListener('popstate',restoreLocation);
  window.addEventListener('hashchange', () => {
    if (location.hash !== '#tasks') showList();
  });
  phone.addEventListener('change', () => {
    arrangeTools();
    if (!phone.matches) tasks.classList.remove('mobile-show-detail');
    else restoreLocation();
    document.dispatchEvent(new CustomEvent('monitor-detail-visibility')); resizeCharts();
  });
  arrangeTools();
})();
