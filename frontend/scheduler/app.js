/**
 * Task Scheduler Frontend — 连接 ws://host:8080/ws/scheduler
 * 支持：自然语言任务（2D SLAM）+ 动作任务
 */

const WS_URL = (() => {
  const h = window.location.hostname;
  const p = window.location.port || '8080';
  return `ws://${h}:${p}/ws/scheduler`;
})();

let ws = null;
let connected = false;
let tasks = [];
let currentTask = null;
let navActive = false;
let avoidanceEvents = [];

// ── 日志 ──
function log(level, source, msg) {
  const now = new Date();
  const ts = `${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}:${String(now.getSeconds()).padStart(2,'0')}`;
  const area = document.getElementById('log-area');
  if (!area) return;
  const div = document.createElement('div');
  div.className = `log-line log-${level}`;
  div.innerHTML = `<span class="log-time">${ts}</span><span class="log-level">${level}</span><span class="log-source">[${source}]</span> ${msg}`;
  area.appendChild(div);
  area.scrollTop = area.scrollHeight;
}

// ── 连接 ──
function connect() {
  try {
    ws = new WebSocket(WS_URL);
    ws.onopen = () => { connected = true; updateConn('online'); log('INFO', 'WS', '调度端连接成功'); };
    ws.onmessage = (e) => {
      try {
        if (typeof e.data !== 'string') { return; }
        const text = e.data.trim();
        if (!(text.startsWith('{') || text.startsWith('['))) { return; }
        handleMessage(JSON.parse(text));
      } catch (err) { /* ignore */ }
    };
    ws.onerror = () => { updateConn('offline'); };
    ws.onclose = () => { connected = false; updateConn('offline'); setTimeout(connect, 3000); };
  } catch (e) { updateConn('offline'); setTimeout(connect, 3000); }
}

function updateConn(state) {
  const dot = document.getElementById('conn-dot');
  const lbl = document.getElementById('conn-label');
  if (dot) dot.className = `conn-dot ${state}`;
  if (lbl) lbl.textContent = state === 'online' ? '已连接' : '已断开';
}

// ── 消息处理 ──
function handleMessage(data) {
  const t = data.type;

  // ── 原有消息类型 ──
  if (t === 'connected') return;
  if (t === 'task_list') { tasks = data.tasks || []; renderTasks(); }
  if (t === 'task_created') { tasks.push(data.task); renderTasks(); log('INFO', 'Scheduler', `任务创建: ${data.task.name}`); }
  if (t === 'task_event') { handleTaskEvent(data); }

  // ── NLP 解析结果 ──
  if (t === 'nlp_parsed') {
    handleNLPParsed(data);
  }

  // ── 导航任务摘要 ──
  if (t === 'nav_task_summary') {
    handleNavSummary(data);
  }

  // ── 导航位置更新 ──
  if (t === 'nav_position_update') {
    handleNavPositionUpdate(data);
  }

  // ── 避障事件 ──
  if (t === 'avoidance_event') {
    handleAvoidanceEvent(data);
  }

  // ── 导航任务完成 ──
  if (t === 'nav_task_completed') {
    handleNavCompleted(data);
  }

  // ── 导航任务取消 ──
  if (t === 'nav_task_cancelled') {
    navActive = false;
    document.getElementById('nav-status-area').innerHTML = '<div class="empty-state">导航任务已取消</div>';
    log('WARN', 'Nav', '导航任务已取消');
  }

  // ── 导航轨迹 ──
  if (t === 'nav_trajectory') {
    log('INFO', 'Nav', `轨迹点数: ${(data.trajectory||[]).length}, 避障: ${(data.avoidance_log||[]).length}次`);
  }
}

// ── NLP 任务处理 ──
function sendNLPTask() {
  const text = document.getElementById('nlp-input').value.trim();
  const currentLocation = document.getElementById('nlp-current-location').value;
  if (!text) { log('WARN', 'NLP', '请输入任务描述'); return; }
  send({ type: 'nlp_task', text, current_location: currentLocation });
  log('INFO', 'NLP', `下发自然语言任务: "${text}" (当前位置: ${currentLocation})`);
}

function handleNLPParsed(data) {
  document.getElementById('nlp-result').style.display = 'block';
  document.getElementById('nlp-task-name').textContent = '✅ ' + data.task_name;
  document.getElementById('nlp-start').textContent = `${data.start_location} (${data.start.x}, ${data.start.y})`;
  document.getElementById('nlp-target').textContent = `${data.target_location} (${data.target.x}, ${data.target.y})`;
  document.getElementById('nlp-yaw').textContent = data.initial_yaw + '°';
  navActive = true;

  // 更新导航状态区域
  document.getElementById('nav-status-area').innerHTML = `
    <div class="nav-active">
      <div class="nav-header">
        <span class="nav-name">${data.task_name}</span>
        <span class="status running">NAVIGATING</span>
      </div>
      <div class="nav-route">
        <span>${data.start_location} (${data.start.x}, ${data.start.y})</span>
        <span class="nav-arrow">→</span>
        <span>${data.target_location} (${data.target.x}, ${data.target.y})</span>
      </div>
      ${data.target_object ? `<div class="nav-object">🎯 目标物品: ${data.target_object}</div>` : ''}
    </div>
  `;

  log('INFO', 'NLP', `解析完成: ${data.task_name} | ${data.start_location}→${data.target_location}`);
}

// ── 导航位置更新 ──
function handleNavPositionUpdate(data) {
  document.getElementById('live-x').textContent = data.current_x.toFixed(2);
  document.getElementById('live-y').textContent = data.current_y.toFixed(2);
  document.getElementById('live-yaw').textContent = data.yaw.toFixed(1);
  document.getElementById('live-dist').textContent = data.distance_to_target.toFixed(2);
  const pct = (data.progress * 100).toFixed(0);
  document.getElementById('live-progress').textContent = pct;
  document.getElementById('nav-progress-bar').style.width = pct + '%';

  // 更新导航状态
  if (navActive) {
    const statusArea = document.getElementById('nav-status-area');
    const statusBadge = statusArea.querySelector('.status');
    if (statusBadge) {
      statusBadge.textContent = data.status === 'avoiding' ? 'AVOIDING' : 'NAVIGATING';
      statusBadge.className = 'status ' + (data.status === 'avoiding' ? 'avoiding' : 'running');
    }
  }
}

// ── 避障事件 ──
function handleAvoidanceEvent(data) {
  avoidanceEvents.push(data);
  const log = document.getElementById('avoidance-log');
  if (avoidanceEvents.length === 1) log.innerHTML = '';

  const div = document.createElement('div');
  div.className = 'avoidance-item';
  div.innerHTML = `
    <div class="avoid-header">⚠️ 避障 #${data.index || avoidanceEvents.length}</div>
    <div class="avoid-detail">
      <div>触发位置: (${data.trigger_x}, ${data.trigger_y})</div>
      <div>动作: 左转 ${data.turn_angle}° + 前进 ${data.forward_distance}m</div>
      <div>新位置: (${data.new_x}, ${data.new_y})</div>
      <div>剩余距离: ${data.remaining_distance}m</div>
    </div>
  `;
  log.insertBefore(div, log.firstChild);
  log('WARN', 'Avoidance', `避障 #${data.index || avoidanceEvents.length}: 左转${data.turn_angle}°+前进${data.forward_distance}m → 剩余${data.remaining_distance}m`);
}

// ── 导航完成 ──
function handleNavCompleted(data) {
  navActive = false;
  document.getElementById('nav-status-area').innerHTML = `
    <div class="nav-completed">
      <div class="nav-header">
        <span class="nav-name">✅ ${data.task_name}</span>
        <span class="status completed">COMPLETED</span>
      </div>
      <div class="nav-detail">
        <div>到达: ${data.target_location}</div>
        <div>总步数: ${data.total_steps}</div>
        <div>避障次数: ${data.avoidance_count}</div>
        <div>耗时: ${data.elapsed_seconds.toFixed(1)}s</div>
      </div>
    </div>
  `;
  document.getElementById('live-progress').textContent = '100';
  document.getElementById('nav-progress-bar').style.width = '100%';
  log('INFO', 'Nav', `✅ 任务完成: ${data.task_name} | 步数=${data.total_steps} 避障=${data.avoidance_count}次`);
}

// ── 导航操作 ──
function refreshNavSummary() { send({ type: 'get_nav_summary' }); }
function cancelNavTask() { send({ type: 'cancel_nav_task' }); navActive = false; }

// ── 任务事件 ──
function handleTaskEvent(data) {
  const evt = data.event;
  const tid = data.task_id;
  const task = tasks.find(t => t.id === tid);
  if (!task) return;
  if (evt === 'started') { task.status = 'running'; currentTask = task; }
  else if (evt === 'paused') task.status = 'paused';
  else if (evt === 'resumed') task.status = 'running';
  else if (evt === 'stopped') { task.status = 'cancelled'; currentTask = null; }
  else if (evt === 'completed') { task.status = 'completed'; currentTask = null; }
  else if (evt === 'failed') { task.status = 'failed'; currentTask = null; }
  task.action_index = data.action_index || 0;
  renderTasks();
  renderCurrentTask();
  log('INFO', 'Task', `${task.name}: ${evt}`);
}

function handleNavSummary(data) {
  if (!data.active) {
    navActive = false;
    document.getElementById('nav-status-area').innerHTML = '<div class="empty-state">暂无活跃导航任务</div>';
    return;
  }
  navActive = true;
  document.getElementById('nav-status-area').innerHTML = `
    <div class="nav-active">
      <div class="nav-header">
        <span class="nav-name">${data.task_name}</span>
        <span class="status ${data.status === 'navigating' ? 'running' : data.status}">${data.status.toUpperCase()}</span>
      </div>
      <div class="nav-route">
        <span>${data.start_location} (${data.start.x}, ${data.start.y})</span>
        <span class="nav-arrow">→</span>
        <span>${data.target_location} (${data.target.x}, ${data.target.y})</span>
      </div>
      <div class="nav-detail">
        <div>当前位置: (${data.current.x}, ${data.current.y})</div>
        <div>距终点: ${data.distance_to_target}m / 总距: ${data.total_distance}m</div>
        <div>进度: ${(data.progress*100).toFixed(0)}% | 步数: ${data.step_count}</div>
        <div>避障: ${data.avoidance_count}次 | 耗时: ${data.elapsed_seconds.toFixed(1)}s</div>
      </div>
    </div>
  `;
}

// ── 渲染任务列表 ──
function renderTasks() {
  const tbody = document.getElementById('task-tbody');
  if (!tasks.length) {
    tbody.innerHTML = '<tr class="empty"><td colspan="7">暂无任务</td></tr>';
    return;
  }
  tbody.innerHTML = tasks.map(t => {
    const statusClass = t.status || 'pending';
    const priorityClass = t.priority || 'NORMAL';
    const steps = `${t.action_index || 0} / ${t.actions ? t.actions.length : 0}`;
    const created = t.created_at ? t.created_at.slice(0, 19).replace('T', ' ') : '-';
    return `<tr>
      <td>${t.id.slice(0, 12)}</td>
      <td>${t.name}</td>
      <td><span class="status ${statusClass}">${statusClass.toUpperCase()}</span></td>
      <td><span class="priority ${priorityClass}">${priorityClass}</span></td>
      <td>${steps}</td>
      <td>${created}</td>
      <td><div class="ops">${renderOps(t)}</div></td>
    </tr>`;
  }).join('');
}

function renderOps(t) {
  const s = t.status;
  let btns = '';
  if (s === 'pending' || s === 'paused' || s === 'cancelled') btns += `<button class="btn-start" onclick="sendTaskCmd('start_task', '${t.id}')">启动</button>`;
  if (s === 'running') {
    btns += `<button class="btn-pause" onclick="sendTaskCmd('pause_task', '${t.id}')">暂停</button>`;
    btns += `<button class="btn-stop" onclick="sendTaskCmd('stop_task', '${t.id}')">停止</button>`;
  }
  if (s === 'paused') {
    btns += `<button class="btn-resume" onclick="sendTaskCmd('resume_task', '${t.id}')">继续</button>`;
    btns += `<button class="btn-stop" onclick="sendTaskCmd('stop_task', '${t.id}')">停止</button>`;
  }
  return btns;
}

function renderCurrentTask() {
  const el = document.getElementById('current-task-info');
  if (!currentTask) {
    el.innerHTML = '<div class="empty-state">暂无运行中的任务</div>';
    return;
  }
  const t = currentTask;
  const total = t.actions ? t.actions.length : 0;
  const idx = t.action_index || 0;
  const pct = total > 0 ? (idx / total) * 100 : 0;
  let stepsHtml = '';
  if (t.actions) {
    stepsHtml = '<div class="current-task-steps">' + t.actions.map((a, i) => {
      let cls = '';
      if (i < idx) cls = 'done';
      else if (i === idx) cls = 'active';
      return `<div class="step-item ${cls}"><span class="step-num">${i+1}</span><span class="step-name">${a.type || a.action || 'action'}</span></div>`;
    }).join('') + '</div>';
  }
  el.innerHTML = `
    <div class="current-task">
      <div class="current-task-name">${t.name} <span class="status ${t.status}">${t.status}</span></div>
      <div class="current-task-meta">
        <span>ID: ${t.id.slice(0, 12)}</span>
        <span>优先级: ${t.priority}</span>
        <span>步骤: ${idx} / ${total}</span>
      </div>
      <div class="progress-track"><div class="progress-bar" style="width:${pct}%"></div></div>
      ${stepsHtml}
    </div>
  `;
}

// ── 操作 ──
function addActionRow() {
  const list = document.getElementById('action-list');
  const div = document.createElement('div');
  div.className = 'action-item';
  div.innerHTML = `<select class="act-type">
    <option value="walk_straight">直线行走</option><option value="turn_in_place">原地掉头</option>
    <option value="turn_walk">转弯行走</option><option value="walk_backward">后退</option>
    <option value="sidestep">侧移</option><option value="stop">停止</option>
    <option value="avoid_obstacle">视觉避障</option>
  </select><input type="text" class="act-params" placeholder='{"distance":2,"speed":0.8}' value='{"distance":2,"speed":0.8}' />
  <button class="btn-remove" onclick="this.parentElement.remove()">×</button>`;
  list.appendChild(div);
}

function createTask() {
  const name = document.getElementById('task-name-input').value.trim() || '未命名任务';
  const priority = document.getElementById('task-priority').value;
  const rows = document.querySelectorAll('.action-item');
  const actions = [];
  rows.forEach(row => {
    const type = row.querySelector('.act-type').value;
    let params = {};
    try { params = JSON.parse(row.querySelector('.act-params').value || '{}'); } catch (e) {}
    actions.push({ type, device: '底盘', params });
  });
  if (!actions.length) { log('WARN', 'Form', '请至少添加一个动作'); return; }
  send({ type: 'create_task', name, priority, actions });
  log('INFO', 'Form', `创建任务: ${name}`);
}

function quickTask(name, actions) {
  document.getElementById('task-name-input').value = name;
  const list = document.getElementById('action-list');
  list.innerHTML = '';
  actions.forEach(a => {
    const div = document.createElement('div');
    div.className = 'action-item';
    div.innerHTML = `<select class="act-type"><option value="${a.type}">${a.type}</option></select>
    <input type="text" class="act-params" value='${JSON.stringify(a.params)}' />
    <button class="btn-remove" onclick="this.parentElement.remove()">×</button>`;
    list.appendChild(div);
  });
  createTask();
}

function sendTaskCmd(type, taskId) {
  send({ type, task_id: taskId });
}

function refreshTasks() {
  send({ type: 'get_tasks' });
}

function clearCompleted() {
  tasks = tasks.filter(t => t.status !== 'completed');
  renderTasks();
}

function send(msg) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg));
  else log('ERROR', 'WS', '未连接');
}

// ── 时钟 ──
function startClock() {
  const tick = () => {
    const n = new Date();
    document.getElementById('clock').textContent = `${String(n.getHours()).padStart(2,'0')}:${String(n.getMinutes()).padStart(2,'0')}:${String(n.getSeconds()).padStart(2,'0')}`;
  };
  tick(); setInterval(tick, 1000);
}

// ── 启动 ──
document.addEventListener('DOMContentLoaded', () => {
  startClock(); connect(); refreshTasks();
});
