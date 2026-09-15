/* 项目会话时间线 —— 父壳与九个阶段页共用的**唯一**顺序 / 去重实现。
 *
 * 解决的问题（用户反馈）：同一条会话线程里，「有的卡片永远钉在最下面，有的按顺序从上
 * 到下」；而且重进项目后只剩下 Agent 对话，任务卡与各阶段页的过程文字全都不见了。
 * 现在所有条目（Agent 回放消息 + 项目时间线事件）都走这里的同一套规则排成一条列表：
 *
 *   - 排序：(ts, seq) 稳定升序 —— ts 相同用 seq 兜底，都缺就保持原顺序；
 *   - 合并：Agent JSONL 消息与本地事件交错排序，不再是「消息全在前、事件全在后」；
 *   - 幂等：同 key 只留一条（位置取先出现的那条、内容取最后写入的那条）；同一 task.id
 *     只留一张卡，进度行按行去重追加、状态 / 错误就地更新；
 *   - 归属：forShell 排除 source=board 的过程文字（它们归阶段页渲染，避免两处重复），
 *     forStage 只返回本阶段 source=board 的条目。
 *
 * 纯函数、不碰 DOM、不发请求：后端 store.append_session_event() 用同一套合并口径，
 * 「左栏渲染出来的卡」和「落库里的卡」因此不会漂移。可在 node 下直接执行。
 */
(function (root) {
  'use strict';

  const DEFAULT_KIND = 'session-note';

  function isPlain(value) {
    return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
  }

  function seqOf(entry) {
    const value = Number(entry && entry.seq);
    return Number.isFinite(value) ? value : 0;
  }

  /* 统一条目形状：补齐 kind（Agent 回放消息只有 type），不改调用方传进来的对象。 */
  function normalize(raw) {
    const entry = Object.assign({}, isPlain(raw) ? raw : {});
    const kind = String(entry.kind || entry.type || '').trim();
    entry.kind = kind || DEFAULT_KIND;
    if (entry.seq === null || entry.seq === undefined || entry.seq === '') delete entry.seq;
    return entry;
  }

  /* 同一张卡的身份：优先幂等 key，其次是任务 id。没有身份的条目（普通消息）不去重。 */
  function cardKey(entry) {
    const key = String((entry && entry.key) || '').trim();
    if (key) return `key:${key}`;
    const taskId = String((entry && entry.task && entry.task.id) || '').trim();
    return taskId ? `task:${taskId}` : '';
  }

  /* 任务卡合并：进度行按行去重追加（已有行不重复、不覆盖），状态 / 错误就地更新。 */
  function mergeTask(previous, incoming) {
    const merged = Object.assign({}, isPlain(previous) ? previous : {});
    Object.keys(isPlain(incoming) ? incoming : {}).forEach(field => {
      const value = incoming[field];
      if (value === null || value === undefined || value === '') return;
      if (Array.isArray(value) && !value.length) return;
      merged[field] = value;
    });
    const steps = [];
    [].concat((isPlain(previous) && previous.steps) || [],
              (isPlain(incoming) && incoming.steps) || []).forEach(row => {
      const text = String(row);
      if (text && steps.indexOf(text) < 0) steps.push(text);
    });
    merged.steps = steps;
    return merged;
  }

  /* (ts, seq) 稳定升序：ts 相同用 seq 兜底；都缺时保持原顺序（调用方再按原下标兜底）。 */
  function compare(left, right) {
    const leftTs = left && left.ts ? String(left.ts) : '';
    const rightTs = right && right.ts ? String(right.ts) : '';
    if (leftTs !== rightTs) {
      if (!leftTs) return 1;          // 没有时间戳的排在最后，不插到历史中间
      if (!rightTs) return -1;
      return leftTs < rightTs ? -1 : 1;
    }
    return seqOf(left) - seqOf(right);
  }

  function sortStable(list) {
    return (list || [])
      .map((entry, index) => ({ entry: entry, index: index }))
      .sort((a, b) => compare(a.entry, b.entry) || (a.index - b.index))
      .map(item => item.entry);
  }

  /* 同 key / 同任务只留一条：位置取先出现的那条，内容取最后写入的那条。 */
  function dedupe(list) {
    const out = [];
    const seen = new Map();
    (list || []).forEach(raw => {
      const entry = normalize(raw);
      const key = cardKey(entry);
      if (!key) { out.push(entry); return; }
      if (!seen.has(key)) { seen.set(key, out.length); out.push(entry); return; }
      const at = seen.get(key);
      const previous = out[at];
      const merged = Object.assign({}, previous, entry);
      merged.seq = previous.seq;      // 就地更新：序号与位置都不动
      if (isPlain(previous.task) && isPlain(entry.task)) {
        merged.task = mergeTask(previous.task, entry.task);
      }
      out[at] = merged;
    });
    return out;
  }

  /* 追加一条后返回**新数组**（不改入参）。 */
  function append(list, entry) {
    return sortStable(dedupe((list || []).concat([entry])));
  }

  /* Agent 回放消息 + 项目时间线事件 → 一条有序列表。 */
  function merge(input) {
    const payload = isPlain(input) ? input : {};
    const messages = (payload.messages || []).map(normalize);
    const events = (payload.events || []).map(normalize);
    return sortStable(dedupe(messages.concat(events)));
  }

  /* 左栏渲染集：Agent 消息 + 任务卡 + tech_ui 卡 + shell note；
     排除 source=board 的过程文字 —— 那些归阶段页自己的线程。 */
  function forShell(input) {
    return merge(input).filter(entry =>
      !(entry.kind === 'session-note' && entry.source === 'board'));
  }

  /* 阶段页渲染集：只要本阶段、由看板产生的条目。 */
  function forStage(input) {
    const payload = isPlain(input) ? input : {};
    const stage = String(payload.stage || '');
    const rows = (payload.events || []).map(normalize).filter(entry =>
      String(entry.stage || '') === stage && entry.source === 'board');
    return sortStable(dedupe(rows));
  }

  /* 任务进度合并：同一 task.id 只留一张卡（位置不动、进度行按行去重追加、状态就地更新）；
     还没有卡时新建一张。payload 就是看板 task-progress 的 detail。 */
  function applyTaskProgress(list, payload) {
    const items = (list || []).map(normalize);
    const detail = isPlain(payload) ? payload : {};
    const taskId = String(detail.id || detail.task_id || '').trim();
    if (!taskId) return items.slice();
    const index = items.findIndex(entry =>
      String((entry.task && entry.task.id) || '') === taskId);
    if (index < 0) {
      const fresh = normalize({
        kind: 'task', source: detail.source || 'board', stage: detail.stage || '',
        ts: detail.ts || new Date().toISOString(),
        text: detail.label || '',
        task: { id: taskId, label: detail.label || '', status: detail.status || 'running',
                steps: (detail.steps || []).slice(), error: detail.error || '' },
      });
      return append(items, fresh);
    }
    const card = Object.assign({}, items[index]);
    card.task = mergeTask(items[index].task, {
      id: taskId, label: detail.label || '', status: detail.status || '',
      steps: (detail.steps || []).slice(), error: detail.error || '',
    });
    if (detail.label) card.text = detail.label;
    const next = items.slice();
    next[index] = card;
    return next;
  }

  root.TechSessionTimeline = {
    normalize: normalize,
    merge: merge,
    append: append,
    dedupe: dedupe,
    applyTaskProgress: applyTaskProgress,
    forShell: forShell,
    forStage: forStage,
    // 供父壳 / 阶段页复用同一套任务卡合并口径（不与上面重复实现）。
    mergeTask: mergeTask,
  };
})(typeof window !== 'undefined' ? window : this);
