"use strict";
(() => {
  const hosts = [...document.querySelectorAll('[data-connection-log]')];
  if (!hosts.length) return;
  const labels = { checking: '检查中', loading: '连接中', ready: '已连接', reconnecting: '重连中', offline: '离线', error: '错误', idle: '待连接' };
  // Only fixed diagnostic copy reaches the log. Raw server errors can contain
  // credentials, URLs, commands or terminal output, so never retain those strings.
  const messages = [
    '本机监控请求连接失败。', '正在检查连接。', '正在建立 WebSocket 连接。', 'WebSocket 握手成功。',
    'WebSocket 暂不可用，切换兼容连接。', '已使用兼容连接。',
    'SSH 预连接失败，正在重试 SSH。', 'SSH 连接失败，正在切换云端通道。',
    '已通过 SSH 连接。', '已通过云端通道连接。',
    '正在恢复原 Shell 连接。', '原 Shell 连接已恢复。', '重连尝试未成功。',
    '用户请求重新连接。', '用户已断开终端。', '终端会话已结束。',
    '网络已离线，等待网络恢复。', '网络已恢复，正在检查连接。',
    '页面已恢复，正在检查连接。', '正在读取运行实例与容器。',
    '请选择要连接的实例与容器。', '正在建立终端连接。', '终端已连接。',
    '编辑器已就绪。', '正在准备编辑器。', '正在恢复编辑器连接。',
    '正在恢复编辑器认证。', '正在重新建立编辑器会话。', '编辑器页面未响应，正在恢复连接。',
    '正在探测后台连接状态。', '正在确认编辑器页面响应。', '正在等待自动重试。',
    '连接暂不可用，将自动重试。', '连接超时，请重试。', '会话已过期，请重新连接。',
    '认证尚未完成，正在恢复连接。', '当前没有可连接的运行容器。',
    'Codex 面板加载中。', 'Codex 面板已加载。', 'Codex 面板已隐藏。',
    '正在识别 Codex 当前项目历史。', 'Codex 项目历史已读取，等待列表过滤确认。',
    'Codex 当前项目聊天过滤完成。', 'Codex 当前项目暂无匹配聊天。',
    'Codex 历史列表加载中，等待过滤。', 'Codex 项目聊天过滤暂不可用，已保留原生列表。',
    'Codex 项目历史读取失败，已保留原生列表。',
  ];
  const allowed = new Set(messages);
  function safeMessage(message, state) {
    const text = typeof message === 'string' ? message : '';
    if (allowed.has(text)) return text;
    if (/^API (targets|open|status|recover|details|history) · HTTP [1-5][0-9]{2} · [0-9]{1,6}ms$/.test(text)) return text;
    if (/^WebSocket 已关闭（代码 [0-9]{4}）。$/.test(text)) return text;
    if (/^正在建立终端连接/.test(text)) return '正在建立终端连接。';
    if (/离线/.test(text)) return '网络已离线，等待网络恢复。';
    if (/超时/.test(text)) return '连接超时，请重试。';
    if (/会话.*过期|终端已结束或过期/.test(text)) return '会话已过期，请重新连接。';
    if (/没有可连接/.test(text)) return '当前没有可连接的运行容器。';
    if (/页面.*(?:无响应|未响应)|工作台.*(?:无响应|未响应)/.test(text)) return '编辑器页面未响应，正在恢复连接。';
    if (/重新建立.*(?:会话|连接)|完整重建|自动重建/.test(text)) return '正在重新建立编辑器会话。';
    if (/恢复.*认证/.test(text)) return '正在恢复编辑器认证。';
    if (/后台.*(?:状态|探测)|探测.*后台/.test(text)) return '正在探测后台连接状态。';
    if (/确认.*(?:页面|工作台).*响应/.test(text)) return '正在确认编辑器页面响应。';
    if (/退避|等待.*重试/.test(text)) return '正在等待自动重试。';
    if (/自动重试|自动修复|无法恢复/.test(text)) return '连接暂不可用，将自动重试。';
    if (/原 Shell.*恢复|终端连接已恢复/.test(text) && state === 'ready') return '原 Shell 连接已恢复。';
    if (/原 Shell/.test(text)) return '正在恢复原 Shell 连接。';
    if (state !== 'ready' && /恢复.*编辑器|编辑器.*恢复|自动重建/.test(text)) return '正在恢复编辑器连接。';
    if (/读取.*(?:实例|容器|Worker)/.test(text)) return '正在读取运行实例与容器。';
    if (/选择.*(?:实例|容器|Worker)|已找到运行容器/.test(text)) return '请选择要连接的实例与容器。';
    if (/终端已连接/.test(text)) return '终端已连接。';
    if (/断开终端/.test(text)) return state === 'idle' ? '等待建立连接。' : '用户已断开终端。';
    if (/终端会话已结束/.test(text)) return '终端会话已结束。';
    if (state === 'ready') return '编辑器已就绪。';
    if (/下载/.test(text)) return '正在下载编辑器组件。';
    if (/安装/.test(text)) return '正在安装编辑器组件。';
    if (/认证|登录|续登/.test(text)) return '认证尚未完成，正在恢复连接。';
    if (state === 'error') return '连接出现错误，请查看页面提示并重试。';
    if (state === 'offline') return '连接已断开。';
    return state === 'idle' ? '等待建立连接。' : '正在准备连接，请稍候。';
  }
  function normalize(state, message) {
    if (state === 'offline' || /网络已离线/.test(message || '')) return 'offline';
    if (state === 'loading' && /恢复|重连|重试|重建|自动修复/.test(message || '')) return 'reconnecting';
    return Object.hasOwn(labels, state) ? state : 'loading';
  }
  const dialog = document.createElement('dialog');
  dialog.id = 'connection-log-dialog';
  dialog.setAttribute('aria-labelledby', 'connection-log-title');
  dialog.innerHTML = '<div class="connection-log-heading"><h2 id="connection-log-title">连接日志</h2><button id="connection-log-close" type="button" aria-label="关闭连接日志">关闭</button></div><p class="connection-log-overview"><span id="connection-log-state" role="status" aria-live="polite"></span><span>最后更新 <time id="connection-log-updated"></time></span></p><p class="connection-log-privacy">仅保存本页最近 200 条连接事件，刷新后清空。不记录终端内容、命令或聊天。</p><ol id="connection-log-list" aria-label="连接事件" tabindex="0"></ol>';
  document.body.append(dialog);
  const list = dialog.querySelector('#connection-log-list');
  let current = 'idle', lastEntry = '', scrollFrame = null, opener = null;
  const controls = hosts.map(host => {
    host.classList.add('connection-monitor');
    host.innerHTML = '<button class="connection-log-button" type="button" aria-haspopup="dialog" aria-controls="connection-log-dialog">连接日志</button><span class="connection-current-state" role="status" aria-live="polite"></span><time class="connection-updated" aria-label="最后更新时间"></time>';
    const button = host.querySelector('button');
    button.setAttribute('aria-label', '连接日志');
    button.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><rect x="4" y="3" width="16" height="18" rx="3"/><path d="M8 8h8M8 12h8M8 16h5"/></svg>';
    button.addEventListener('click', () => { opener = button; dialog.showModal(); scrollToLatest(); });
    return { host, label: host.querySelector('.connection-current-state'), time: host.querySelector('time') };
  });
  dialog.querySelector('#connection-log-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => opener?.focus());
  function scrollToLatest() {
    if (!dialog.open || scrollFrame !== null) return;
    scrollFrame = requestAnimationFrame(() => { scrollFrame = null; list.scrollTop = list.scrollHeight; });
  }
  function timestamp() {
    const date = new Date();
    return { iso: date.toISOString(), display: date.toLocaleTimeString('zh-CN', { hour12: false }) };
  }
  function append(message, state, time) {
    const row = document.createElement('li');
    row.dataset.state = state;
    const date = document.createElement('time'), badge = document.createElement('span'), text = document.createElement('span');
    date.dateTime = time.iso; date.textContent = time.display;
    badge.className = 'connection-event-state'; badge.textContent = labels[state];
    text.textContent = message;
    row.append(date, badge, text); list.append(row);
    while (list.children.length > 200) list.firstElementChild.remove();
    scrollToLatest();
  }
  function update(state, message) {
    current = normalize(state, message);
    const text = safeMessage(message, current), time = timestamp();
    controls.forEach(({ host, label, time: updated }) => {
      host.dataset.state = current; label.textContent = labels[current];
      host.title = `${labels[current]} · 最后更新 ${time.display} · 点击查看连接日志`;
      updated.dateTime = time.iso; updated.textContent = time.display;
      updated.title = `最后更新 ${time.display}`;
    });
    dialog.dataset.state = current;
    dialog.querySelector('#connection-log-state').textContent = labels[current];
    const updated = dialog.querySelector('#connection-log-updated');
    updated.dateTime = time.iso; updated.textContent = time.display;
    // Repeated heartbeats refresh the timestamp without growing the event list.
    const key = current + ':' + text;
    if (key !== lastEntry) { append(text, current, time); lastEntry = key; }
  }
  function note(message, state = current) {
    const normalized = normalize(state, message);
    append(safeMessage(message, normalized), normalized, timestamp());
  }
  // The dialog must remain inside the fullscreen subtree to be visible there.
  document.addEventListener('fullscreenchange', () => (document.fullscreenElement || document.body).append(dialog));
  window.MonitorConnectionLog = Object.freeze({ update, note });
  update('idle', '等待建立连接。');
})();
