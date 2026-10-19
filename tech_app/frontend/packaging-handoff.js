/* 包装零件工艺确认与按人交接，复用现有派发弹窗视觉，不负责导航。 */
(function () {
  'use strict';
  const states = new Map(), revisions = new Map(), pending = new Map(), changes = new Map();
  let dialog = null;
  const endpoint = pid => `/api/projects/${encodeURIComponent(pid)}/integration/process`;
  async function request(url, options) {
    const response = await fetch(url, options);
    const value = await response.json();
    if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail
      : value.detail?.message || value.message || '操作失败，请重试');
    return value;
  }
  function notify() { window.TechBoardRuntime?.refreshState?.(); }
  function invalidate(pid) {
    revisions.set(pid, (revisions.get(pid) || 0) + 1);
    states.delete(pid); notify();
  }
  async function refresh(pid) {
    if (!pid) return {};
    const revision = revisions.get(pid) || 0;
    const existing = pending.get(pid);
    if (existing?.revision === revision) return existing.promise;
    const entry = {revision};
    entry.promise = request(endpoint(pid) + '/confirmation').then(value => {
      if ((revisions.get(pid) || 0) === revision) { states.set(pid, value); notify(); }
      return value;
    }).catch(error => {
      if ((revisions.get(pid) || 0) === revision) { states.set(pid, {error: error.message}); notify(); }
      throw error;
    }).finally(() => { if (pending.get(pid) === entry) pending.delete(pid); });
    pending.set(pid, entry);
    return entry.promise;
  }
  async function confirm(pid) {
    await request(endpoint(pid) + '/confirm', {method: 'POST'});
    return refresh(pid);
  }
  function current(pid) { return changes.get(pid) ? {} : states.get(pid) || {}; }
  function activate(pid) {
    if (dialog && dialog.dataset.projectId !== String(pid || '')) dialog.dismiss();
  }
  function beginChange(pid) { changes.set(pid, (changes.get(pid) || 0) + 1); invalidate(pid); }
  function endChange(pid) {
    changes.set(pid, Math.max(0, (changes.get(pid) || 0) - 1)); invalidate(pid);
    if (!changes.get(pid)) refresh(pid).catch(()=>{});
  }

  function open(options) {
    const pid = options.projectId;
    activate(pid);
    if (dialog) { dialog.focus(); return {ok: true, result: {message: '请选择接收人员'}}; }
    const mask = document.createElement('div');
    mask.className = 'ai-send-mask'; mask.id = 'packagingPricingDialog';
    mask.dataset.projectId = String(pid);
    mask.tabIndex = -1;
    mask.innerHTML = `<div class="ai-send-box" role="dialog" aria-modal="true" aria-labelledby="pricingSendTitle">
      <div class="ai-send-head"><h3 id="pricingSendTitle">发送至报价人员</h3><p>选择接收当前零件工艺并核算成本的人员。</p></div>
      <div class="ai-send-body"><div class="ai-send-row"><label for="pricingSearch">搜索报价人员</label>
        <input id="pricingSearch" placeholder="输入姓名或账号" autocomplete="off"></div>
        <div class="ai-send-row"><label for="pricingUser">接收人员</label><select id="pricingUser" size="5" aria-label="接收人员"></select></div></div>
      <div class="ai-send-msg" role="status"></div><div class="ai-send-foot">
        <button class="cancel" type="button">取消</button><button class="cancel retry" type="button" hidden>重新读取</button>
        <button class="send" type="button" disabled>发送</button></div></div>`;
    document.body.append(mask); dialog = mask;
    const find = selector => mask.querySelector(selector);
    const message = find('.ai-send-msg'), select = find('#pricingUser'), send = find('.send');
    let users = [], busy = false, loadSequence = 0;
    const origin = document.activeElement;
    const isCurrent = () => mask.isConnected && (!options.isCurrent || options.isCurrent());
    const close = () => { if (busy) return; loadSequence++; mask.remove(); dialog = null; document.removeEventListener('keydown', keys); origin?.focus?.(); };
    const keys = event => {
      if (event.key === 'Escape') close();
      if (event.key === 'Tab') {
        const focusable = [...mask.querySelectorAll('input,select,button')].filter(el => !el.disabled && !el.hidden);
        const index = focusable.indexOf(document.activeElement);
        if (event.shiftKey && index <= 0) { event.preventDefault(); focusable.at(-1)?.focus(); }
        else if (!event.shiftKey && index === focusable.length - 1) { event.preventDefault(); focusable[0]?.focus(); }
      }
    };
    mask.dismiss = () => { busy = false; close(); };
    document.addEventListener('keydown', keys);
    find('.cancel').onclick = close;
    mask.onmousedown = event => { if (event.target === mask) close(); };
    const fill = () => {
      const previous = select.value, query = find('#pricingSearch').value.trim().toLowerCase();
      select.replaceChildren();
      const placeholder = document.createElement('option'); placeholder.value = ''; placeholder.textContent = '请选择报价人员';
      select.append(placeholder);
      users.filter(user => `${user.display_name || ''} ${user.username || ''}`.toLowerCase().includes(query))
        .forEach(user => { const option = document.createElement('option'); option.value = String(user.user_id);
          option.textContent = `${user.display_name || user.username}（${user.username || '报价人员'}）`; select.append(option); });
      select.value = [...select.options].some(option => option.value === previous) ? previous : '';
      send.disabled = busy || !select.value;
    };
    const load = async () => {
      const sequence = ++loadSequence;
      message.textContent = '正在读取报价人员…'; find('.retry').hidden = true; send.disabled = true;
      try {
        const auth = window.cpqAuth || window.parent?.cpqAuth;
        if (!auth?.api) throw new Error('登录模块尚未就绪，请重试');
        const result = await auth.api('/auth/users');
        if (sequence !== loadSequence || !isCurrent()) return;
        users = (result.users || []).filter(user => user.role_code === 'finance_mgr'
          && (!user.status || user.status === 'active'));
        fill(); message.textContent = users.length ? '' : '暂无可选报价人员';
        find('.retry').hidden = users.length > 0;
      } catch (error) {
        if (sequence !== loadSequence || !isCurrent()) return;
        users = []; fill(); message.textContent = error.message; find('.retry').hidden = false;
      }
    };
    find('.retry').onclick = load; find('#pricingSearch').oninput = fill;
    select.onchange = () => { send.disabled = busy || !select.value; };
    send.onclick = async () => {
      if (busy || !select.value) return;
      if (!isCurrent()) { message.textContent = '项目已切换，请在当前项目重新发送'; return; }
      busy = true; send.disabled = true; select.disabled = true; find('.cancel').disabled = true;
      message.textContent = '正在发送…';
      const taskId = `sendPartsToPricing:${pid}:${Date.now()}`;
      const publish = (event, payload) => window.TechBoardRuntime?.publish?.(event, 'sendPartsToPricing',
        {taskId, prompt: '请将已确认工艺发送至所选报价人员。', ...payload});
      publish('task-progress', {status: 'running', progress: '正在交接工艺…'});
      try {
        const result = await request(`/api/projects/${encodeURIComponent(pid)}/integration/send-to-finance`, {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({target_type: 'user', target_user_id: select.value})});
        if (!isCurrent()) return;
        publish('task-completed', {status: 'succeeded', message: '已发送至报价人员', result});
        options.onSent?.(result); busy = false; close();
      } catch (error) {
        if (!isCurrent()) return;
        message.textContent = error.message; publish('task-failed', {status: 'failed', error: error.message});
      } finally {
        busy = false; select.disabled = false; find('.cancel').disabled = false;
        send.disabled = !select.value;
      }
    };
    load(); find('#pricingSearch').focus();
    return {ok: true, result: {message: '请选择接收人员'}};
  }
  window.PackagingHandoff = {open, refresh, confirm, current, invalidate, beginChange, endChange, activate};
})();
