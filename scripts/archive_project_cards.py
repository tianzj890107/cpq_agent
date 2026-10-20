#!/usr/bin/env python3
"""Prepare private backup + explicit plan; apply only with its SHA256 confirmation.

Example: python scripts/archive_project_cards.py plan --backup-root /absolute/private/path
  --keep-quote <id> ... --keep-tech <id> ...
Then: python scripts/archive_project_cards.py apply --snapshot <printed path> --digest <printed hash>
No files are deleted; no cleanup runs implicitly at startup.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import cpq_auth
import cpq_wf
import cpq_project_archive as archive
from tech_app.backend.storage import store
from tech_app.backend.config import DATA_DIR


def private_write(path, value):
    with path.open('x',encoding='utf-8') as out:
        os.chmod(path,0o600)
        json.dump(value,out,ensure_ascii=False,indent=2,default=str)
        out.flush()
        os.fsync(out.fileno())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    plan=sub.add_parser('plan')
    plan.add_argument('--backup-root',required=True,type=Path)
    plan.add_argument('--keep-quote',action='append',required=True)
    plan.add_argument('--keep-tech',action='append',required=True)
    apply=sub.add_parser('apply')
    apply.add_argument('--snapshot',required=True,type=Path)
    apply.add_argument('--digest',required=True)
    args=parser.parse_args()
    history=ROOT/'cpq_history'
    if args.command=='plan':
        # Force read-only PG access during planning.
        with cpq_auth._connect() as conn:
            conn.execute('SET default_transaction_read_only = on')
            cards,tasks=archive.workflow_state(conn)
        metas=[store.load_meta(m['project_id']) for m in
               store.list_projects(include_archived=True,lightweight=True)]
        metas=sorted(metas,key=lambda m:m['project_id'])
        ids=sorted(p.stem for p in history.glob('*.json'))
        proposal=archive.build_plan(cards,tasks,metas,ids,args.keep_quote,args.keep_tech)
        batch=datetime.now().strftime('project-archive-%Y%m%d-%H%M%S-%f')
        backup=args.backup_root.resolve()/batch
        backup.mkdir(parents=True,mode=0o700)
        os.chmod(backup,0o700)
        snapshot={'batch':batch,'repo':str(ROOT),'data_dir':str(DATA_DIR.resolve()),
                  'plan':proposal,'workflow_digest':archive.digest([cards,tasks]),
                  'cards':cards,'tasks':tasks,'metas':metas,
                  'meta_digest':archive.digest(metas),'history_ids':ids,
                  'index':archive.read_index(history)}
        private_write(backup/'snapshot.json',snapshot)
        print(json.dumps({'snapshot':str(backup/'snapshot.json'),'digest':archive.digest(snapshot),
                          'keep_quote':args.keep_quote,'keep_tech':args.keep_tech,
                          'archive_cards':len(proposal['archive_cards']),
                          'archive_history':len(proposal['archive_history']),
                          'archive_tech':len(proposal['archive_tech']),
                          'archive_tasks':len(proposal['archive_tasks'])},ensure_ascii=False))
    else:
        snapshot=json.loads(args.snapshot.read_text())
        if args.digest!=archive.digest(snapshot):
            raise ValueError('计划摘要不符，拒绝执行')
        if snapshot['repo']!=str(ROOT) or snapshot['data_dir']!=str(DATA_DIR.resolve()):
            raise ValueError('计划不属于当前仓库/数据目录，拒绝执行')
        result_path=args.snapshot.parent/'result.json'
        if result_path.exists():
            raise ValueError('该计划已执行，请先查看结果，不重复执行')
        conn=cpq_wf.tx_connect()
        try:
            archive.apply_snapshot(conn,store,history,snapshot,'user-authorized-project-maintenance')
        finally:
            conn.close()
        result={'batch':snapshot['batch'],'status':'applied',
                'keep_quote':snapshot['plan']['keep_quote'],'keep_tech':snapshot['plan']['keep_tech']}
        private_write(result_path,result)
        print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    main()
