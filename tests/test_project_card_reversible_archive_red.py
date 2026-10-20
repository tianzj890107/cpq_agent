import importlib
import tempfile
import unittest
import copy
import sqlite3
from pathlib import Path
from unittest.mock import patch


class ProjectArchive(unittest.TestCase):
    def service(self):
        return importlib.import_module('cpq_project_archive')

    def test_plan_is_exact_and_keeps_completed_tasks(self):
        mod = self.service()
        plan = mod.build_plan(
            [{'card_id':1,'session_id':'keep','overall_status':'in_progress'},
             {'card_id':2,'session_id':'old','overall_status':'completed'}],
            [{'task_id':11,'status':'claimed','replaced_by_task_id':None},
             {'task_id':12,'status':'open','replaced_by_task_id':None},
             {'task_id':13,'status':'completed','replaced_by_task_id':None},
             {'task_id':14,'status':'cancelled','replaced_by_task_id':99}],
            [{'project_id':'tech_keep'},{'project_id':'tech_old'}],
            ['keep','old','history_only'],['keep'],['tech_keep'])
        self.assertEqual(['old','history_only'],plan['archive_history'])
        self.assertEqual([2],[r['card_id'] for r in plan['archive_cards']])
        self.assertEqual([11,12,14],[r['task_id'] for r in plan['archive_tasks']])
        self.assertEqual(['tech_old'],plan['archive_tech'])

    def test_invalid_or_missing_keep_id_rejected(self):
        mod=self.service()
        for keep in ([],['missing'],['../bad'],['keep','keep']):
            with self.subTest(keep=keep),self.assertRaises(ValueError):
                mod.build_plan([{'session_id':'keep'}],[],[{'project_id':'t'}],['keep'],keep,['t'])

    def test_history_list_filters_archive_without_changing_source(self):
        mod=self.service()
        import cpq_agent_server as server
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            original=b'{"id":"old","title":"old","events":[]}'
            (root/'old.json').write_bytes(original)
            (root/'keep.json').write_text('{"id":"keep","title":"keep","events":[]}')
            mod.write_index(root,{'archived_sessions':['old']})
            with patch.object(server,'HISTORY_DIR',directory):
                self.assertEqual(['keep'],[r['id'] for r in server.list_history()])
                self.assertEqual('old',server.load_history('old')['id'])
            self.assertEqual(original,(root/'old.json').read_bytes())

    def test_workflow_archive_not_in_my_cards(self):
        import cpq_wf
        import inspect
        self.assertIn("c.overall_status <> 'archived'",inspect.getsource(cpq_wf.my_cards))

    def test_refresh_does_not_resurrect_archived_card(self):
        import cpq_wf
        from unittest.mock import Mock
        card={'overall_status':'archived','current_step':1,'card_id':'1'}
        with patch.object(cpq_wf,'_fetch_card',return_value=card),patch.object(cpq_wf.cpq_auth,'_exec') as execute:
            conn=Mock()
            result=cpq_wf.sync_card('old',{'user_id':'1'},current_step=6,conn=conn)
            self.assertEqual('archived',result['overall_status'])
            execute.assert_not_called()

    def fixture(self, directory):
        mod=self.service()
        database=sqlite3.connect(':memory:')
        database.executescript('''
CREATE TABLE cpq_wf_card(card_id INTEGER,session_id TEXT,overall_status TEXT);
INSERT INTO cpq_wf_card VALUES(1,'keep','in_progress'),(2,'old','completed');
CREATE TABLE cpq_wf_task(task_id INTEGER,status TEXT,replaced_by_task_id INTEGER,claimed_by_user_id INTEGER,payload TEXT);
INSERT INTO cpq_wf_task VALUES(11,'claimed',NULL,123,'original payload'),(12,'completed',NULL,456,'completed payload');
''')
        class Connection:
            def execute(self,sql,args=()):
                if sql.startswith('LOCK TABLE'):return None
                return database.execute(sql.replace('%s','?'),args)
            def commit(self):database.commit()
            def rollback(self):database.rollback()
        class Store:
            def __init__(self):
                self.metas={'tk':{'project_id':'tk'},'to':{'project_id':'to'}}
                self.active=[]
            def list_projects(self,**kw):return copy.deepcopy(list(self.metas.values()))
            def load_meta(self,pid):return copy.deepcopy(self.metas[pid])
            def list_tasks(self,pid):return self.active
            def archive_project(self,pid,author):self.metas[pid]['deleted_at']='now'
            def _meta(self):return self
            def put_meta(self,pid,meta):self.metas[pid]=copy.deepcopy(meta)
        conn=Connection();store=Store();root=Path(directory)
        (root/'keep.json').write_text('{"id":"keep"}')
        (root/'old.json').write_text('{"id":"old"}')
        cards,tasks=mod.workflow_state(conn)
        metas=store.list_projects()
        plan=mod.build_plan(cards,tasks,metas,['keep','old'],['keep'],['tk'])
        snapshot={'batch':'test','plan':plan,'workflow_digest':mod.digest([cards,tasks]),
                  'metas':metas,'meta_digest':mod.digest(metas),
                  'history_ids':['keep','old'],'index':mod.read_index(root)}
        return mod,conn,store,snapshot

    def test_apply_preserves_task_owner_payload_and_source_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            mod,conn,store,snapshot=self.fixture(directory)
            mod.apply_snapshot(conn,store,directory,snapshot,'tester')
            self.assertEqual([(1,'in_progress'),(2,'archived')],
                             conn.execute('SELECT card_id,overall_status FROM cpq_wf_card').fetchall())
            self.assertEqual([(11,'archived',123,'original payload'),(12,'completed',456,'completed payload')],
                             conn.execute('SELECT task_id,status,claimed_by_user_id,payload FROM cpq_wf_task').fetchall())
            self.assertNotIn('deleted_at',store.metas['tk'])
            self.assertIn('deleted_at',store.metas['to'])
            self.assertEqual('{"id":"old"}',(Path(directory)/'old.json').read_text())

    def test_cross_store_failure_rolls_back_pg_and_meta(self):
        with tempfile.TemporaryDirectory() as directory:
            mod,conn,store,snapshot=self.fixture(directory)
            with patch.object(mod,'write_index',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    mod.apply_snapshot(conn,store,directory,snapshot,'tester')
            self.assertEqual('completed',conn.execute('SELECT overall_status FROM cpq_wf_card WHERE card_id=2').fetchone()[0])
            self.assertNotIn('deleted_at',store.metas['to'])

    def test_changed_state_and_running_work_fail_before_mutation(self):
        for running in (True,False):
            with self.subTest(running=running),tempfile.TemporaryDirectory() as directory:
                mod,conn,store,snapshot=self.fixture(directory)
                if running:store.active=[{'status':'running'}]
                else:
                    conn.execute("UPDATE cpq_wf_task SET status='open' WHERE task_id=11")
                    conn.commit()
                with self.assertRaises(ValueError):
                    mod.apply_snapshot(conn,store,directory,snapshot,'tester')
                self.assertEqual('completed',conn.execute('SELECT overall_status FROM cpq_wf_card WHERE card_id=2').fetchone()[0])
                self.assertNotIn('deleted_at',store.metas['to'])
