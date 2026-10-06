"use strict";

/* Works both as a normal page script and as an Android-bundled enhancement. */
(() => {
  if (window.ilabMobileReady || !document.querySelector('.sidebar') || !document.getElementById('main')) return;
  window.ilabMobileReady = true;
  const phone = window.matchMedia('(max-width: 760px)');
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

  function resizeCharts() { window.dispatchEvent(new Event('resize')); }
  function showDetail() {
    if (!phone.matches || !tasks || !detail || detail.hidden) return;
    tasks.classList.add('mobile-show-detail');
    document.dispatchEvent(new CustomEvent('monitor-detail-visibility'));
    requestAnimationFrame(() => {
      detail.scrollIntoView({ block: 'start', behavior: 'instant' });
      back.focus({ preventScroll: true }); resizeCharts();
    });
  }
  function showList() {
    tasks?.classList.remove('mobile-show-detail');
    document.dispatchEvent(new CustomEvent('monitor-detail-visibility'));
    if (phone.matches) {
      // Existing tab handler cancels outstanding log reads and the refresh timer.
      document.getElementById('detail-tab-monitor')?.click();
    }
    if (phone.matches) {
      document.getElementById('tasks-section')?.scrollIntoView({ block: 'start', behavior: 'instant' });
      list?.querySelector('.task-card.selected')?.focus({ preventScroll: true });
    }
  }
  back.addEventListener('click', showList);
  // Bubble after the existing task selection handler has populated the detail.
  document.addEventListener('click', event => {
    if (event.target.closest('.task-card, .overview-task')) requestAnimationFrame(showDetail);
    if (event.target.closest('.nav-item[href="#tasks"]')) showList();
  });
  document.addEventListener('monitor-selection', event => {
    if (!event.detail) tasks?.classList.remove('mobile-show-detail');
  });
  window.addEventListener('hashchange', () => {
    if (location.hash !== '#tasks') {
      if (phone.matches) document.getElementById('detail-tab-monitor')?.click();
      tasks?.classList.remove('mobile-show-detail');
    }
  });
  phone.addEventListener('change', () => {
    if (!phone.matches) tasks?.classList.remove('mobile-show-detail');
    else if (!tasks?.classList.contains('mobile-show-detail')) showList();
    document.dispatchEvent(new CustomEvent('monitor-detail-visibility'));
    resizeCharts();
  });
})();
