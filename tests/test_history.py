import json
from pathlib import Path
import re
import tempfile
import unittest
import uuid
from unittest.mock import patch
from agent_utils.agterm.history import claude_history,codex_history


class HistoryTests(unittest.TestCase):
    def test_claude_requires_actual_matching_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            root='/tmp/synthetic-project';directory=Path(d)/'projects'/re.sub(r'[^A-Za-z0-9-]','-',root)
            directory.mkdir(parents=True)
            own,other=str(uuid.uuid4()),str(uuid.uuid4())
            for ident,cwd in [(own,root),(other,'/tmp/other')]:
                (directory/(ident+'.jsonl')).write_text(json.dumps({'cwd':cwd,'sessionId':ident,'message':{'content':'not exported'}})+'\n')
            result=claude_history([root],10,d)
            self.assertEqual([r['conversation_id'] for r in result],[own])
            self.assertNotIn('not exported',json.dumps(result))
    def test_codex_filters_metadata_and_paginates(self):
        with patch('agent_utils.agterm.history.codex_server') as server:
            request=server.return_value.__enter__.return_value
            request.side_effect=[{'data':[{'id':'a','cwd':'/tmp/demo'}],'nextCursor':'next'},
                {'data':[{'id':'b','cwd':'/tmp/other'}],'nextCursor':None}]
            result=codex_history(['/tmp/demo'],10)
            self.assertEqual([r['conversation_id'] for r in result],['a'])
            self.assertEqual(request.call_count,2)
            self.assertTrue(request.call_args.args[1]['useStateDbOnly'])
