/**
 * ControlPanel.js
 * 负责人：成员B
 * 对应接口：src/status_ui/control_panel.py → ControlPanel
 *
 * ── 职责 ──
 * 提供任务控制的回调接口。
 * 成员A的 main.py 将回调注册到 ControlPanel，
 * UI按钮触发时调用对应回调，通过WebSocket发送给C→底层。
 *
 * ── 回调属性（由 main.py 通过 WebSocket 指令实现）──
 *   on_start_task(task_id)
 *   on_stop_task(task_id)
 *   on_pause_task(task_id)
 *   on_resume_task(task_id)
 *   on_send_action(action_type, params)
 *
 * ── 与成员A约定的 ActionType 枚举 ──
 *   walk_straight / turn_in_place / turn_walk /
 *   stop / walk_backward / sidestep
 *
 * ── 与成员C约定的指令消息格式 ──
 * {
 *   type:        "command",
 *   action:      "walk_straight",
 *   params:      { distance_m: 2.0, speed: 0.5 },
 *   seq:         1001,
 *   priority:    "NORMAL" | "HIGH"
 * }
 * {
 *   type:    "task_control",
 *   command: "start" | "stop" | "pause" | "resume",
 *   task_id: "task_001"
 * }
 */

const ControlPanel = (() => {

  let _seq     = 1000;  // 单调递增指令序列号
  let _sendFn  = null;  // 实际发送函数（由Dashboard注入）

  // 当前动作（用于按钮高亮 / 进度条）
  let _currentAction = null;
  let _progressTimer = null;

  // ── ActionType 枚举（与A对齐）──
  const ActionType = {
    WALK_STRAIGHT: 'walk_straight',
    TURN_IN_PLACE: 'turn_in_place',
    TURN_WALK:     'turn_walk',
    STOP:          'stop',
    WALK_BACKWARD: 'walk_backward',
    SIDESTEP:      'sidestep',
    WAVE:          'wave',       // 扩展：挥手
    SQUAT:         'squat',      // 扩展：下蹲
    AVOID_START:   'avoid_start' // 扩展：视觉避障
  };

  // 动作按钮配置（UI展示用）
  const _actionConfig = {
    walk:       { type: ActionType.WALK_STRAIGHT, label: '直线行走', speed: 0.8, duration: 5000, velocity: 0.8 },
    turn_left:  { type: ActionType.TURN_IN_PLACE, label: '左转',     speed: 0.3, duration: 1500, velocity: 0.1,
                  params: { direction: 'left',  angle: 45 } },
    turn_right: { type: ActionType.TURN_IN_PLACE, label: '右转',     speed: 0.3, duration: 1500, velocity: 0.1,
                  params: { direction: 'right', angle: 45 } },
    wave:       { type: ActionType.WAVE,          label: '挥手',     speed: 0.0, duration: 2500, velocity: 0.0 },
    squat:      { type: ActionType.SQUAT,         label: '下蹲起立', speed: 0.0, duration: 3000, velocity: 0.0 },
    avoid:      { type: ActionType.AVOID_START,   label: '视觉避障', speed: 0.8, duration: -1,   velocity: 0.8 }
  };

  // ── 私有 ──

  function _nextSeq() { return ++_seq; }

  function _setActiveBtn(actionKey) {
    document.querySelectorAll('.action-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.action === actionKey);
    });
  }

  function _startProgress(duration) {
    clearInterval(_progressTimer);
    const bar = document.getElementById('task-bar');
    let prog = 0;

    if (duration < 0) {
      // 持续模式（避障）：进度条来回摆动
      let dir = 1;
      _progressTimer = setInterval(() => {
        prog += dir * 2;
        if (prog >= 95) dir = -1;
        if (prog <= 5)  dir = 1;
        if (bar) bar.style.width = prog + '%';
      }, 60);
      return;
    }
    const step = 100 / (duration / 50);
    _progressTimer = setInterval(() => {
      prog = Math.min(100, prog + step);
      if (bar) bar.style.width = prog + '%';
      if (prog >= 100) clearInterval(_progressTimer);
    }, 50);
  }

  function _resetUI() {
    clearInterval(_progressTimer);
    _currentAction = null;
    _setActiveBtn(null);
    const bar = document.getElementById('task-bar');
    if (bar) bar.style.width = '0%';
    StatusManager.setVelocity(0);
  }

  // ══════════════════════════════════════════════
  // 公开 API — 对应 control_panel.py 接口
  // ══════════════════════════════════════════════

  /**
   * 初始化（由Dashboard调用，注入发送函数）
   */
  function init(sendFn) {
    _sendFn = sendFn;
    LogSystem.info('ControlPanel', '控制面板初始化完成');
  }

  // ── 任务控制（on_start/stop/pause/resume 的Web实现）──

  /**
   * start_task(task_id)
   * 触发：启动任务按钮
   */
  function startTask(taskId) {
    const cmd = { type: 'task_control', command: 'start', task_id: taskId, seq: _nextSeq() };
    LogSystem.info('ControlPanel', `启动任务 task_id=${taskId} seq=${cmd.seq}`);
    if (_sendFn) _sendFn(cmd);
  }

  /**
   * stop_task(task_id)
   */
  function stopTask(taskId) {
    _resetUI();
    const cmd = { type: 'task_control', command: 'stop', task_id: taskId, seq: _nextSeq() };
    LogSystem.info('ControlPanel', `停止任务 task_id=${taskId}`);
    if (_sendFn) _sendFn(cmd);
  }

  /**
   * pause_task(task_id)
   */
  function pauseTask(taskId) {
    const cmd = { type: 'task_control', command: 'pause', task_id: taskId, seq: _nextSeq() };
    LogSystem.info('ControlPanel', `暂停任务 task_id=${taskId}`);
    if (_sendFn) _sendFn(cmd);
  }

  /**
   * resume_task(task_id)
   */
  function resumeTask(taskId) {
    const cmd = { type: 'task_control', command: 'resume', task_id: taskId, seq: _nextSeq() };
    LogSystem.info('ControlPanel', `继续任务 task_id=${taskId}`);
    if (_sendFn) _sendFn(cmd);
  }

  /**
   * send_manual_action(action_type, params)
   * 手动发送单个动作（手动模式按钮）
   * @param {string} actionType - ActionType 枚举值
   * @param {object} params     - 如 { distance_m:2.0, speed:0.5 }
   */
  function sendManualAction(actionType, params) {
    const cmd = {
      type:     'command',
      action:   actionType,
      params:   params || {},
      seq:      _nextSeq(),
      priority: 'NORMAL'
    };
    LogSystem.info('ControlPanel', `手动动作 action=${actionType} seq=${cmd.seq}`);
    if (_sendFn) _sendFn(cmd);
  }

  /**
   * get_status_text()
   * 返回UI可用的状态数据（供界面读取）
   */
  function getStatusText() {
    return StatusManager.getRobotStatus();
  }

  /**
   * get_logs_for_display(count)
   * 返回日志列表（供界面展示）
   */
  function getLogsForDisplay(count) {
    return StatusManager.getLogs(count || 100);
  }

  // ── 按钮点击入口（HTML onclick 调用）──

  /**
   * executeAction(key) — 由 index.html 的 onclick 调用
   * key: 'walk' | 'turn_left' | 'turn_right' | 'wave' | 'squat' | 'avoid'
   */
  function executeAction(key) {
    const cfg = _actionConfig[key];
    if (!cfg) { LogSystem.error('ControlPanel', `未知动作键: ${key}`); return; }

    // 视觉避障特殊处理
    if (key === 'avoid') {
      if (ObstacleAvoidance.isActive()) {
        LogSystem.warning('ControlPanel', '视觉避障已在运行');
        return;
      }
      _currentAction = key;
      _setActiveBtn(key);
      _startProgress(-1);
      StatusManager.setVelocity(cfg.velocity);
      ObstacleAvoidance.start();
      sendManualAction(cfg.type, { mode: 'visual', camera: 'front' });
      return;
    }

    // 普通动作
    if (_currentAction) _resetUI();
    _currentAction = key;
    _setActiveBtn(key);
    _startProgress(cfg.duration);
    StatusManager.setVelocity(cfg.velocity);

    const params = cfg.params
      ? cfg.params
      : { speed: cfg.speed, duration: cfg.duration / 1000 };
    sendManualAction(cfg.type, params);

    // 仿真自动完成
    if (cfg.duration > 0) {
      setTimeout(() => {
        if (_currentAction === key) {
          LogSystem.info('ControlPanel', `${cfg.label} 执行完成`);
          _resetUI();
        }
      }, cfg.duration);
    }
  }

  /**
   * emergencyStop() — 紧急停止（E-STOP，支持 ESC 快捷键）
   * 验收要求：紧急停止立即生效
   */
  function emergencyStop() {
    clearInterval(_progressTimer);
    if (ObstacleAvoidance.isActive()) ObstacleAvoidance.stop();

    const prev = _currentAction;
    _currentAction = null;
    _setActiveBtn(null);
    StatusManager.setVelocity(0);
    StatusManager.setObstacleDist(null);
    const bar = document.getElementById('task-bar');
    if (bar) bar.style.width = '0%';

    const cmd = {
      type:     'command',
      action:   ActionType.STOP,
      params:   {},
      seq:      _nextSeq(),
      priority: 'HIGH'
    };
    LogSystem.warning('ControlPanel', `紧急停止！[seq:${cmd.seq}] 中止动作: ${prev || '无'}`);
    if (_sendFn) _sendFn(cmd);
  }

  /**
   * sendAvoidCmd(cmd) — 供 ObstacleAvoidance 内部调用，发送避障子指令
   */
  function sendAvoidCmd(cmd) {
    cmd.seq = _nextSeq();
    LogSystem.info('ControlPanel', `避障子指令 [seq:${cmd.seq}] action=${cmd.action}`);
    if (_sendFn) _sendFn(cmd);
  }

  // ESC 快捷键绑定（验收要求）
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') emergencyStop();
  });

  return {
    // control_panel.py 接口
    startTask, stopTask, pauseTask, resumeTask,
    sendManualAction,
    getStatusText, getLogsForDisplay,
    // 内部用
    init,
    executeAction,
    emergencyStop,
    sendAvoidCmd,
    ActionType
  };
})();
