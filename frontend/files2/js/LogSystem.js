/**
 * LogSystem.js
 * 负责人：成员B
 * 对应接口：src/status_ui/log_system.py → LogSystem
 *
 * ── 接口对齐 ──
 *   info(source, message)
 *   warning(source, message)
 *   error(source, message)
 *   debug(source, message)
 *   get_log_file_path()   → Web端改为 export() 导出TXT
 *
 * ── 验收要求 ──
 *   支持按级别过滤（ERROR / WARN / INFO / DEBUG）
 *   最多保留 500 条（防卡顿），可导出TXT
 *   来源字段 source 显示在日志行
 */

const LogSystem = (() => {

  // 全量日志（内存存储，对应文件滚动存储）
  const _logs = [];
  const MAX_LOGS = 500;  // 对应"10MB/文件，保留5个备份"在Web端的近似

  let _currentFilter = 'ALL';

  // ── 私有 ──

  function _timestamp() {
    const n  = new Date();
    const hh = String(n.getHours()).padStart(2, '0');
    const mm = String(n.getMinutes()).padStart(2, '0');
    const ss = String(n.getSeconds()).padStart(2, '0');
    const ms = String(n.getMilliseconds()).padStart(3, '0');
    return `${hh}:${mm}:${ss}.${ms}`;
  }

  function _createLine(entry) {
    const div = document.createElement('div');
    div.className  = `log-line log-${entry.level}`;
    div.dataset.level = entry.level;
    // source 列
    div.innerHTML = `
      <span class="log-time">${entry.time}</span>
      <span class="log-level">${entry.level}</span>
      <span class="log-msg"><span class="log-source">[${entry.source}]</span> ${entry.msg}</span>
    `;
    return div;
  }

  function _append(entry) {
    const area = document.getElementById('log-area');
    if (!area) return;
    if (_currentFilter !== 'ALL' && entry.level !== _currentFilter) return;

    area.appendChild(_createLine(entry));
    // DOM节点数量限制
    while (area.children.length > MAX_LOGS) area.removeChild(area.firstChild);
    area.scrollTop = area.scrollHeight;
  }

  // ── 公开 API（对应 log_system.py 接口）──

  /**
   * 核心写入方法（内部使用）
   */
  function log(level, message, source) {
    source = source || 'System';
    const entry = {
      level,
      source,
      msg:       message,
      time:      _timestamp(),
      timestamp: Date.now()
    };
    _logs.push(entry);
    // 内存上限（最多保留MAX_LOGS条）
    if (_logs.length > MAX_LOGS) _logs.shift();
    _append(entry);
  }

  /**
   * info(source, message)   ← 对应 LogSystem.info()
   * 也支持 info(message) 的单参数调用（内部兼容）
   */
  function info(sourceOrMsg, message) {
    if (message === undefined) { log('INFO',  sourceOrMsg, 'System'); }
    else                       { log('INFO',  message, sourceOrMsg); }
  }

  function warning(sourceOrMsg, message) {
    if (message === undefined) { log('WARN',  sourceOrMsg, 'System'); }
    else                       { log('WARN',  message, sourceOrMsg); }
  }

  // 内部简写（兼容旧代码 LogSystem.warn(msg)）
  const warn = warning;

  function error(sourceOrMsg, message) {
    if (message === undefined) { log('ERROR', sourceOrMsg, 'System'); }
    else                       { log('ERROR', message, sourceOrMsg); }
  }

  function debug(sourceOrMsg, message) {
    if (message === undefined) { log('DEBUG', sourceOrMsg, 'System'); }
    else                       { log('DEBUG', message, sourceOrMsg); }
  }

  /**
   * getLogs(count) ← 对应 get_logs()
   * 返回最近N条日志对象列表
   */
  function getLogs(count) {
    count = count || 100;
    return _logs.slice(-count).map(e => ({ ...e }));
  }

  /**
   * get_log_file_path() → Web端实现为导出TXT文件
   */
  function exportLog() {
    const lines = _logs.map(e =>
      `[${e.time}] [${e.level.padEnd(5)}] [${e.source}] ${e.msg}`
    );
    const blob = new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = `robot_log_${new Date().toISOString().slice(0,19).replace(/:/g,'-')}.txt`;
    a.click();
    URL.revokeObjectURL(url);
    info('LogSystem', '日志已导出');
  }

  /**
   * filter(level) — 按级别过滤显示
   * 验收要求：支持 ERROR / WARNING / INFO / DEBUG 过滤
   */
  function filter(level) {
    _currentFilter = level;
    document.querySelectorAll('.filter-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.level === level);
    });
    const area = document.getElementById('log-area');
    if (!area) return;
    area.innerHTML = '';
    const filtered = level === 'ALL' ? _logs : _logs.filter(e => e.level === level);
    filtered.slice(-MAX_LOGS).forEach(e => area.appendChild(_createLine(e)));
    area.scrollTop = area.scrollHeight;
  }

  function clear() {
    _logs.length = 0;
    const area = document.getElementById('log-area');
    if (area) area.innerHTML = '';
    info('LogSystem', '日志已清空');
  }

  return {
    log, info, warning, warn, error, debug,
    getLogs,
    filter, clear,
    export: exportLog
  };
})();
