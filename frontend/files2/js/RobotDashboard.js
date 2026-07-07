/**
 * RobotDashboard.js
 * 负责人：成员B
 * 对应接口：src/status_ui/dashboard.py → RobotDashboard
 *
 * ── 接口对齐 ──
 *   __init__(status_manager)       → Dashboard.init()
 *   register_control_panel(cp)     → 内部自动绑定 ControlPanel
 *   run()                          → DOMContentLoaded 自动启动
 *   update_vision_frame(frame)     → Dashboard.updateVisionFrame(data)
 *
 * ── 消息路由（来自成员C的WebSocket）──
 *   type:"status"      → StatusManager.updateRobotStatus()
 *   type:"task_event"  → StatusManager.onTaskEvent()         ← A的任务事件
 *   type:"obstacle"    → StatusManager.setObstacleDist()
 *                        + ObstacleAvoidance.triggerAvoid()
 *   type:"vision_frame"→ Dashboard.updateVisionFrame()
 *   type:"ack"         → 日志记录
 *   type:"heartbeat"   → 回复
 *
 * ── 刷新率 ──
 *   状态UI: 100ms（验收要求 >= 10Hz）
 *   图表:   1000ms
 */

const Dashboard = (() => {

  // ══════════════════════════════════════════════
  // 配置（联调时只改这里）
  // ══════════════════════════════════════════════
  const CONFIG = {
    WS_URL:          'ws://192.168.1.100:8765',  // ← 改成C给你的地址
    WS_RECONNECT_MS: 3000,
    WS_HEARTBEAT_MS: 5000,
    UI_REFRESH_MS:   100,    // 10Hz，满足验收要求
    CHART_REFRESH_MS:1000
  };

  // ── 内部变量 ──
  let _ws             = null;
  let _wsConnected    = false;
  let _usingMock      = false;
  let _wsReconnTimer  = null;
  let _wsHbTimer      = null;
  let _uiTimer        = null;
  let _chartTimer     = null;
  let _chart          = null;

  // ══════════════════════════════════════════════
  // WebSocket
  // ══════════════════════════════════════════════

  function _connectWS() {
    const dot   = document.getElementById('conn-dot');
    const label = document.getElementById('conn-label');
    if (dot)   dot.className    = 'conn-dot connecting';
    if (label) label.textContent = '连接中...';

    try {
      // 如果配置的是后端 /ws/control，则接收 type=vision_frame, frame=base64
      // 如果直连 D435i vision_server:8765，则接收 frame_b64=base64
      _ws = new WebSocket(CONFIG.WS_URL);

      _ws.onopen = () => {
        _wsConnected = true;
        _usingMock   = false;
        StatusManager.setConnected(true, CONFIG.WS_URL);
        LogSystem.info('Dashboard', `WebSocket 连接成功: ${CONFIG.WS_URL}`);

        _wsHbTimer = setInterval(() => {
          if (_ws && _ws.readyState === WebSocket.OPEN) {
            _ws.send(JSON.stringify({ type: 'heartbeat', ts: Date.now() }));
          }
        }, CONFIG.WS_HEARTBEAT_MS);
      };

      _ws.onmessage = (event) => {
        let data;
        try {
          data = JSON.parse(event.data);
        } catch (e) {
          LogSystem.warning('Dashboard', '收到非JSON消息: ' + String(event.data).slice(0, 200));
          return;
        }
        try {
          _handleMessage(data);
        } catch (e) {
          LogSystem.error('Dashboard', `消息处理异常 type=${data && data.type}: ${e.message}`);
        }
      };

      _ws.onerror = () => {
        LogSystem.warning('Dashboard', 'WebSocket 出错，降级到模拟模式');
        _startMockMode();
      };

      _ws.onclose = () => {
        _wsConnected = false;
        clearInterval(_wsHbTimer);
        StatusManager.setConnected(false);
        if (!_usingMock) {
          LogSystem.warning('Dashboard', `WebSocket 断开，${CONFIG.WS_RECONNECT_MS/1000}秒后重连...`);
          _wsReconnTimer = setTimeout(_connectWS, CONFIG.WS_RECONNECT_MS);
        }
      };

    } catch (e) {
      LogSystem.warning('Dashboard', 'WebSocket 不可用，使用模拟模式');
      _startMockMode();
    }
  }

  /**
   * 消息路由分发
   * ← 所有来自C的消息都在这里处理
   */
  function _handleMessage(data) {
    switch (data.type) {

      // ── 来自C：机器人实时状态（对应 RobotStatus dataclass）──
      case 'status':
        StatusManager.updateRobotStatus(data);
        break;

      // ── 来自A（经C转发）：任务事件通知 ──
      case 'task_event':
        StatusManager.onTaskEvent(data);
        break;

      // ── 来自A（经C转发）：障碍物检测结果 ──
      case 'obstacle':
        LogSystem.warning('Dashboard',
          `障碍物检测: 距离${data.distance}m 方向${data.direction || 'center'} 置信度${data.confidence || '--'}`
        );
        StatusManager.setObstacleDist(data.distance);
        // 触发界面避障状态机
        ObstacleAvoidance.triggerAvoid(data.distance);
        break;

      // ── 来自C：摄像头帧数据（Base64 JPEG）──
      case 'vision_frame':
        updateVisionFrame(data);
        break;

      // ── 来自C：指令确认 ──
      case 'ack':
        LogSystem.info('Dashboard', `指令确认 [seq:${data.seq}] action=${data.action} status=${data.status}`);
        break;

      // ── 心跳回复 ──
      case 'heartbeat':
        break;

      default:
        LogSystem.info('Dashboard', `未知消息类型: ${data.type}`);
    }
  }

  /**
   * _sendCommand — 所有模块发送指令的统一出口（注入给ControlPanel）
   */
  function _sendCommand(cmd) {
    const json = JSON.stringify(cmd);
    if (_wsConnected && _ws && _ws.readyState === WebSocket.OPEN) {
      _ws.send(json);
    } else {
      LogSystem.info('Dashboard', `[MOCK-TX] ${json}`);
    }
  }

  // ══════════════════════════════════════════════
  // update_vision_frame(frame_data)
  // 对应 dashboard.py 接口：显示摄像头画面或障碍物检测框
  // ══════════════════════════════════════════════

  /**
   * 接收C推送的视觉帧，切换到真实画面模式
   * frame_data 格式：
   *   { type:"vision_frame", frame:"<base64 jpeg>", detections:[{x,y,w,h,label,dist},...] }
   */
  function updateVisionFrame(frameData) {
    // 找到显示区域，如果还是canvas就替换为img
    const panel = document.querySelector('.camera-panel');
    if (!panel) return;

    let img = document.getElementById('camera-real');
    if (!img) {
      // 首次收到真实帧：隐藏仿真canvas，插入img
      const canvas = document.getElementById('camera-canvas');
      if (canvas) canvas.style.display = 'none';
      img = document.createElement('img');
      img.id    = 'camera-real';
      img.style.cssText = 'width:100%;border-radius:8px;display:block;';
      panel.insertBefore(img, panel.firstChild);
      LogSystem.info('Dashboard', '已切换到真实摄像头画面');
    }

    const frame = frameData.frame || frameData.frame_b64 || '';
    if (!frame) {
      LogSystem.warning('Dashboard', '收到空视频帧');
      return;
    }
    if (!/^[A-Za-z0-9+/=]+$/.test(frame)) {
      LogSystem.warning('Dashboard', `视频帧包含非法 base64 字符，长度=${frame.length}`);
      return;
    }

    img.onerror = () => {
      LogSystem.error('Dashboard', '图片解码失败，帧长度=' + frame.length);
    };
    img.onload = () => {
      if (!img.dataset.reported) {
        LogSystem.info('Dashboard', `视频帧首次渲染成功，长度=${frame.length}`);
        img.dataset.reported = '1';
      }
    };

    img.src = 'data:image/jpeg;base64,' + frame;

    // 障碍物检测框叠加显示
    if (frameData.detections && frameData.detections.length > 0) {
      const det = document.getElementById('detection-label');
      const nearest = frameData.detections.reduce(
        (min, d) => d.dist < min.dist ? d : min,
        frameData.detections[0]
      );
      if (det) det.textContent = `⚠ ${nearest.label} ${nearest.dist.toFixed(2)}m`;
    } else {
      const det = document.getElementById('detection-label');
      if (det) det.textContent = '';
    }
  }

  // ══════════════════════════════════════════════
  // 模拟模式（联调前）
  // ══════════════════════════════════════════════

  function _startMockMode() {
    if (_usingMock) return;
    _usingMock = true;
    const dot   = document.getElementById('conn-dot');
    const label = document.getElementById('conn-label');
    if (dot)   dot.className    = 'conn-dot connecting';
    if (label) label.textContent = '模拟模式（无WebSocket）';
    LogSystem.warning('Dashboard', '已启动模拟模式，数据由前端生成');
  }

  // ══════════════════════════════════════════════
  // Chart.js 图表
  // ══════════════════════════════════════════════

  function _initChart() {
    const canvas = document.getElementById('chart-main');
    if (!canvas || typeof Chart === 'undefined') return;

    _chart = new Chart(canvas, {
      type: 'line',
      data: {
        labels: Array.from({ length: 30 }, (_, i) => i === 29 ? 'now' : ''),
        datasets: [
          {
            label: '速度 (m/s)',
            data: new Array(30).fill(0),
            borderColor: '#3b82f6',
            backgroundColor: 'rgba(59,130,246,0.08)',
            borderWidth: 1.5, pointRadius: 0, tension: 0.4, fill: true, yAxisID: 'y'
          },
          {
            label: '电量 (%)',
            data: new Array(30).fill(87),
            borderColor: '#22c55e',
            backgroundColor: 'rgba(34,197,94,0.05)',
            borderWidth: 1.5, pointRadius: 0, tension: 0.4, fill: true, yAxisID: 'y2'
          }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: true,
        animation: { duration: 200 },
        plugins: { legend: { labels: { color: '#7e8597', font: { size: 10 }, boxWidth: 12, padding: 10 } } },
        scales: {
          x:  { ticks: { color: '#4a5168', font: { size: 9 } }, grid: { color: 'rgba(255,255,255,0.04)' } },
          y:  { type:'linear', position:'left',  min:0, max:1.5,
                ticks: { color:'#3b82f6', font:{size:9}, stepSize:0.5 },
                grid:  { color:'rgba(59,130,246,0.08)' },
                title: { display:true, text:'m/s', color:'#3b82f6', font:{size:9} } },
          y2: { type:'linear', position:'right', min:0, max:100,
                ticks: { color:'#22c55e', font:{size:9}, stepSize:25 },
                grid:  { drawOnChartArea:false },
                title: { display:true, text:'%', color:'#22c55e', font:{size:9} } }
        }
      }
    });
  }

  function _updateChart() {
    if (!_chart) return;
    const hist = StatusManager.getHistory();
    _chart.data.datasets[0].data = hist.speed;
    _chart.data.datasets[1].data = hist.battery;
    _chart.update('none');
  }

  // ══════════════════════════════════════════════
  // 时钟
  // ══════════════════════════════════════════════

  function _startClock() {
    function tick() {
      const n  = new Date();
      const el = document.getElementById('topbar-time');
      if (el) el.textContent =
        `${String(n.getHours()).padStart(2,'0')}:` +
        `${String(n.getMinutes()).padStart(2,'0')}:` +
        `${String(n.getSeconds()).padStart(2,'0')}`;
    }
    tick();
    setInterval(tick, 1000);
  }

  // ══════════════════════════════════════════════
  // UI 定时刷新（10Hz，满足验收要求）
  // ══════════════════════════════════════════════

  function _startUIRefresh() {
    // 模拟模式下同时推数据
    _uiTimer = setInterval(() => {
      if (_usingMock) StatusManager.simulateUpdate();
    }, CONFIG.UI_REFRESH_MS);

    _chartTimer = setInterval(_updateChart, CONFIG.CHART_REFRESH_MS);
  }

  // ══════════════════════════════════════════════
  // 公开 API
  // ══════════════════════════════════════════════

  /**
   * init() — 对应 dashboard.py::__init__ + run()
   * register_control_panel 在此内部完成
   */
  function init() {
    _startClock();
    _initChart();

    // register_control_panel：将 sendCommand 注入 ControlPanel
    ControlPanel.init(_sendCommand);

    // ObstacleAvoidance 初始化，注入子指令发送函数
    ObstacleAvoidance.init(ControlPanel.sendAvoidCmd);

    // Observer：订阅状态更新（示例：其他模块可在此扩展）
    StatusManager.subscribeStatus((status) => {
      // 状态变更时可在此做额外处理，如更新标题栏
      if (status.error_code !== 0) {
        LogSystem.error('StatusManager', `机器人故障码: ${status.error_code}`);
      }
    });

    LogSystem.info('Dashboard', '系统启动中...');
    LogSystem.info('Dashboard', `尝试连接 ${CONFIG.WS_URL}`);
    _connectWS();
    _startUIRefresh();

    LogSystem.info('Dashboard', '所有模块初始化完成（刷新率10Hz）');
    LogSystem.info('Dashboard',
      '支持动作: walk_straight / turn_in_place(L/R) / wave / squat / avoid | ESC=紧急停止'
    );
  }

  // HTML onclick 入口
  function sendAction(actionKey) { ControlPanel.executeAction(actionKey); }
  function emergencyStop()       { ControlPanel.emergencyStop(); }

  // 任务控制按钮（供HTML调用，task_id 从界面输入或写死当前任务）
  function startTask(taskId)  { ControlPanel.startTask(taskId  || 'current'); }
  function stopTask(taskId)   { ControlPanel.stopTask(taskId   || 'current'); }
  function pauseTask(taskId)  { ControlPanel.pauseTask(taskId  || 'current'); }
  function resumeTask(taskId) { ControlPanel.resumeTask(taskId || 'current'); }

  return {
    init,
    sendAction, emergencyStop,
    startTask, stopTask, pauseTask, resumeTask,
    updateVisionFrame,
    CONFIG  // 暴露给调试用
  };
})();

// ── 页面就绪后启动 ──
document.addEventListener('DOMContentLoaded', () => { Dashboard.init(); });
