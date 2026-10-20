"""Explicit, reversible project maintenance. No automatic cleanup on import or startup."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

INDEX_NAME = '.archived-projects'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     default=str).encode()).hexdigest()


def read_index(history_dir):
    path = Path(history_dir) / INDEX_NAME
    if not path.exists():
        return {'archived_sessions': []}
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or not isinstance(value.get('archived_sessions'), list):
        raise ValueError('报价归档索引损坏，拒绝当作空索引继续')
    return value


def write_index(history_dir, value):
    directory = Path(history_dir)
    directory.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.archive-write-', dir=directory)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, directory / INDEX_NAME)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def build_plan(cards, tasks, metas, history_ids, keep_quote, keep_tech):
    for ids in (keep_quote, keep_tech):
        if not ids or len(ids) != len(set(ids)) or any(
                not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', str(i)) for i in ids):
            raise ValueError('必须指定非空、无重复、安全的精确保留ID')
    if not set(keep_quote) <= {r['session_id'] for r in cards}:
        raise ValueError('保留报价卡片不存在')
    if not set(keep_quote) <= set(history_ids):
        raise ValueError('保留报价的历史文件缺失')
    if not set(keep_tech) <= {r['project_id'] for r in metas if not r.get('deleted_at')}:
        raise ValueError('保留工艺项目不存在或已归档')
    return {
        'keep_quote': list(keep_quote), 'keep_tech': list(keep_tech),
        'archive_cards': [r for r in cards if r['session_id'] not in keep_quote
                          and r['overall_status'] != 'archived'],
        'archive_history': [sid for sid in history_ids if sid not in keep_quote],
        'archive_tasks': [r for r in tasks if r['status'] in ('open', 'claimed') or
                          (r['status'] == 'cancelled' and r.get('replaced_by_task_id'))],
        'archive_tech': [r['project_id'] for r in metas if not r.get('deleted_at')
                         and r['project_id'] not in keep_tech],
    }


def workflow_state(conn):
    cards = conn.execute('SELECT card_id, session_id, overall_status FROM cpq_wf_card ORDER BY card_id')
    cards = [dict(zip(('card_id','session_id','overall_status'), row)) for row in cards.fetchall()]
    tasks = conn.execute('SELECT task_id, status, replaced_by_task_id FROM cpq_wf_task ORDER BY task_id')
    tasks = [dict(zip(('task_id','status','replaced_by_task_id'), row)) for row in tasks.fetchall()]
    return cards, tasks


def apply_snapshot(conn, store, history_dir, snapshot, actor):
    """Application maintenance command; caller must already have durable private backup.

    PG changes commit together. Files/meta are compensated on failure; backup remains
    authoritative for manual recovery if compensation itself fails.
    """
    plan = snapshot['plan']
    changed_meta = []
    index_written = False
    try:
        conn.execute('LOCK TABLE cpq_wf_card, cpq_wf_task IN SHARE ROW EXCLUSIVE MODE')
        cards, tasks = workflow_state(conn)
        if digest([cards,tasks]) != snapshot['workflow_digest']:
            raise ValueError('任务或卡片状态已变化，请重新生成计划，未归档任何项目')
        current_metas = store.list_projects(include_archived=True, lightweight=True)
        # Compare only persisted metadata: list_projects adds derived owner display names.
        current_metas = [store.load_meta(m['project_id']) for m in current_metas]
        if digest(sorted(current_metas, key=lambda m:m['project_id'])) != snapshot['meta_digest']:
            raise ValueError('技术项目状态已变化，请重新生成计划')
        current_history = sorted(p.stem for p in Path(history_dir).glob('*.json'))
        if current_history != snapshot['history_ids'] or read_index(history_dir) != snapshot['index']:
            raise ValueError('历史索引已变化，请重新生成计划')
        for meta in current_metas:
            active = [t for t in store.list_tasks(meta['project_id'])
                      if t.get('status') in ('running','queued','pending','cancelling')]
            if active:
                raise ValueError('仍有运行中的计算任务，暂不归档：' + meta['project_id'])
        for row in plan['archive_cards']:
            cur=conn.execute("UPDATE cpq_wf_card SET overall_status='archived' "
                             'WHERE card_id=%s AND overall_status=%s',
                             (row['card_id'],row['overall_status']))
            if cur.rowcount != 1:
                raise ValueError('报价状态冲突')
        for row in plan['archive_tasks']:
            cur=conn.execute("UPDATE cpq_wf_task SET status='archived' "
                             'WHERE task_id=%s AND status=%s',(row['task_id'],row['status']))
            if cur.rowcount != 1:
                raise ValueError('待办状态冲突')
        for pid in plan['archive_tech']:
            changed_meta.append(pid)
            store.archive_project(pid, author=actor)
        archived = set(snapshot['index']['archived_sessions']) | set(plan['archive_history'])
        archived.difference_update(plan['keep_quote'])
        write_index(history_dir, {'archived_sessions':sorted(archived), 'batch':snapshot['batch']})
        index_written = True
        conn.commit()
    except Exception:
        conn.rollback()
        originals={m['project_id']:m for m in snapshot['metas']}
        for pid in changed_meta:
            store._meta().put_meta(pid, originals[pid])
        if index_written:
            write_index(history_dir, snapshot['index'])
        raise
