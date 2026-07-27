/* 配置报价CPQ —— 消息系统（四个页面共用）
 *
 * 记录并提醒任务流事件：自己发出任务（task_sent）、收到别人转交的任务（task_received）、
 * 自己转交的任务被领取（task_claimed）。数据来自 /wf/messages。
 *
 * 用法：页面左下角（设置与账号按钮之上）放：
 *   <div class="nav-icon" id="cpqMsgBtn" onclick="cpqMsg.open()">
 *     <i class="ti ti-bell"></i><span class="nav-tooltip">消息</span>
 *   </div>
 * 本脚本自动挂未读红点、轮询新消息并弹出右下角提醒。依赖 cpq_auth.js（登录态与带令牌的 api）。
 */
(function () {
  'use strict';

  var POLL_MS = 20000;          // 轮询间隔
  var state = { list: [], unread: 0, seen: null, timer: null, open: false };

  function api(p, o) {
    if (!window.cpqAuth) return Promise.reject(new Error('未加载登录模块'));
    return window.cpqAuth.api(p, o);
  }
  function user() { return (window.cpqAuth && window.cpqAuth.user()) || null; }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function fmt(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso).replace('T', ' ').slice(0, 16);
    var s = (Date.now() - d.getTime()) / 1000;
    if (s < 60) return '刚刚';
    if (s < 3600) return Math.floor(s / 60) + ' 分钟前';
    if (s < 86400) return Math.floor(s / 3600) + ' 小时前';
    if (s < 604800) return Math.floor(s / 86400) + ' 天前';
    var p = function (n) { return (n < 10 ? '0' : '') + n; };
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) +
      ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
  }

  var ICON = {
    task_sent: ['ti-send', '#6366f1'],
    task_received: ['ti-inbox', '#16a34a'],
    task_claimed: ['ti-user-check', '#f59e0b'],
  };

  /* ---------------- 样式 ---------------- */
  var CSS = [
    '#cpqMsgBtn{position:relative;}',
    '.cpq-msg-dot{position:absolute;top:4px;right:4px;min-width:15px;height:15px;padding:0 3px;',
    'border-radius:9px;background:#ef4444;color:#fff;font-size:10px;line-height:15px;text-align:center;',
    'font-weight:700;box-sizing:border-box;}',
    '.cpq-msg-mask{position:fixed;inset:0;z-index:99998;}',
    '.cpq-msg-panel{position:fixed;left:72px;bottom:16px;width:340px;max-height:70vh;z-index:99999;',
    'background:var(--bg-primary,#fff);color:var(--text-primary,#0f172a);border-radius:12px;',
    'box-shadow:0 18px 48px rgba(0,0,0,.26);display:flex;flex-direction:column;overflow:hidden;',
    'border:1px solid var(--border-color,#e2e8f0);}',
    '.cpq-msg-hd{display:flex;align-items:center;justify-content:space-between;padding:12px 14px;',
    'border-bottom:1px solid var(--border-color,#e2e8f0);font-size:14px;font-weight:650;}',
    '.cpq-msg-hd a{font-size:12px;font-weight:400;color:var(--color-primary,#6366f1);cursor:pointer;}',
    '.cpq-msg-list{overflow-y:auto;padding:4px 0;}',
    '.cpq-msg-item{display:flex;gap:10px;padding:10px 14px;cursor:pointer;border-left:3px solid transparent;}',
    '.cpq-msg-item:hover{background:var(--bg-tertiary,#f1f5f9);}',
    '.cpq-msg-item.unread{border-left-color:var(--color-primary,#6366f1);background:rgba(99,102,241,.05);}',
    '.cpq-msg-ico{flex:0 0 28px;height:28px;border-radius:50%;display:flex;align-items:center;',
    'justify-content:center;background:var(--bg-tertiary,#f1f5f9);font-size:15px;}',
    '.cpq-msg-t{font-size:13px;font-weight:600;margin-bottom:2px;}',
    '.cpq-msg-b{font-size:12px;color:var(--text-secondary,#64748b);line-height:1.5;}',
    '.cpq-msg-time{font-size:11px;color:var(--text-tertiary,#94a3b8);margin-top:3px;}',
    '.cpq-msg-empty{padding:28px 14px;text-align:center;font-size:12px;color:var(--text-secondary,#64748b);}',
    '.cpq-toast-wrap{position:fixed;right:18px;bottom:18px;z-index:100000;display:flex;',
    'flex-direction:column;gap:8px;}',
    '.cpq-toast{width:300px;background:var(--bg-primary,#fff);color:var(--text-primary,#0f172a);',
    'border-radius:10px;box-shadow:0 12px 32px rgba(0,0,0,.24);padding:11px 13px;display:flex;gap:10px;',
    'cursor:pointer;border-left:3px solid var(--color-primary,#6366f1);animation:cpqToastIn .25s ease;}',
    '@keyframes cpqToastIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}',
  ].join('');

  function injectCss() {
    if (document.getElementById('cpqMsgCss')) return;
    var st = document.createElement('style');
    st.id = 'cpqMsgCss';
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  /* ---------------- 未读红点 ---------------- */
  function syncBadge() {
    var btn = document.getElementById('cpqMsgBtn');
    if (!btn) return;
    var dot = btn.querySelector('.cpq-msg-dot');
    if (!state.unread) { if (dot) dot.remove(); return; }
    if (!dot) {
      dot = document.createElement('span');
      dot.className = 'cpq-msg-dot';
      btn.appendChild(dot);
    }
    dot.textContent = state.unread > 99 ? '99+' : String(state.unread);
  }

  /* ---------------- 面板 ---------------- */
  function close() {
    var p = document.getElementById('cpqMsgPanel');
    var m = document.getElementById('cpqMsgMask');
    if (p) p.remove();
    if (m) m.remove();
    state.open = false;
  }

  function itemHtml(m) {
    var ic = ICON[m.msg_type] || ['ti-bell', '#64748b'];
    return '<div class="cpq-msg-item' + (m.is_read ? '' : ' unread') + '" data-id="' + esc(m.message_id) +
      '" data-session="' + esc(m.session_id || '') + '">' +
      '<div class="cpq-msg-ico"><i class="ti ' + ic[0] + '" style="color:' + ic[1] + '"></i></div>' +
      '<div style="flex:1;min-width:0">' +
      '<div class="cpq-msg-t">' + esc(m.title) + '</div>' +
      '<div class="cpq-msg-b">' + esc(m.body) + '</div>' +
      '<div class="cpq-msg-time">' + esc(fmt(m.created_at)) + '</div>' +
      '</div></div>';
  }

  function render() {
    var list = document.getElementById('cpqMsgList');
    if (!list) return;
    list.innerHTML = state.list.length
      ? state.list.map(itemHtml).join('')
      : '<div class="cpq-msg-empty">暂无消息。<br>转交或收到报价任务时会记录在这里。</div>';
    list.querySelectorAll('.cpq-msg-item').forEach(function (el) {
      el.onclick = function () {
        var sid = el.dataset.session;
        markRead([el.dataset.id]);
        if (!sid) return;
        // 跳到该报价卡片的工作台
        try { sessionStorage.setItem('cpq:openSession', sid); } catch (e) {}
        window.location.href = encodeURI('确认需求解析结果.html');
      };
    });
  }

  function open() {
    if (state.open) { close(); return; }
    if (!user()) { if (window.cpqAuth) window.cpqAuth.open(); return; }
    injectCss();
    var mask = document.createElement('div');
    mask.className = 'cpq-msg-mask';
    mask.id = 'cpqMsgMask';
    mask.onmousedown = close;
    var panel = document.createElement('div');
    panel.className = 'cpq-msg-panel';
    panel.id = 'cpqMsgPanel';
    // 贴着按钮右侧弹出（各页导航栏宽度不同、首页还能展开，按实际位置算更稳）
    var btn = document.getElementById('cpqMsgBtn');
    if (btn) {
      var r = btn.getBoundingClientRect();
      panel.style.left = Math.round(r.right + 12) + 'px';
      panel.style.bottom = Math.max(12, Math.round(window.innerHeight - r.bottom - 8)) + 'px';
    }
    panel.innerHTML =
      '<div class="cpq-msg-hd"><span>消息</span>' +
      '<a id="cpqMsgReadAll">全部标为已读</a></div>' +
      '<div class="cpq-msg-list" id="cpqMsgList"></div>';
    document.body.appendChild(mask);
    document.body.appendChild(panel);
    state.open = true;
    document.getElementById('cpqMsgReadAll').onclick = function () { markRead(null); };
    render();
    refresh();   // 打开时拉一次最新
  }

  function markRead(ids) {
    api('/wf/messages/read', { method: 'POST', body: { ids: ids || [] } })
      .then(function () {
        (state.list || []).forEach(function (m) {
          if (!ids || ids.indexOf(m.message_id) >= 0) m.is_read = true;
        });
        state.unread = state.list.filter(function (m) { return !m.is_read; }).length;
        syncBadge();
        render();
      })
      .catch(function () {});
  }

  /* ---------------- 弹出提醒 ---------------- */
  function toast(m) {
    injectCss();
    var wrap = document.getElementById('cpqToastWrap');
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.className = 'cpq-toast-wrap';
      wrap.id = 'cpqToastWrap';
      document.body.appendChild(wrap);
    }
    var ic = ICON[m.msg_type] || ['ti-bell', '#64748b'];
    var el = document.createElement('div');
    el.className = 'cpq-toast';
    el.innerHTML =
      '<div class="cpq-msg-ico"><i class="ti ' + ic[0] + '" style="color:' + ic[1] + '"></i></div>' +
      '<div style="flex:1;min-width:0">' +
      '<div class="cpq-msg-t">' + esc(m.title) + '</div>' +
      '<div class="cpq-msg-b">' + esc(m.body) + '</div></div>';
    el.onclick = function () {
      el.remove();
      if (m.session_id) {
        try { sessionStorage.setItem('cpq:openSession', m.session_id); } catch (e) {}
        window.location.href = encodeURI('确认需求解析结果.html');
      } else { open(); }
    };
    wrap.appendChild(el);
    setTimeout(function () { if (el.parentElement) el.remove(); }, 8000);
  }

  /* ---------------- 轮询 ---------------- */
  function refresh(quiet) {
    if (!user()) {
      state.list = []; state.unread = 0; state.seen = null;
      syncBadge();
      return Promise.resolve();
    }
    return api('/wf/messages?limit=50').then(function (d) {
      var list = d.messages || [];
      // 首次加载只建立基线，不刷屏；之后只对新增的未读消息弹提醒
      if (state.seen !== null && !quiet) {
        list.filter(function (m) { return !m.is_read && !state.seen[m.message_id]; })
            .slice(0, 3).reverse().forEach(toast);
      }
      state.seen = {};
      list.forEach(function (m) { state.seen[m.message_id] = 1; });
      state.list = list;
      state.unread = d.unread || 0;
      syncBadge();
      if (state.open) render();
    }).catch(function () {});
  }

  function startPolling() {
    if (state.timer) clearInterval(state.timer);
    state.timer = setInterval(function () { refresh(); }, POLL_MS);
  }

  window.cpqMsg = {
    open: open, close: close, refresh: refresh,
    unread: function () { return state.unread; },
    /** 供页面在自己触发任务流后立即刷新（不必等轮询） */
    ping: function () { return refresh(true); },
  };

  // 登录态就绪/变化后建立基线并开始轮询
  document.addEventListener('cpq-auth-ready', function () { refresh(true).then(startPolling); });
  document.addEventListener('cpq-auth-change', function () {
    state.seen = null;
    refresh(true).then(startPolling);
  });
})();
