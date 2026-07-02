/**
 * ws-mock/mock-server.js
 * 本地测试用 WebSocket 模拟服务器
 *
 * 使用方法：
 *   1. npm install ws          （只需装一次）
 *   2. node ws-mock/mock-server.js
 *   3. 浏览器打开 index.html
 *
 * 功能：
 *   - 模拟机器人状态推送（电量、CPU、速度、关节状态）
 *   - 模拟障碍物检测上报
 *   - 接收并确认前端发出的指令
 *   - 心跳回复
 */

const WebSocket = require('ws');

const PORT = 8765;
const wss  = new WebSocket.Server({ port: PORT });

console.log(`[MockServer] 启动中，监听 ws://localhost:${PORT}`);

// -------- 机器人状态（可被指令修改）--------
let robotState = {
  battery:       87.0,
  cpu:           42.0,
  speed:         0.0,
  distance:      0.0,
  obstacle_dist: null,
  joints: {
    left_knee:      'ok',
    right_knee:     'ok',
    hip:            'warn',
    left_shoulder:  'ok',
    right_shoulder: 'ok',
    neck:           'ok'
  }
};

let obstacleSimTimer = null;

// -------- 连接处理 --------
wss.on('connection', (ws) => {
  console.log('[MockServer] 客户端已连接');

  // 立即推送一条状态
  ws.send(JSON.stringify({ type: 'status', ...robotState, timestamp: Date.now() }));

  // 定时推送状态（每800ms）
  const statusTimer = setInterval(() => {
    if (ws.readyState !== WebSocket.OPEN) { clearInterval(statusTimer); return; }

    // 模拟电量缓慢下降
    robotState.battery = Math.max(0, robotState.battery - 0.03);
    // 模拟CPU随机波动
    robotState.cpu = 30 + Math.round(Math.random() * 35);
    // 累积里程
    if (robotState.speed > 0) {
      robotState.distance += robotState.speed * 0.8 / 1000;
    }

    ws.send(JSON.stringify({
      type:          'status',
      battery:       parseFloat(robotState.battery.toFixed(1)),
      cpu:           robotState.cpu,
      speed:         robotState.speed,
      distance:      parseFloat(robotState.distance.toFixed(2)),
      obstacle_dist: robotState.obstacle_dist,
      joints:        robotState.joints,
      timestamp:     Date.now()
    }));
  }, 800);

  // 随机模拟障碍物出现（每15-25秒触发一次）
  function scheduleObstacle() {
    const delay = 15000 + Math.random() * 10000;
    obstacleSimTimer = setTimeout(() => {
      if (ws.readyState !== WebSocket.OPEN) return;
      const dist = parseFloat((0.4 + Math.random() * 0.8).toFixed(2));
      robotState.obstacle_dist = dist;
      ws.send(JSON.stringify({
        type:       'obstacle',
        distance:   dist,
        direction:  'center',
        confidence: parseFloat((0.85 + Math.random() * 0.1).toFixed(2)),
        timestamp:  Date.now()
      }));
      console.log(`[MockServer] 模拟障碍物: 距离 ${dist}m`);

      // 3秒后清除障碍（模拟绕过）
      setTimeout(() => {
        robotState.obstacle_dist = null;
        scheduleObstacle();
      }, 3000);
    }, delay);
  }
  scheduleObstacle();

  // -------- 接收指令 --------
  ws.on('message', (raw) => {
    let msg;
    try { msg = JSON.parse(raw); } catch { return; }

    if (msg.type === 'heartbeat') {
      ws.send(JSON.stringify({ type: 'heartbeat', ts: Date.now() }));
      return;
    }

    if (msg.type === 'command') {
      const { action, params, seq } = msg;
      console.log(`[MockServer] 收到指令 seq=${seq} action=${action}`, params || '');

      // 更新状态
      switch (action) {
        case 'walk':
          robotState.speed = params?.speed ?? 0.8;
          break;
        case 'turn_left':
        case 'turn_right':
          robotState.speed = 0.1;
          setTimeout(() => { robotState.speed = 0; }, (params?.duration ?? 1.5) * 1000);
          break;
        case 'wave':
        case 'squat':
          robotState.speed = 0;
          break;
        case 'stop':
        case 'avoid_stop':
          robotState.speed = 0;
          break;
        case 'avoid_start':
          robotState.speed = 0.8;
          break;
      }

      // 回复ACK
      ws.send(JSON.stringify({
        type:   'ack',
        seq:    seq,
        action: action,
        status: 'received',
        ts:     Date.now()
      }));
    }
  });

  ws.on('close', () => {
    clearInterval(statusTimer);
    if (obstacleSimTimer) clearTimeout(obstacleSimTimer);
    console.log('[MockServer] 客户端断开连接');
  });

  ws.on('error', (err) => {
    console.error('[MockServer] 错误:', err.message);
  });
});

wss.on('error', (err) => {
  console.error('[MockServer] 服务器错误:', err.message);
});

console.log(`[MockServer] 就绪 — 在浏览器打开 index.html 即可联调`);

// ── 模拟成员A发送任务事件（每20秒跑一次完整任务流程）──
function scheduleTaskSimulation(ws) {
  setTimeout(() => {
    if (ws.readyState !== WebSocket.OPEN) return;

    const taskId   = 'task_' + Date.now();
    const taskName = '避障直行综合任务';
    const totalActions = 5;
    const events = ['created', 'started', 'started', 'started', 'started', 'completed'];

    events.forEach((event, i) => {
      setTimeout(() => {
        if (ws.readyState !== WebSocket.OPEN) return;
        ws.send(JSON.stringify({
          type:          'task_event',
          event,
          task_id:       taskId,
          task_name:     taskName,
          action_index:  Math.min(i, totalActions),
          total_actions: totalActions,
          timestamp:     Date.now()
        }));
        console.log(`[MockServer] 任务事件: ${event} ${i}/${totalActions}`);
      }, i * 2000);
    });

    // 递归调度
    setTimeout(() => scheduleTaskSimulation(ws), 25000);
  }, 20000);
}

// 在connection事件里调用（追加到原有逻辑末尾）
const _origOn = wss.listeners('connection')[0];
wss.removeAllListeners('connection');
wss.on('connection', (ws) => {
  _origOn(ws);
  scheduleTaskSimulation(ws);
});
