/**
 * Task Scheduler Frontend — 连接 ws://host:8080/ws/scheduler
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
    ws.onmessage = (e) => { try { handleMessage(JSON.parse(e.data)); } catch (err) {} };
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
  if (t === 'connected') return;
  if (t === 'task_list') { tasks = data.tasks || []; renderTasks(); }
  if (t === 'task_created') { tasks.push(data.task); renderTasks(); log('INFO', 'Scheduler', `任务创建: ${data.task.name}`); }
  if (t === 'task_event') { handleTaskEvent(data); }
}

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
