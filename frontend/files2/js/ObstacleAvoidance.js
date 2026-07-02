/**
 * ObstacleAvoidance.js
 * 负责人：成员B
 * 功能：视觉避障系统——摄像头画面仿真、障碍检测、状态机控制、界面反馈
 *
 * 避障状态机：
 *   IDLE → DETECTING → PLANNING → AVOIDING → RESUMING → IDLE
 *
 * 与成员C约定的避障相关消息格式：
 *   上报障碍：{ type:"obstacle", distance: 0.8, direction:"center", confidence: 0.92 }
 *   绕行指令（由本模块生成后交给ControlPanel/C发送）：
 *     { type:"command", action:"turn_left",  params:{ angle:45 } }
 *     { type:"command", action:"walk",       params:{ speed:0.4, duration:2 } }
 *     { type:"command", action:"turn_right", params:{ angle:45 } }
 *     { type:"command", action:"walk",       params:{ speed:0.8 } }  ← 恢复直行
 */

const ObstacleAvoidance = (() => {

  // -------- 状态枚举 --------
  const STATE = {
    IDLE:      'IDLE',
    DETECTING: 'DETECTING',
    PLANNING:  'PLANNING',
    AVOIDING:  'AVOIDING',
    RESUMING:  'RESUMING'
  };

  // -------- 内部变量 --------
  let _state      = STATE.IDLE;
  let _active     = false;       // 避障功能是否启用
  let _canvas     = null;
  let _ctx        = null;
  let _animFrame  = null;
  let _frameCount = 0;

  // 仿真场景中的障碍物列表
  let _obstacles  = [];
  // 机器人在仿真画面中的位置
  let _robotX     = 230;
  let _robotY     = 160;
  let _robotAngle = 0;   // 朝向角度（度）
  // 当前绕行计时器
  let _avoidTimer = null;
  // 避障步骤序列下标
  let _avoidStep  = 0;

  // 最近一次检测到的障碍距离（m）
  let _detectedDist = null;

  // 回调：发送指令给通信层（由Dashboard注入）
  let _sendCommand = null;

  // -------- 状态机流转 --------

  function _setState(newState) {
    _state = newState;
    _updateStateMachineUI();

    // 更新右上角badge
    const badge = document.getElementById('avoid-status-badge');
    if (!badge) return;
    const labels = {
      IDLE:      { text: '待机',   cls: '' },
      DETECTING: { text: '检测中', cls: 'badge-info' },
      PLANNING:  { text: '规划中', cls: 'badge-warn' },
      AVOIDING:  { text: '绕行中', cls: 'badge-err'  },
      RESUMING:  { text: '恢复中', cls: 'badge-ok'   }
    };
    const l = labels[newState];
    badge.textContent = l.text;
    badge.className   = 'card-title-badge ' + (l.cls || '');
  }

  /** 更新避障状态机步骤高亮 */
  function _updateStateMachineUI() {
    const map = {
      DETECTING: 'avs-detecting',
      PLANNING:  'avs-planning',
      AVOIDING:  'avs-avoiding',
      RESUMING:  'avs-resuming'
    };
    // 计算已完成的步骤
    const order = ['DETECTING','PLANNING','AVOIDING','RESUMING'];
    const curIdx = order.indexOf(_state);

    order.forEach((s, i) => {
      const el = document.getElementById(map[s]);
      if (!el) return;
      el.classList.remove('active', 'done');
      if (i === curIdx) el.classList.add('active');
      else if (i < curIdx) el.classList.add('done');
    });
  }

  // -------- 摄像头画面仿真 --------

  /** 初始化Canvas */
  function _initCanvas() {
    _canvas = document.getElementById('camera-canvas');
    if (!_canvas) return;
    _ctx = _canvas.getContext('2d');
    _spawnObstacles();
    _renderLoop();
  }

  /** 随机生成障碍物 */
  function _spawnObstacles() {
    _obstacles = [];
    const count = 2 + Math.floor(Math.random() * 2);
    for (let i = 0; i < count; i++) {
      _obstacles.push({
        x:      80  + Math.random() * 300,
        y:      20  + Math.random() * 120,
        w:      30  + Math.random() * 50,
        h:      30  + Math.random() * 60,
        color:  `hsl(${Math.random()*360},60%,50%)`,
        vx:     (Math.random() - 0.5) * 0.3,  // 缓慢横移，增加真实感
        detected: false
      });
    }
  }

  /** 主渲染循环 */
  function _renderLoop() {
    _frameCount++;
    _drawFrame();
    _animFrame = requestAnimationFrame(_renderLoop);
  }

  /** 绘制单帧 */
  function _drawFrame() {
    const W = _canvas.width;
    const H = _canvas.height;
    const ctx = _ctx;

    // --- 背景（地面/走廊场景）---
    ctx.fillStyle = '#111';
    ctx.fillRect(0, 0, W, H);

    // 地板透视网格
    ctx.strokeStyle = 'rgba(255,255,255,0.06)';
    ctx.lineWidth = 0.5;
    // 水平线
    for (let y = H * 0.55; y < H; y += 14) {
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
    }
    // 透视纵线（向灭点汇聚）
    const vanX = W / 2, vanY = H * 0.52;
    for (let x = 0; x <= W; x += 30) {
      ctx.beginPath(); ctx.moveTo(x, H); ctx.lineTo(vanX, vanY); ctx.stroke();
    }

    // 地平线
    ctx.strokeStyle = 'rgba(255,255,255,0.1)';
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, H * 0.52); ctx.lineTo(W, H * 0.52); ctx.stroke();

    // --- 走廊墙壁 ---
    ctx.strokeStyle = 'rgba(100,120,160,0.2)';
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(60, 0); ctx.lineTo(vanX, vanY); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(W - 60, 0); ctx.lineTo(vanX, vanY); ctx.stroke();

    // --- 移动障碍物 ---
    _obstacles.forEach(obs => {
      obs.x += obs.vx;
      if (obs.x < 20 || obs.x + obs.w > W - 20) obs.vx *= -1;

      // 判断是否在"危险区"（中央下半部分，近处）
      const inDangerZone = (
        obs.x + obs.w > W * 0.25 && obs.x < W * 0.75 &&
        obs.y + obs.h > H * 0.3
      );

      // 检测框颜色
      if (inDangerZone && _active) {
        obs.detected = true;
        ctx.strokeStyle = '#ef4444';
        ctx.lineWidth   = 2;
        // 闪烁效果
        if (Math.floor(_frameCount / 8) % 2 === 0) {
          ctx.fillStyle = 'rgba(239,68,68,0.12)';
          ctx.fillRect(obs.x - 3, obs.y - 3, obs.w + 6, obs.h + 6);
        }
      } else {
        obs.detected  = false;
        ctx.strokeStyle = 'rgba(100,200,100,0.5)';
        ctx.lineWidth   = 1;
      }

      // 画障碍物实体（箱子/柱子）
      ctx.fillStyle = obs.color + '88';
      ctx.fillRect(obs.x, obs.y, obs.w, obs.h);
      ctx.strokeRect(obs.x, obs.y, obs.w, obs.h);

      // 标签
      if (obs.detected && _active) {
        ctx.fillStyle   = '#ef4444';
        ctx.font        = 'bold 10px monospace';
        ctx.fillText('OBSTACLE', obs.x + 2, obs.y - 4);

        // 估算距离（Y坐标越大 = 越近，近似 0.3m ~ 3m）
        const dist = Math.max(0.3, ((H - obs.y - obs.h) / H) * 3).toFixed(1);
        ctx.fillStyle = '#f59e0b';
        ctx.fillText(`~${dist}m`, obs.x + obs.w - 28, obs.y - 4);
      }
    });

    // --- 检测射线（当避障激活时） ---
    if (_active) {
      const rays = [-20, -10, 0, 10, 20];
      rays.forEach(angle => {
        const rad = (angle - 90) * Math.PI / 180;
        const len = 180;
        const ex  = vanX + Math.cos(rad) * len;
        const ey  = vanY + Math.sin(rad) * len;
        ctx.strokeStyle = angle === 0
          ? 'rgba(59,130,246,0.6)'
          : 'rgba(59,130,246,0.2)';
        ctx.lineWidth = angle === 0 ? 1.5 : 0.7;
        ctx.setLineDash([4, 4]);
        ctx.beginPath(); ctx.moveTo(vanX, vanY); ctx.lineTo(ex, ey); ctx.stroke();
        ctx.setLineDash([]);
      });
    }

    // --- HUD 覆盖层 ---
    // 左上：FPS
    ctx.fillStyle = 'rgba(255,255,255,0.3)';
    ctx.font = '9px monospace';
    ctx.fillText(`${Math.round(_frameCount % 60 === 0 ? 30 : 30)}fps  MODE:${_active ? 'AVOID' : 'STANDBY'}`, 8, 14);

    // 中央准星
    const cx = W / 2, cy = H / 2 + 10;
    ctx.strokeStyle = 'rgba(255,255,255,0.3)';
    ctx.lineWidth   = 1;
    ctx.beginPath(); ctx.moveTo(cx - 12, cy); ctx.lineTo(cx - 4, cy); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(cx + 4,  cy); ctx.lineTo(cx + 12, cy); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(cx, cy - 12); ctx.lineTo(cx, cy - 4); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(cx, cy + 4);  ctx.lineTo(cx, cy + 12); ctx.stroke();

    // 右下：状态文字
    ctx.fillStyle = _state === STATE.AVOIDING ? '#ef4444'
                  : _state === STATE.IDLE     ? 'rgba(255,255,255,0.3)'
                  : '#f59e0b';
    ctx.font = 'bold 10px monospace';
    ctx.textAlign = 'right';
    ctx.fillText(_state, W - 8, H - 8);
    ctx.textAlign = 'left';
  }

  // -------- 避障逻辑 --------

  /**
   * 检测是否有障碍物在危险区域
   * @returns {{ detected: boolean, distance: number|null }}
   */
  function _detectObstacle() {
    if (!_obstacles.length) return { detected: false, distance: null };

    const W = _canvas ? _canvas.width  : 460;
    const H = _canvas ? _canvas.height : 220;

    for (const obs of _obstacles) {
      const inCenter = (obs.x + obs.w > W * 0.28 && obs.x < W * 0.72);
      const inFront  = (obs.y + obs.h > H * 0.28);
      if (inCenter && inFront) {
        // 估算距离（y越大 = 越近）
        const dist = parseFloat(Math.max(0.3, ((H - obs.y - obs.h) / H) * 3).toFixed(2));
        return { detected: true, distance: dist };
      }
    }
    return { detected: false, distance: null };
  }

  /**
   * 执行避障动作序列
   * 顺序：左转45° → 直行绕过 → 右转45° → 恢复直行
   */
  function _executeAvoidSequence() {
    // 避障动作步骤定义
    const steps = [
      {
        label:    '规划路径...',
        state:    STATE.PLANNING,
        duration: 800,
        cmd:      null   // 规划阶段不发指令
      },
      {
        label:    '左转45°，绕避障碍',
        state:    STATE.AVOIDING,
        duration: 1200,
        cmd:      { type: 'command', action: 'turn_left', params: { angle: 45, speed: 0.3 } }
      },
      {
        label:    '直行2m，绕过障碍物',
        state:    STATE.AVOIDING,
        duration: 2500,
        cmd:      { type: 'command', action: 'walk', params: { speed: 0.4, duration: 2 } }
      },
      {
        label:    '右转45°，对齐原方向',
        state:    STATE.AVOIDING,
        duration: 1200,
        cmd:      { type: 'command', action: 'turn_right', params: { angle: 45, speed: 0.3 } }
      },
      {
        label:    '恢复直行',
        state:    STATE.RESUMING,
        duration: 1000,
        cmd:      { type: 'command', action: 'walk', params: { speed: 0.8 } }
      }
    ];

    let stepIdx = 0;

    function runStep() {
      if (stepIdx >= steps.length) {
        // 序列完成，回到IDLE
        _setState(STATE.IDLE);
        StatusManager.setSpeed(0.8);
        StatusManager.setObstacleDist(null);
        LogSystem.info('视觉避障完成，已恢复直行');

        // 检测标签清空
        const det = document.getElementById('detection-label');
        if (det) det.textContent = '';

        // 重新生成障碍物场景
        setTimeout(_spawnObstacles, 500);
        return;
      }

      const step = steps[stepIdx];
      _setState(step.state);
      LogSystem.info(`避障步骤 ${stepIdx + 1}/${steps.length}: ${step.label}`);

      // 发送指令
      if (step.cmd && _sendCommand) {
        _sendCommand(step.cmd);
      }

      // 更新速度显示
      if (step.cmd && step.cmd.params && step.cmd.params.speed !== undefined) {
        StatusManager.setSpeed(step.cmd.params.speed);
      } else if (step.cmd && step.cmd.action !== 'walk') {
        StatusManager.setSpeed(0.1);  // 转向时低速
      }

      stepIdx++;
      _avoidTimer = setTimeout(runStep, step.duration);
    }

    runStep();
  }

  // -------- 主循环（避障检测心跳）--------

  let _detectInterval = null;

  function _startDetectionLoop() {
    _detectInterval = setInterval(() => {
      if (!_active || _state !== STATE.IDLE) return;

      _setState(STATE.DETECTING);

      const result = _detectObstacle();

      if (result.detected) {
        _detectedDist = result.distance;
        StatusManager.setObstacleDist(result.distance);

        // 更新检测标签
        const det = document.getElementById('detection-label');
        if (det) det.textContent = `⚠ OBSTACLE ${result.distance}m`;

        LogSystem.warn(`检测到障碍物，距离 ${result.distance}m，启动避障序列`);

        // 进入规划，触发避障动作序列
        _executeAvoidSequence();
      } else {
        _setState(STATE.IDLE);
        StatusManager.setObstacleDist(null);
        const det = document.getElementById('detection-label');
        if (det) det.textContent = '';
      }
    }, 1500);  // 每1.5秒检测一次
  }

  function _stopDetectionLoop() {
    if (_detectInterval) { clearInterval(_detectInterval); _detectInterval = null; }
    if (_avoidTimer)     { clearTimeout(_avoidTimer);      _avoidTimer = null; }
    _setState(STATE.IDLE);
  }

  // -------- 公开API --------

  /**
   * 初始化避障模块
   * @param {function} sendCommandFn - 发送指令的回调（由RobotDashboard注入）
   */
  function init(sendCommandFn) {
    _sendCommand = sendCommandFn;
    _initCanvas();
    LogSystem.info('ObstacleAvoidance', '视觉避障模块初始化完成');
  }

  /**
   * start() — 手动按钮触发，启动本地仿真检测循环
   * 联调后此模式仍保留用于演示
   */
  function start() {
    if (_active) return;
    _active = true;
    _setState(STATE.DETECTING);
    _startDetectionLoop();
    LogSystem.info('ObstacleAvoidance', '视觉避障已启用（仿真检测模式）');
  }

  /**
   * triggerAvoid(distance)
   * ← 联调时由 RobotDashboard._handleMessage 调用
   *   当成员A（经C）发来 type:"obstacle" 消息时触发
   * 直接跳过仿真检测，执行真实避障序列
   * @param {number} distance - 障碍物距离（米）
   */
  function triggerAvoid(distance) {
    if (_state !== STATE.IDLE) {
      LogSystem.warning('ObstacleAvoidance', `收到障碍信号但当前状态为${_state}，忽略`);
      return;
    }
    _active       = true;
    _detectedDist = distance;
    StatusManager.setObstacleDist(distance);

    const det = document.getElementById('detection-label');
    if (det) det.textContent = `⚠ OBSTACLE ${distance.toFixed(2)}m`;

    LogSystem.warning('ObstacleAvoidance',
      `收到A的障碍信号，距离${distance}m，启动避障序列`
    );
    _executeAvoidSequence();
  }

  /**
   * stop() — 紧急停止时调用
   */
  function stop() {
    _active = false;
    _stopDetectionLoop();
    StatusManager.setObstacleDist(null);
    const det = document.getElementById('detection-label');
    if (det) det.textContent = '';
    LogSystem.warning('ObstacleAvoidance', '视觉避障已停止');
  }

  /** 当前是否激活 */
  function isActive() { return _active; }

  /** 当前状态 */
  function getState() { return _state; }

  return { init, start, stop, triggerAvoid, isActive, getState, STATE };
})();
