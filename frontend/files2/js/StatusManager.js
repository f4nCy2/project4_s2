/**
 * StatusManager.js
 * 负责人：成员B
 * 对应接口：src/common/interfaces.py → IStatusManager
 *
 * ── 职责 ──
 * 作为系统中心状态存储，实现以下接口：
 *   update_robot_status(status)   ← 成员C的APIService写入
 *   get_robot_status()            ← UI读取
 *   add_log(level, source, msg)   ← 成员A任务事件写入
 *   get_logs(count)               ← 日志查询
 *   subscribe_status(callback)    ← 订阅状态推送（Observer模式）
 *
 * ── 与成员C约定的 WebSocket status 消息格式 ──
 * {
 *   type:        "status",
 *   state:       "idle"|"moving"|"avoiding"|"stopped"|"error",
 *   battery:     87.0,          // 0-100
 *   position:    [x, y, z],    // 米
 *   orientation: [roll,pitch,yaw], // 度
 *   velocity:    0.8,           // m/s
 *   error_code:  0,             // 0=正常
 *   // 扩展字段（B界面用）
 *   cpu:           45,
 *   obstacle_dist: 1.2,         // null=无障碍
 *   joints: { left_knee:"ok", right_knee:"ok", hip:"warn",
 *             left_shoulder:"ok", right_shoulder:"ok", neck:"ok" },
 *   timestamp: 1715300000
 * }
 *
 * ── 与成员A约定的任务事件消息格式 ──
 * {
 *   type:    "task_event",
 *   event:   "created"|"started"|"paused"|"resumed"|"stopped"|"completed",
 *   task_id: "task_001",
 *   task_name: "避障直行任务",
 *   action_index: 2,
 *   total_actions: 5
 * }
 */

const StatusManager = (() => {

  // ══════════════════════════════════════════════
  // 内部状态 — 对应 models.py::RobotStatus
  // ══════════════════════════════════════════════
  const _robotStatus = {
    state:       'idle',          // RobotState 枚举
    battery:     87.0,
    position:    [0.0, 0.0, 0.0], // (x, y, z) 米
    orientation: [0.0, 0.0, 0.0], // (roll, pitch, yaw) 度
    velocity:    0.0,
    error_code:  0,
    // 扩展字段（B界面额外需要）
    cpu:           42.0,
    obstacle_dist: null,
    joints: {
      left_knee:      'ok',
      right_knee:     'ok',
      hip:            'warn',
      left_shoulder:  'ok',
      right_shoulder: 'ok',
      neck:           'ok'
    },
    lastUpdate: null
  };

  // 当前任务信息（来自A的task_event消息）
  const _taskStatus = {
    task_id:       null,
    task_name:     '待机中',
    event:         'idle',
    action_index:  0,
    total_actions: 0,
    startTime:     null
  };

  // 历史曲线数据（30帧滑动窗口）
  const _velocityHistory = new Array(30).fill(0);
  const _batteryHistory  = new Array(30).fill(87);

  // Observer 订阅者列表
  const _subscribers = [];

  // 关节中文名映射
  const _jointNames = {
    left_knee:      '左膝关节',
    right_knee:     '右膝关节',
    hip:            '髋关节',
    left_shoulder:  '左肩关节',
    right_shoulder: '右肩关节',
    neck:           '颈部舵机'
  };

  // RobotState → 中文 + 样式
  const _stateMap = {
    idle:     { label: '待机',   cls: 'badge-info' },
    moving:   { label: '移动中', cls: 'badge-ok'   },
    avoiding: { label: '避障中', cls: 'badge-warn' },
    stopped:  { label: '已停止', cls: ''            },
    error:    { label: '故障',   cls: 'badge-err'  }
  };

  // ══════════════════════════════════════════════
  // 私有 UI 更新函数
  // ══════════════════════════════════════════════

  function _updateConnectionUI(connected, wsUrl) {
    const dot   = document.getElementById('conn-dot');
    const label = document.getElementById('conn-label');
    if (!dot || !label) return;
    if (connected) {
      dot.className     = 'conn-dot online';
      label.textContent = '已连接 ' + (wsUrl || '');
    } else {
      dot.className     = 'conn-dot offline';
      label.textContent = '连接断开';
    }
  }

  function _updateStateUI() {
    const s   = _robotStatus.state;
    const map = _stateMap[s] || { label: s, cls: '' };

    // 顶部模式badge
    const modeBadge = document.getElementById('mode-badge');
    if (modeBadge) {
      modeBadge.textContent = map.label;
      modeBadge.className   = 'card-title-badge';
    }

    // 状态指示（错误时顶栏变红）
    const topbar = document.querySelector('.topbar');
    if (topbar) {
      topbar.style.borderBottom = s === 'error'
        ? '1px solid #ef4444'
        : '1px solid var(--c-border)';
    }

    // error_code 提示
    if (_robotStatus.error_code !== 0) {
      LogSystem.error(`机器人故障码: ${_robotStatus.error_code}`);
    }
  }

  function _updateBatteryUI(v) {
    const el  = document.getElementById('m-battery');
    const bar = document.getElementById('bar-battery');
    if (el)  el.textContent = Math.round(v);
    if (bar) {
      bar.style.width      = v + '%';
      bar.style.background = v < 20 ? '#ef4444' : v < 40 ? '#f59e0b' : '#3b82f6';
    }
  }

  function _updateCpuUI(v) {
    const el  = document.getElementById('m-cpu');
    const bar = document.getElementById('bar-cpu');
    if (el)  el.textContent = Math.round(v);
    if (bar) {
      bar.style.width      = v + '%';
      bar.style.background = v > 80 ? '#ef4444' : v > 60 ? '#f59e0b' : '#22c55e';
    }
  }

  function _updateMotionUI() {
    const es = document.getElementById('m-speed');
    const ed = document.getElementById('m-distance');
    // 位置（用 position[0] 作里程近似）
    const dist = Math.sqrt(
      _robotStatus.position[0] ** 2 + _robotStatus.position[1] ** 2
    );
    if (es) es.textContent = _robotStatus.velocity.toFixed(1);
    if (ed) ed.textContent = dist.toFixed(1);

    // 姿态显示（roll/pitch/yaw）
    const er = document.getElementById('m-roll');
    const ep = document.getElementById('m-pitch');
    const ey = document.getElementById('m-yaw');
    if (er) er.textContent = _robotStatus.orientation[0].toFixed(1);
    if (ep) ep.textContent = _robotStatus.orientation[1].toFixed(1);
    if (ey) ey.textContent = _robotStatus.orientation[2].toFixed(1);
  }

  function _updateJointsUI(joints) {
    const container = document.getElementById('joint-list');
    if (!container) return;
    container.innerHTML = '';
    for (const [key, status] of Object.entries(joints)) {
      const name     = _jointNames[key] || key;
      const badgeCls = status === 'ok' ? 'badge-ok' : status === 'warn' ? 'badge-warn' : 'badge-err';
      const badgeTxt = status === 'ok' ? '正常'     : status === 'warn' ? '微热'       : '故障';
      const div = document.createElement('div');
      div.className = 'joint-row';
      div.innerHTML = `<span>${name}</span><span class="badge ${badgeCls}">${badgeTxt}</span>`;
      container.appendChild(div);
    }
  }

  function _updateObstacleUI(dist) {
    const fill = document.getElementById('distance-fill');
    const val  = document.getElementById('distance-val');
    if (!fill || !val) return;
    if (dist === null || dist === undefined) {
      fill.style.width      = '100%';
      fill.style.background = '#22c55e';
      val.textContent       = '安全';
    } else {
      const pct = Math.min(dist / 3.0, 1) * 100;
      fill.style.width      = pct + '%';
      fill.style.background = dist < 0.5 ? '#ef4444' : dist < 1.2 ? '#f59e0b' : '#22c55e';
      val.textContent       = dist.toFixed(2) + 'm';
    }
  }

  /** 更新任务进度面板（消费A的task_event） */
  function _updateTaskUI() {
    const nameEl  = document.getElementById('task-name');
    const badgeEl = document.getElementById('task-badge');
    const countEl = document.getElementById('action-count');
    const barEl   = document.getElementById('task-bar');
    const durEl   = document.getElementById('task-duration');

    const eventLabel = {
      idle:      { text: '待机中',   cls: '' },
      created:   { text: 'CREATED',  cls: 'badge-info' },
      started:   { text: 'RUNNING',  cls: 'badge-ok'   },
      paused:    { text: 'PAUSED',   cls: 'badge-warn'  },
      resumed:   { text: 'RUNNING',  cls: 'badge-ok'   },
      stopped:   { text: 'STOPPED',  cls: ''            },
      completed: { text: 'DONE',     cls: 'badge-ok'   }
    };
    const ev = eventLabel[_taskStatus.event] || { text: _taskStatus.event, cls: '' };

    if (nameEl)  nameEl.textContent  = _taskStatus.task_name;
    if (badgeEl) {
      badgeEl.textContent = ev.text;
      badgeEl.className   = 'badge ' + ev.cls;
    }
    if (countEl) countEl.textContent = _taskStatus.action_index;

    // 进度条
    if (barEl && _taskStatus.total_actions > 0) {
      const pct = (_taskStatus.action_index / _taskStatus.total_actions) * 100;
      barEl.style.width = pct + '%';
    } else if (barEl) {
      barEl.style.width = '0%';
    }

    // 任务时长
    if (durEl && _taskStatus.startTime) {
      const sec = Math.floor((Date.now() - _taskStatus.startTime) / 1000);
      durEl.textContent = sec + 's';
    }
  }

  /** 通知所有订阅者 */
  function _notifySubscribers() {
    const snapshot = getRobotStatus();
    _subscribers.forEach(cb => { try { cb(snapshot); } catch(e) {} });
  }

  // ══════════════════════════════════════════════
  // 公开 API — 对应 IStatusManager 接口
  // ══════════════════════════════════════════════

  /**
   * update_robot_status(status)
   * 由成员C的APIService调用，写入机器人最新状态
   * @param {object} status - 对应 models.py::RobotStatus 的JSON
   */
  function updateRobotStatus(status) {
    // 核心字段（对应 RobotStatus dataclass）
    if (status.state       !== undefined) _robotStatus.state       = status.state;
    if (status.battery     !== undefined) _robotStatus.battery     = status.battery;
    if (status.position    !== undefined) _robotStatus.position    = status.position;
    if (status.orientation !== undefined) _robotStatus.orientation = status.orientation;
    if (status.velocity    !== undefined) _robotStatus.velocity    = status.velocity;
    if (status.error_code  !== undefined) _robotStatus.error_code  = status.error_code;
    // 扩展字段
    if (status.cpu           !== undefined) _robotStatus.cpu           = status.cpu;
    if (status.obstacle_dist !== undefined) _robotStatus.obstacle_dist = status.obstacle_dist;
    if (status.joints        !== undefined) _robotStatus.joints        = status.joints;
    _robotStatus.lastUpdate = Date.now();

    // 历史窗口
    _velocityHistory.push(_robotStatus.velocity); _velocityHistory.shift();
    _batteryHistory.push(_robotStatus.battery);   _batteryHistory.shift();

    // 刷新UI
    _updateStateUI();
    _updateBatteryUI(_robotStatus.battery);
    _updateCpuUI(_robotStatus.cpu);
    _updateMotionUI();
    _updateJointsUI(_robotStatus.joints);
    _updateObstacleUI(_robotStatus.obstacle_dist);

    // 通知订阅者
    _notifySubscribers();
  }

  /**
   * 兼容旧版消息格式（type:"status"的WebSocket消息直接传入）
   * 内部转换后调用 updateRobotStatus
   */
  function onStatusMessage(data) {
    if (data.type !== 'status') return;
    updateRobotStatus(data);
  }

  /**
   * get_robot_status()
   * 返回当前状态快照（只读副本）
   */
  function getRobotStatus() {
    return {
      state:         _robotStatus.state,
      battery:       _robotStatus.battery,
      position:      [..._robotStatus.position],
      orientation:   [..._robotStatus.orientation],
      velocity:      _robotStatus.velocity,
      error_code:    _robotStatus.error_code,
      cpu:           _robotStatus.cpu,
      obstacle_dist: _robotStatus.obstacle_dist,
      joints:        { ..._robotStatus.joints },
      lastUpdate:    _robotStatus.lastUpdate
    };
  }

  /**
   * add_log(level, source, message)
   * 由成员A的TaskManager调用，记录任务事件日志
   * @param {'INFO'|'WARN'|'ERROR'|'DEBUG'} level
   * @param {string} source  - 来源模块，如 "TaskManager"
   * @param {string} message
   */
  function addLog(level, source, message) {
    const lvl = level === 'WARNING' ? 'WARN' : level;
    LogSystem.log(lvl, `[${source}] ${message}`);
  }

  /**
   * get_logs(count)
   * 返回最近N条日志（供UI查询）
   */
  function getLogs(count) {
    return LogSystem.getLogs(count || 100);
  }

  /**
   * subscribe_status(callback)
   * Observer模式：注册状态推送回调
   * @param {function} callback - fn(RobotStatus)
   */
  function subscribeStatus(callback) {
    if (typeof callback === 'function') _subscribers.push(callback);
  }

  /**
   * 处理A发来的任务事件消息（type:"task_event"）
   * 由 RobotDashboard._handleMessage() 调用
   */
  function onTaskEvent(data) {
    _taskStatus.task_id       = data.task_id       ?? _taskStatus.task_id;
    _taskStatus.task_name     = data.task_name     ?? _taskStatus.task_name;
    _taskStatus.event         = data.event         ?? _taskStatus.event;
    _taskStatus.action_index  = data.action_index  ?? _taskStatus.action_index;
    _taskStatus.total_actions = data.total_actions ?? _taskStatus.total_actions;

    if (data.event === 'started' || data.event === 'created') {
      _taskStatus.startTime = Date.now();
    }
    if (data.event === 'stopped' || data.event === 'completed') {
      _taskStatus.startTime = null;
    }

    _updateTaskUI();

    // 同步更新机器人state
    if (data.event === 'started' || data.event === 'resumed') {
      _robotStatus.state = 'moving';
    } else if (data.event === 'stopped') {
      _robotStatus.state = 'stopped';
    } else if (data.event === 'completed') {
      _robotStatus.state = 'idle';
    }
    _updateStateUI();

    addLog('INFO', 'TaskManager',
      `任务[${data.task_name || data.task_id}] 事件:${data.event} ` +
      (data.total_actions ? `步骤:${data.action_index}/${data.total_actions}` : '')
    );
  }

  // ── 以下为内部辅助，供其他JS模块使用 ──

  function setConnected(connected, wsUrl) {
    _robotStatus.connected = connected;
    _updateConnectionUI(connected, wsUrl);
  }

  function setObstacleDist(d) {
    _robotStatus.obstacle_dist = d;
    _updateObstacleUI(d);
  }

  function getVelocity()  { return _robotStatus.velocity; }
  function setVelocity(v) { _robotStatus.velocity = v; _updateMotionUI(); }

  function getHistory() {
    return { speed: [..._velocityHistory], battery: [..._batteryHistory] };
  }

  /** 模拟模式：生成假数据推送（联调前使用） */
  function simulateUpdate() {
    _robotStatus.battery  = Math.max(0, _robotStatus.battery - 0.05);
    _robotStatus.cpu      = 30 + Math.random() * 40;
    updateRobotStatus({
      state:       _robotStatus.state,
      battery:     _robotStatus.battery,
      position:    [
        _robotStatus.position[0] + _robotStatus.velocity * 0.001,
        _robotStatus.position[1],
        0
      ],
      orientation:   _robotStatus.orientation,
      velocity:      _robotStatus.velocity,
      error_code:    0,
      cpu:           _robotStatus.cpu,
      obstacle_dist: _robotStatus.obstacle_dist,
      joints:        _robotStatus.joints
    });
  }

  return {
    // IStatusManager 接口
    updateRobotStatus,
    getRobotStatus,
    addLog,
    getLogs,
    subscribeStatus,
    // WebSocket 消息入口
    onStatusMessage,
    onTaskEvent,
    // 内部辅助
    setConnected,
    setObstacleDist,
    getVelocity, setVelocity,
    getHistory,
    simulateUpdate
  };
})();
