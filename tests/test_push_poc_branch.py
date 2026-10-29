"""推送目标安全：模拟Git，不执行真实网络或推送。"""
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


class PushBranchTests(unittest.TestCase):
    def setUp(self):
        path=Path(__file__).resolve().parents[1]/'scripts/push_remotes.py'
        spec=importlib.util.spec_from_file_location('push_poc',path)
        self.script=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.script)
        self.calls=[];self.branch='feat/poc-1030';self.head='a'*40

    def git(self,*args,check=True):
        self.calls.append(args);text=''
        if args==('branch','--show-current'):text=self.branch+'\n'
        elif args==('status','--porcelain'):pass
        elif args==('rev-parse','HEAD'):text=self.head+'\n'
        elif args[:2]==('remote','get-url'):
            text=self.script.EXPECTED_PUSH_URLS[args[-1]][0]+'\n'
        elif args[0]=='ls-remote':
            text=self.head+'\t'+args[-1]+'\n'
        elif args[0]=='push':pass
        else:raise AssertionError(args)
        return subprocess.CompletedProcess(args,0,text,'')

    def run_script(self,*args):
        with patch.object(self.script,'git',side_effect=self.git),patch('sys.argv',['push_remotes.py',*args]):
            self.script.main()

    def test_check_poc_only_reads_exact_feature_refs(self):
        self.run_script('--check')
        self.assertFalse(any(call[0]=='push' for call in self.calls))
        refs=[call[-1] for call in self.calls if call[0]=='ls-remote']
        self.assertEqual(['refs/heads/feat/poc-1030']*2,refs)

    def test_push_poc_both_remotes_same_head(self):
        self.run_script()
        pushes=[call for call in self.calls if call[0]=='push']
        self.assertEqual([('push',remote,self.head+':refs/heads/feat/poc-1030') for remote in ['gitlab','origin']],pushes)

    def test_master_main_and_unapproved_branches_cannot_push(self):
        for branch in ['master','main','feature/unapproved']:
            self.branch=branch;self.calls=[]
            with self.subTest(branch=branch),self.assertRaises(SystemExit):self.run_script()
            self.assertFalse(any(call[0]=='push' for call in self.calls))
