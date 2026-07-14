/**
 * Task Scheduler Frontend v2.0 — 闭环任务调度 + 实时坐标地图
 *
 * 连接 ws://host:8080/ws/scheduler
 * 新增功能：
 *   - 2D Canvas 实时地图：机器人位置、朝向、轨迹
 *   - action_event 处理：动作生命周期事件可视化
 *   - 坐标统计：实时 X/Y/Yaw/总里程
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
let autoScheduleEnabled = true;  // 与后端默认保持一致
let waitingQueue = [];

// ── 地图状态 ──
const mapState = {
  canvas: null,
  ctx: null,
  viewport: { x: 0, y: 0 },     // 世界坐标系中心
  scale: 40,                       // 像素/米
  autoFollow: true,                // 自动跟随机器人
  dragging: false,
  dragStart: { x: 0, y: 0 },
  viewportStart: { x: 0, y: 0 },
  robot: { x: 0, y: 0, yaw: 0 },
  trajectory: [],                  // 轨迹点 [{x, y, yaw, timestamp}]
  totalDistance: 0,
  targets: []                      // 目标点
};

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
  if (t === 'action_event') { handleActionEvent(data); }
  if (t === 'robot_position') { handlePositionUpdate(data); }
  if (t === 'position_reset') { resetMapState(); }
  if (t === 'queue_changed') { updateQueue(data.queue || []); }
  if (t === 'auto_schedule_changed') { setAutoScheduleUI(data.enabled); }
  if (t === 'scheduling_started') {
    if (data.has_task) {
      log('INFO', 'Scheduler', `开始调度，选中任务: ${data.next_task_id.slice(0, 12)}`);
    } else {
      log('WARN', 'Scheduler', '开始调度，但等待队列为空');
    }
  }
}

// ── 任务事件 ──
function handleTaskEvent(data) {
  const evt = data.event;

  // 队列状态变更（由 TaskManager._notify_queue_changed 产生）
  if (evt === 'queue_changed') {
    updateQueue(data.queue || []);
    return;
  }

  const tid = data.task_id;
  const task = tasks.find(t => t.id === tid);
  if (!task) return;
  if (evt === 'started') { task.status = 'running'; currentTask = task; }
  else if (evt === 'paused') task.status = 'paused';
  else if (evt === 'resumed') task.status = 'running';
  else if (evt === 'stopped') { task.status = 'cancelled'; currentTask = null; }
  else if (evt === 'completed') { task.status = 'completed'; currentTask = null; }
  else if (evt === 'failed') { task.status = 'failed'; currentTask = null; }
  else if (evt === 'preempted') { task.status = 'cancelled'; currentTask = null; }
  task.action_index = data.action_index || 0;
  renderTasks();
  renderCurrentTask();
  log('INFO', 'Task', `${task.name}: ${evt}`);
}

// ── 动作事件（闭环确认）──
function handleActionEvent(data) {
  const event = data.event;
  const actionType = data.action_type || '';
  const actionId = data.action_id;
  const progress = data.progress || 0;
  const detail = data.detail || '';
  const pos = data.position || {};

  // 日志
  const level = event === 'failed' ? 'ERROR' : event === 'completed' ? 'INFO' : 'DEBUG';
  log(level, 'Action', `[${actionType}#${actionId}] ${event} (${Math.round(progress*100)}%) ${detail}`);

  // 更新当前任务的动作状态
  if (currentTask && currentTask.actions) {
    const action = currentTask.actions.find(a => a.id === actionId);
    if (action) {
      action._status = event;
      action._progress = progress;
      action._detail = detail;
    }
    renderCurrentTask();
  }
}

// ── 坐标更新 ──
function handlePositionUpdate(data) {
  const pos = data.position || {};
  const yaw = data.yaw || 0;
  const traj = data.trajectory || [];

  mapState.robot.x = pos.x || 0;
  mapState.robot.y = pos.y || 0;
  mapState.robot.yaw = yaw;
  mapState.trajectory = traj;
  mapState.totalDistance = data.total_distance || 0;

  // 更新坐标显示
  updateCoordDisplay();

  // 自动跟随
  if (mapState.autoFollow) {
    mapState.viewport.x = mapState.robot.x;
    mapState.viewport.y = mapState.robot.y;
  }

  // 重绘地图
  drawMap();
}

function updateCoordDisplay() {
  const el = document.getElementById('coord-display');
  if (!el) return;
  const r = mapState.robot;
  el.textContent = `X: ${r.x.toFixed(2)} Y: ${r.y.toFixed(2)} | Yaw: ${Math.round(r.yaw)}° | 总里程: ${mapState.totalDistance.toFixed(2)}m`;
}

// ══════════════════════════════════════════════════════════════
// 地图绘制
// ══════════════════════════════════════════════════════════════

function initMap() {
  const canvas = document.getElementById('map-canvas');
  if (!canvas) return;
  mapState.canvas = canvas;
  mapState.ctx = canvas.getContext('2d');

  // 延迟一帧等 CSS 布局完成
  requestAnimationFrame(() => {
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    // 如果 CSS 还没生效，fallback 到 HTML 属性
    const displayW = rect.width > 0 ? rect.width : (canvas.width || 600);
    const displayH = rect.height > 0 ? rect.height : (canvas.height || 350);

    canvas.width = displayW * dpr;
    canvas.height = displayH * dpr;
    mapState.ctx.scale(dpr, dpr);
    mapState.canvasWidth = displayW;
    mapState.canvasHeight = displayH;

    // 事件绑定
    canvas.addEventListener('mousedown', onMapMouseDown);
    canvas.addEventListener('mousemove', onMapMouseMove);
    canvas.addEventListener('mouseup', onMapMouseUp);
    canvas.addEventListener('wheel', onMapWheel, { passive: false });
    canvas.addEventListener('dblclick', onMapDblClick);

    drawMap();
  });
}

// 世界坐标 → Canvas 坐标
function worldToCanvas(wx, wy) {
  const cx = (wx - mapState.viewport.x) * mapState.scale + mapState.canvasWidth / 2;
  const cy = mapState.canvasHeight / 2 - (wy - mapState.viewport.y) * mapState.scale;
  return { x: cx, y: cy };
}

// Canvas 坐标 → 世界坐标
function canvasToWorld(cx, cy) {
  const wx = (cx - mapState.canvasWidth / 2) / mapState.scale + mapState.viewport.x;
  const wy = mapState.viewport.y - (cy - mapState.canvasHeight / 2) / mapState.scale;
  return { x: wx, y: wy };
}

function drawMap() {
  const ctx = mapState.ctx;
  const w = mapState.canvasWidth;
  const h = mapState.canvasHeight;
  if (!ctx || !w || !h) return;

  ctx.save();
  // 清除
  ctx.fillStyle = '#0a0f1a';
  ctx.fillRect(0, 0, w, h);

  // 绘制网格
  drawGrid(ctx, w, h);

  // 绘制坐标轴
  drawAxes(ctx, w, h);

  // 绘制轨迹
  drawTrajectory(ctx);

  // 绘制目标点
  drawTargets(ctx);

  // 绘制机器人
  drawRobot(ctx);

  // 绘制信息
  drawMapInfo(ctx, w, h);

  ctx.restore();
}

function drawGrid(ctx, w, h) {
  ctx.strokeStyle = '#1a2332';
  ctx.lineWidth = 1;

  // 网格间距（根据缩放动态调整）
  let gridSize = 1.0; // 米
  if (mapState.scale > 80) gridSize = 0.5;
  if (mapState.scale > 160) gridSize = 0.25;
  if (mapState.scale < 20) gridSize = 2.0;
  if (mapState.scale < 10) gridSize = 5.0;

  const pixelSize = gridSize * mapState.scale;
  const offsetX = (mapState.canvasWidth / 2 - mapState.viewport.x * mapState.scale) % pixelSize;
  const offsetY = (mapState.canvasHeight / 2 + mapState.viewport.y * mapState.scale) % pixelSize;

  ctx.beginPath();
  for (let x = offsetX; x < w; x += pixelSize) {
    ctx.moveTo(x, 0); ctx.lineTo(x, h);
  }
  for (let y = offsetY; y < h; y += pixelSize) {
    ctx.moveTo(0, y); ctx.lineTo(w, y);
  }
  ctx.stroke();
}

function drawAxes(ctx, w, h) {
  const origin = worldToCanvas(0, 0);
  // 只有当原点在视野内才画
  if (origin.x < -50 || origin.x > w + 50 || origin.y < -50 || origin.y > h + 50) return;

  ctx.strokeStyle = '#334155';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(origin.x, 0); ctx.lineTo(origin.x, h);
  ctx.moveTo(0, origin.y); ctx.lineTo(w, origin.y);
  ctx.stroke();

  // 轴标签
  ctx.fillStyle = '#64748b';
  ctx.font = '10px monospace';
  ctx.fillText('O', origin.x + 4, origin.y - 4);
  ctx.fillText('X+', w - 20, origin.y - 4);
  ctx.fillText('Y+', origin.x + 4, 14);
}

function drawTrajectory(ctx) {
  const traj = mapState.trajectory;
  if (traj.length < 2) return;

  ctx.strokeStyle = '#8b5cf6';
  ctx.lineWidth = 2;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.beginPath();

  let started = false;
  for (const pt of traj) {
    const p = worldToCanvas(pt.x, pt.y);
    if (!started) {
      ctx.moveTo(p.x, p.y);
      started = true;
    } else {
      ctx.lineTo(p.x, p.y);
    }
  }
  ctx.stroke();

  // 轨迹点
  ctx.fillStyle = '#a78bfa';
  for (const pt of traj) {
    const p = worldToCanvas(pt.x, pt.y);
    ctx.beginPath();
    ctx.arc(p.x, p.y, 1.5, 0, Math.PI * 2);
    ctx.fill();
  }
}

function drawRobot(ctx) {
  const r = mapState.robot;
  const p = worldToCanvas(r.x, r.y);
  const yawRad = -r.yaw * Math.PI / 180; // Canvas Y 向下，取反校正

  // 绘制朝向箭头
  const arrowLen = 20;
  const arrowX = p.x + Math.cos(yawRad) * arrowLen;
  const arrowY = p.y + Math.sin(yawRad) * arrowLen;

  ctx.strokeStyle = '#22c55e';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(p.x, p.y);
  ctx.lineTo(arrowX, arrowY);
  ctx.stroke();

  // 箭头尖端
  const tipSize = 6;
  const tipAngle1 = yawRad + Math.PI * 0.8;
  const tipAngle2 = yawRad - Math.PI * 0.8;
  ctx.fillStyle = '#22c55e';
  ctx.beginPath();
  ctx.moveTo(arrowX, arrowY);
  ctx.lineTo(arrowX + Math.cos(tipAngle1) * tipSize, arrowY + Math.sin(tipAngle1) * tipSize);
  ctx.lineTo(arrowX + Math.cos(tipAngle2) * tipSize, arrowY + Math.sin(tipAngle2) * tipSize);
  ctx.closePath();
  ctx.fill();

  // 机器人本体（圆）
  ctx.fillStyle = '#22c55e';
  ctx.shadowColor = '#22c55e';
  ctx.shadowBlur = 10;
  ctx.beginPath();
  ctx.arc(p.x, p.y, 6, 0, Math.PI * 2);
  ctx.fill();
  ctx.shadowBlur = 0;

  // 中心白点
  ctx.fillStyle = '#fff';
  ctx.beginPath();
  ctx.arc(p.x, p.y, 2.5, 0, Math.PI * 2);
  ctx.fill();
}

function drawTargets(ctx) {
  for (const t of mapState.targets) {
    const p = worldToCanvas(t.x, t.y);
    ctx.strokeStyle = '#f59e0b';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(p.x, p.y, 8, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillStyle = '#f59e0b';
    ctx.beginPath();
    ctx.arc(p.x, p.y, 3, 0, Math.PI * 2);
    ctx.fill();
  }
}

function drawMapInfo(ctx, w, h) {
  // 缩放比例显示
  ctx.fillStyle = '#475569';
  ctx.font = '11px monospace';
  ctx.fillText(`Scale: ${mapState.scale.toFixed(1)} px/m`, 10, h - 10);
  ctx.fillText(`Follow: ${mapState.autoFollow ? 'ON' : 'OFF'}`, 10, h - 24);
}

// ── 地图交互 ──

function onMapMouseDown(e) {
  mapState.dragging = true;
  mapState.dragStart = { x: e.clientX, y: e.clientY };
  mapState.viewportStart = { ...mapState.viewport };
  mapState.autoFollow = false; // 手动拖动时关闭自动跟随
}

function onMapMouseMove(e) {
  if (!mapState.dragging) return;
  const dx = e.clientX - mapState.dragStart.x;
  const dy = e.clientY - mapState.dragStart.y;
  mapState.viewport.x = mapState.viewportStart.x - dx / mapState.scale;
  mapState.viewport.y = mapState.viewportStart.y + dy / mapState.scale;
  drawMap();
}

function onMapMouseUp() {
  mapState.dragging = false;
}

function onMapWheel(e) {
  e.preventDefault();
  const delta = e.deltaY > 0 ? 0.9 : 1.1;
  const newScale = Math.max(5, Math.min(300, mapState.scale * delta));

  // 以鼠标位置为中心缩放
  const rect = mapState.canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const my = e.clientY - rect.top;
  const worldBefore = canvasToWorld(mx, my);

  mapState.scale = newScale;

  const worldAfter = canvasToWorld(mx, my);
  mapState.viewport.x += worldBefore.x - worldAfter.x;
  mapState.viewport.y += worldBefore.y - worldAfter.y;

  drawMap();
}

function onMapDblClick() {
  mapState.autoFollow = true;
  mapState.viewport.x = mapState.robot.x;
  mapState.viewport.y = mapState.robot.y;
  drawMap();
}

function resetMap() {
  send({ type: 'reset_position' });
  resetMapState();
}

function resetMapState() {
  mapState.viewport = { x: 0, y: 0 };
  mapState.scale = 40;
  mapState.autoFollow = true;
  mapState.trajectory = [];
  mapState.totalDistance = 0;
  mapState.robot = { x: 0, y: 0, yaw: 0 };
  mapState.targets = [];
  updateCoordDisplay();
  drawMap();
  log('INFO', 'Map', '坐标已重置');
}

// ══════════════════════════════════════════════════════════════
// 任务列表渲染
// ══════════════════════════════════════════════════════════════

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

// ── 等待队列 ──
function updateQueue(queue) {
  waitingQueue = queue;
  renderQueue();
}

function renderQueue() {
  const list = document.getElementById('queue-list');
  const count = document.getElementById('queue-count');
  if (!list || !count) return;
  count.textContent = `${waitingQueue.length} 个`;
  if (!waitingQueue.length) {
    list.innerHTML = '<div class="queue-empty">等待队列为空</div>';
    return;
  }
  list.innerHTML = waitingQueue.map((t, i) => {
    const total = t.actions ? t.actions.length : 0;
    const created = t.created_at ? t.created_at.slice(0, 19).replace('T', ' ') : '-';
    return `<div class="queue-item">
      <div class="queue-rank ${i === 0 ? 'top' : ''}">${i + 1}</div>
      <div class="queue-info">
        <div class="queue-name">${t.name}</div>
        <div class="queue-meta">${total} 个动作 · ${created}</div>
      </div>
      <div class="queue-priority ${t.priority}">${t.priority}</div>
    </div>`;
  }).join('');
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

  // 计算更精细的进度（基于 action 的 progress）
  let finePct = pct;
  if (t.actions && idx < total) {
    const currentAction = t.actions[idx];
    const actionProgress = currentAction._progress || 0;
    finePct = ((idx + actionProgress) / total) * 100;
  }

  let stepsHtml = '';
  if (t.actions) {
    stepsHtml = '<div class="current-task-steps">' + t.actions.map((a, i) => {
      let cls = '';
      let statusText = '';
      if (i < idx) { cls = 'done'; statusText = '✓'; }
      else if (i === idx) {
        const s = a._status || 'running';
        if (s === 'failed') cls = 'failed';
        else cls = 'active';
        const prog = a._progress !== undefined ? Math.round(a._progress * 100) + '%' : '执行中';
        statusText = s === 'failed' ? '✗' : prog;
      }
      const typeLabel = actionTypeLabel(a.type || a.action_type || 'action');
      return `<div class="step-item ${cls}"><span class="step-num">${i+1}</span><span class="step-name">${typeLabel}</span><span class="step-status">${statusText}</span></div>`;
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
      <div class="progress-track"><div class="progress-bar" style="width:${finePct.toFixed(1)}%"></div></div>
      ${stepsHtml}
    </div>
  `;
}

function actionTypeLabel(type) {
  const labels = {
    walk_straight: '直线行走',
    turn_in_place: '原地转向',
    turn_walk: '转弯行走',
    walk_backward: '后退',
    sidestep: '侧移',
    stop: '停止',
    avoid_obstacle: '视觉避障'
  };
  return labels[type] || type;
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
  const autoStart = document.getElementById('auto-start-checkbox').checked;
  const rows = document.querySelectorAll('.action-item');
  const actions = [];
  rows.forEach(row => {
    const type = row.querySelector('.act-type').value;
    let params = {};
    try { params = JSON.parse(row.querySelector('.act-params').value || '{}'); } catch (e) {}
    actions.push({ type, device: '底盘', params });
  });
  if (!actions.length) { log('WARN', 'Form', '请至少添加一个动作'); return; }
  send({ type: 'create_task', name, priority, actions, auto_start: autoStart });
  const mode = autoStart ? '立即执行' : '进入等待队列';
  log('INFO', 'Form', `创建任务: ${name} (${mode})`);
}

function startScheduling() {
  send({ type: 'start_scheduling' });
  log('INFO', 'Scheduler', '手动触发调度');
}

function toggleAutoSchedule() {
  const next = !autoScheduleEnabled;
  send({ type: 'set_auto_schedule', enabled: next });
}

function setAutoScheduleUI(enabled) {
  autoScheduleEnabled = enabled;
  const badge = document.getElementById('auto-schedule-status');
  const btn = document.getElementById('btn-auto-schedule');
  const checkbox = document.getElementById('auto-start-checkbox');
  if (badge) {
    badge.className = `status-badge ${enabled ? 'auto-on' : 'auto-off'}`;
    badge.textContent = enabled ? '自动' : '手动';
  }
  if (btn) {
    btn.textContent = enabled ? '关闭自动调度' : '开启自动调度';
    btn.className = enabled ? 'btn-secondary' : 'btn-accent';
  }
  if (checkbox) checkbox.checked = enabled;
  log('INFO', 'Scheduler', `自动调度已${enabled ? '开启' : '关闭'}`);
}

function batchDemoTasks() {
  // 批量创建 3 个不同优先级的任务，关闭立即执行，进入等待队列
  const checkbox = document.getElementById('auto-start-checkbox');
  if (checkbox) checkbox.checked = false;

  const demos = [
    { name: '巡逻：客厅一圈', priority: 'LOW', actions: [
      { type: 'walk_straight', params: { distance: 2, speed: 0.6 } },
      { type: 'turn_in_place', params: { angle: 90, direction: 'left', speed: 0.3 } },
      { type: 'walk_straight', params: { distance: 2, speed: 0.6 } }
    ]},
    { name: '取物：厨房拿杯子', priority: 'NORMAL', actions: [
      { type: 'turn_in_place', params: { angle: 45, direction: 'right', speed: 0.3 } },
      { type: 'walk_straight', params: { distance: 1.5, speed: 0.8 } }
    ]},
    { name: '⚠️ 紧急避让 + 停止', priority: 'EMERGENCY', actions: [
      { type: 'avoid_obstacle', params: {} },
      { type: 'stop', params: { emergency: true } }
    ]}
  ];

  // 先关闭自动调度，避免第一个创建时立即执行
  if (autoScheduleEnabled) {
    send({ type: 'set_auto_schedule', enabled: false });
  }

  demos.forEach((d, i) => {
    setTimeout(() => {
      send({ type: 'create_task', name: d.name, priority: d.priority, actions: d.actions, auto_start: false });
    }, i * 200);
  });

  setTimeout(() => {
    log('INFO', 'Scheduler', '批量演示任务已创建，点击“开始调度”查看优先级排序');
  }, demos.length * 200);
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
  startClock(); connect(); refreshTasks(); initMap();
  setAutoScheduleUI(true);  // 初始状态与后端一致
});
