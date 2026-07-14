/**
 * Robot Control Frontend — 连接后端 ws://host:8080/ws/control
 */

const WS_URL = (() => {
  const h = window.location.hostname;
  const p = window.location.port || '8080';
  return `ws://${h}:${p}/ws/control`;
})();

let ws = null;
let connected = false;
let usingMock = false;
let reconnectTimer = null;
let heartbeatTimer = null;
let seq = 0;

// ── 状态 ──
const robot = {
  state: 'idle', battery: 87, cpu: 42, speed: 0, dist: 0,
  roll: 0, pitch: 0, yaw: 0,
  obstacle: null, joints: {}
};
const history = { speed: new Array(30).fill(0), battery: new Array(30).fill(87) };

// ── 日志 ──
const MAX_LOGS = 500;
let logs = [];
let filterLevel = 'ALL';

function log(level, source, msg) {
  const now = new Date();
  const ts = `${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}:${String(now.getSeconds()).padStart(2,'0')}.${String(now.getMilliseconds()).padStart(3,'0')}`;
  const entry = { level, source, msg, ts };
  logs.push(entry);
  if (logs.length > MAX_LOGS) logs.shift();
  appendLog(entry);
}

function appendLog(entry) {
  if (filterLevel !== 'ALL' && entry.level !== filterLevel) return;
  const area = document.getElementById('log-area');
  if (!area) return;
  const div = document.createElement('div');
  div.className = `log-line log-${entry.level}`;
  div.innerHTML = `<span class="log-time">${entry.ts}</span><span class="log-level">${entry.level}</span><span class="log-source">[${entry.source}]</span> ${entry.msg}`;
  area.appendChild(div);
  while (area.children.length > MAX_LOGS) area.removeChild(area.firstChild);
  area.scrollTop = area.scrollHeight;
}

function clearLog() {
  logs = [];
  document.getElementById('log-area').innerHTML = '';
}

function exportLog() {
  const text = logs.map(e => `[${e.ts}] [${e.level}] [${e.source}] ${e.msg}`).join('\n');
  const blob = new Blob([text], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = `robot_log_${new Date().toISOString().slice(0,19).replace(/:/g,'-')}.txt`; a.click();
  URL.revokeObjectURL(url);
}

function setFilter(level) {
  filterLevel = level;
  document.querySelectorAll('.log-filters .filter').forEach(b => b.classList.toggle('active', b.textContent === level || (level === 'ALL' && b.textContent === '全部')));
  const area = document.getElementById('log-area');
  area.innerHTML = '';
  const filtered = level === 'ALL' ? logs : logs.filter(e => e.level === level);
  filtered.forEach(appendLog);
}

// ── 连接 ──
function connect() {
  updateConn('connecting');
  try {
    ws = new WebSocket(WS_URL);
    ws.onopen = () => {
      connected = true; usingMock = false;
      updateConn('online');
      log('INFO', 'WS', `连接成功: ${WS_URL}`);
      heartbeatTimer = setInterval(() => { if (ws && ws.readyState === 1) ws.send(JSON.stringify({type:'heartbeat', seq:++seq, ts:Date.now()})); }, 5000);
      // 拉取任务列表（与调度面板同步）
      ws.send(JSON.stringify({type: 'get_tasks'}));
    };
    ws.onmessage = (e) => {
      let data;
      try {
        data = JSON.parse(e.data);
      } catch (err) {
        const preview = String(e.data).slice(0, 200);
        log('WARN', 'WS', `JSON 解析失败: ${err.message} | 预览: ${preview}`);
        return;
      }
      try {
        handleMessage(data);
      } catch (err) {
        log('ERROR', 'WS', `消息处理异常 type=${data && data.type}: ${err.message}`);
      }
    };
    ws.onerror = () => { log('WARN', 'WS', '连接错误'); startMock(); };
    ws.onclose = () => {
      connected = false; clearInterval(heartbeatTimer); updateConn('offline');
      if (!usingMock) reconnectTimer = setTimeout(connect, 3000);
    };
  } catch (e) { startMock(); }
}

function updateConn(state) {
  const dot = document.getElementById('conn-dot');
  const lbl = document.getElementById('conn-label');
  dot.className = `conn-dot ${state}`;
  lbl.textContent = state === 'online' ? '已连接' : state === 'connecting' ? '连接中...' : '已断开';
}

function startMock() {
  if (usingMock) return;
  usingMock = true; updateConn('offline');
  log('INFO', 'MOCK', '已启动模拟模式');
}

// ── 消息处理 ──
function handleMessage(data) {
  const t = data.type;
  if (t === 'status') updateStatus(data.status || data);
  else if (t === 'task_event') updateTask(data);
  else if (t === 'task_list') updateTaskList(data);
  else if (t === 'task_created') log('INFO', 'Task', `新任务创建: ${data.task?.name || data.task?.id || '?'}`);
  else if (t === 'action_event') logActionEvent(data);
  else if (t === 'robot_position') updatePosition(data);
  else if (t === 'obstacle') updateObstacle(data);
  else if (t === 'vision_frame') updateVision(data);
  else if (t === 'error') log('ERROR', 'Server', data.message);
}

function updateStatus(s) {
  if (s.state !== undefined) robot.state = s.state;
  if (s.battery !== undefined) robot.battery = s.battery;
  if (s.cpu !== undefined) robot.cpu = s.cpu;
  if (s.velocity !== undefined) robot.speed = s.velocity;
  if (s.position !== undefined) {
    if (Array.isArray(s.position)) {
      const [x, y] = s.position;
      robot.dist = Math.sqrt(x*x + y*y);
    } else if (s.position && typeof s.position === 'object') {
      const x = s.position.x || 0;
      const y = s.position.y || 0;
      robot.dist = Math.sqrt(x*x + y*y);
    }
  }
  if (s.orientation !== undefined) {
    if (Array.isArray(s.orientation)) {
      [robot.roll, robot.pitch, robot.yaw] = s.orientation;
    } else if (s.orientation && typeof s.orientation === 'object') {
      robot.roll = s.orientation.roll || 0;
      robot.pitch = s.orientation.pitch || 0;
      robot.yaw = s.orientation.yaw || 0;
    }
  }
  if (s.obstacle_dist !== undefined) robot.obstacle = s.obstacle_dist;
  if (s.joints !== undefined) robot.joints = s.joints;

  // UI
  document.getElementById('val-battery').textContent = Math.round(robot.battery);
  document.getElementById('bar-battery').style.width = robot.battery + '%';
  document.getElementById('bar-battery').className = 'bar-fill ' + (robot.battery < 20 ? 'low' : robot.battery < 40 ? 'warn' : '');
  document.getElementById('val-cpu').textContent = Math.round(robot.cpu);
  document.getElementById('bar-cpu').style.width = robot.cpu + '%';
  document.getElementById('bar-cpu').className = 'bar-fill cpu ' + (robot.cpu > 80 ? 'low' : robot.cpu > 60 ? 'warn' : '');
  document.getElementById('val-speed').textContent = robot.speed.toFixed(1);
  document.getElementById('val-dist').textContent = robot.dist.toFixed(1);
  document.getElementById('val-roll').textContent = robot.roll.toFixed(1) + '°';
  document.getElementById('val-pitch').textContent = robot.pitch.toFixed(1) + '°';
  document.getElementById('val-yaw').textContent = robot.yaw.toFixed(1) + '°';

  // mode badge
  const badge = document.getElementById('mode-badge');
  badge.textContent = robot.state.toUpperCase();
  badge.className = 'badge ' + (robot.state === 'moving' ? 'moving' : robot.state === 'avoiding' ? 'avoiding' : robot.state === 'error' ? 'error' : '');

  // joints
  const jlist = document.getElementById('joint-list');
  if (robot.joints && jlist) {
    const names = { left_knee:'左膝关节', right_knee:'右膝关节', hip:'髋关节', left_shoulder:'左肩关节', right_shoulder:'右肩关节', neck:'颈部' };
    jlist.innerHTML = Object.entries(robot.joints).map(([k, v]) => {
      const cls = v === 'ok' ? 'ok' : v === 'warn' ? 'warn' : 'err';
      const txt = v === 'ok' ? '正常' : v === 'warn' ? '微热' : '故障';
      return `<div class="joint-row"><span>${names[k] || k}</span><span class="tag ${cls}">${txt}</span></div>`;
    }).join('');
  }

  // obstacle
  updateObstacleUI(robot.obstacle);

  // history
  history.speed.push(robot.speed); history.speed.shift();
  history.battery.push(robot.battery); history.battery.shift();
  drawChart();
}

function updateObstacle(data) {
  const dist = data.distance !== undefined ? data.distance : null;
  robot.obstacle = dist;
  updateObstacleUI(dist);
  if (dist !== null) log('WARN', 'Obstacle', `检测到障碍物 ${dist.toFixed(2)}m`);
}

function updateObstacleUI(dist) {
  const fill = document.getElementById('dist-fill');
  const val = document.getElementById('dist-val');
  if (!fill || !val) return;
  if (dist === null || dist === undefined) {
    fill.style.width = '100%'; fill.style.background = '#22c55e'; val.textContent = '安全';
  } else {
    const pct = Math.min(dist / 3.0, 1) * 100;
    fill.style.width = pct + '%';
    fill.style.background = dist < 0.5 ? '#ef4444' : dist < 1.2 ? '#f59e0b' : '#22c55e';
    val.textContent = dist.toFixed(2) + 'm';
  }
}

function updateTask(data) {
  const name = data.task_name || data.task_id || '任务';
  const idx = data.action_index || 0;
  const total = data.total_actions || 0;
  const event = data.event || '';
  document.getElementById('task-name').textContent = name;
  document.getElementById('action-count').textContent = idx;
  document.getElementById('total-actions').textContent = total;
  const pct = total > 0 ? (idx / total) * 100 : 0;
  document.getElementById('task-bar').style.width = pct + '%';
  log('INFO', 'Task', `${name} ${event} (${idx}/${total})`);
}

function updateTaskList(data) {
  const tasks = data.tasks || [];
  const running = tasks.find(t => t.status === 'running');
  if (running) {
    document.getElementById('task-name').textContent = running.name || running.id;
    document.getElementById('action-count').textContent = running.current_action_index || 0;
    document.getElementById('total-actions').textContent = running.actions ? running.actions.length : 0;
  }
  log('INFO', 'Task', `任务列表更新: ${tasks.length} 个任务`);
}

function logActionEvent(data) {
  const event = data.event || '';
  const actionType = data.action_type || '';
  const detail = data.detail || '';
  const level = event === 'failed' ? 'ERROR' : event === 'completed' ? 'INFO' : 'DEBUG';
  log(level, 'Action', `[${actionType}] ${event} | ${detail}`);
}

function updatePosition(data) {
  const pos = data.position || {};
  const yaw = data.yaw || 0;
  const dist = Math.sqrt((pos.x || 0) ** 2 + (pos.y || 0) ** 2);
  robot.dist = dist;
  robot.yaw = yaw;
  document.getElementById('val-dist').textContent = dist.toFixed(1);
  document.getElementById('val-yaw').textContent = yaw.toFixed(1) + '°';
}

function updateVision(data) {
  const panel = document.querySelector('.video-panel');
  if (!panel) return;

  const frame = data.frame || data.frame_b64 || '';
  if (!frame) {
    log('WARN', 'Vision', '收到空视频帧');
    return;
  }

  if (!/^[A-Za-z0-9+/=]+$/.test(frame)) {
    log('WARN', 'Vision', `视频帧包含非法 base64 字符，长度=${frame.length}`);
    return;
  }

  let img = document.getElementById('cam-real');
  const canvas = document.getElementById('cam-canvas');

  if (!img) {
    if (canvas) canvas.style.display = 'none';
    img = document.createElement('img');
    img.id = 'cam-real';
    img.alt = 'D435i 实时画面';
    img.style.cssText = 'width:100%;height:100%;object-fit:cover;border-radius:8px;display:block;';
    img.onerror = () => {
      log('ERROR', 'Vision', '图片解码失败，帧长度=' + frame.length);
    };
    img.onload = () => {
      if (!img.dataset.reported) {
        log('INFO', 'Vision', `视频帧首次渲染成功，长度=${frame.length}`);
        img.dataset.reported = '1';
      }
    };
    panel.insertBefore(img, panel.firstChild);
  } else {
    img.style.display = 'block';
    if (canvas) canvas.style.display = 'none';
  }

  img.src = 'data:image/jpeg;base64,' + frame;

  const detLabel = document.getElementById('det-label');
  if (data.detections && data.detections.length > 0) {
    const n = data.detections.reduce((m, d) => d.dist < m.dist ? d : m, data.detections[0]);
    if (detLabel) detLabel.textContent = `⚠ ${n.label} ${n.dist.toFixed(2)}m`;
  } else {
    if (detLabel) detLabel.textContent = '';
  }
}

// ── 图表 ──
let chartCtx = null;
function initChart() {
  const c = document.getElementById('chart');
  if (!c) return;
  chartCtx = c.getContext('2d');
  drawChart();
}

function drawChart() {
  if (!chartCtx) return;
  const c = document.getElementById('chart');
  const w = c.width, h = c.height;
  chartCtx.clearRect(0, 0, w, h);

  // 网格
  chartCtx.strokeStyle = '#1e293b'; chartCtx.lineWidth = 1;
  for (let i = 0; i < 5; i++) { chartCtx.beginPath(); chartCtx.moveTo(0, h/4*i); chartCtx.lineTo(w, h/4*i); chartCtx.stroke(); }

  // speed line
  chartCtx.strokeStyle = '#3b82f6'; chartCtx.lineWidth = 1.5; chartCtx.beginPath();
  history.speed.forEach((v, i) => { const x = (i / (history.speed.length - 1)) * w; const y = h - (v / 1.5) * h; if (i === 0) chartCtx.moveTo(x, y); else chartCtx.lineTo(x, y); });
  chartCtx.stroke();

  // battery line
  chartCtx.strokeStyle = '#22c55e'; chartCtx.lineWidth = 1.5; chartCtx.beginPath();
  history.battery.forEach((v, i) => { const x = (i / (history.battery.length - 1)) * w; const y = h - (v / 100) * h; if (i === 0) chartCtx.moveTo(x, y); else chartCtx.lineTo(x, y); });
  chartCtx.stroke();
}

// ── 模拟 ──
function simulate() {
  if (!usingMock) return;
  robot.battery = Math.max(0, robot.battery - 0.02);
  robot.cpu = 30 + Math.random() * 40;
  updateStatus({
    state: robot.state, battery: robot.battery, cpu: robot.cpu, velocity: robot.speed,
    position: [robot.dist, 0, 0], orientation: [robot.roll, robot.pitch, robot.yaw],
    obstacle_dist: robot.obstacle, joints: robot.joints
  });
}

// ── 动作发送 ──
function sendAction(key) {
  const actions = {
    walk: { action: 'walk_straight', params: { distance: 2, speed: 0.8 } },
    turn_left: { action: 'turn_in_place', params: { angle: 45, speed: 0.3, direction: 'left' } },
    turn_right: { action: 'turn_in_place', params: { angle: 45, speed: 0.3, direction: 'right' } },
    wave: { action: 'wave', params: {} },
    squat: { action: 'squat', params: {} },
    avoid: { action: 'avoid_obstacle', params: {} }
  };
  const a = actions[key];
  if (!a) return;
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'command', ...a, seq: ++seq }));
  log('INFO', 'Action', `${a.action} 发送`);
  // active state
  document.querySelectorAll('.btn-action').forEach(b => b.classList.remove('active'));
  const btn = document.querySelector(`[data-action="${key}"]`);
  if (btn) btn.classList.add('active');
}

function taskControl(cmd) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'task_control', command: cmd, task_id: 'current', seq: ++seq }));
  log('INFO', 'TaskCtrl', cmd);
}

function emergencyStop() {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'emergency_stop', seq: ++seq }));
  log('ERROR', 'ESTOP', '紧急停止已触发');
  document.querySelectorAll('.btn-action').forEach(b => b.classList.remove('active'));
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
  startClock(); initChart(); connect();
  setInterval(simulate, 100);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') emergencyStop(); });
});
