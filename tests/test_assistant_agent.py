import sqlite3
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from backend.app.routers.ai import AgentTurnRequest, AgentExchangeRequest, agent_turn, save_agent_exchange

class AgentEndpointTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''CREATE TABLE ai_profile_models(profile_id INTEGER, model_id TEXT, is_visible INTEGER, is_available INTEGER);
        INSERT INTO ai_profile_models VALUES(1, 'test', 1, 1);
        CREATE TABLE ai_conversations(id INTEGER PRIMARY KEY, title TEXT, updated_at TEXT);
        INSERT INTO ai_conversations VALUES(1, '新对话', '');
        CREATE TABLE ai_messages(id INTEGER PRIMARY KEY, conversation_id INTEGER, role TEXT, content TEXT, profile_id INTEGER, model_id TEXT);''')
        self.addCleanup(self.db.close)

    def test_tool_context_forwarded_and_hidden_model_rejected(self):
        messages = [{'role': 'system', 'content': 'tool protocol'}, {'role': 'user', 'content': 'document text'}]
        with patch('backend.app.routers.ai._profile_or_404', return_value={'enabled': True}), patch('backend.app.routers.ai.chat_completion', return_value='{"tool":"read_document","args":{}}') as complete:
            result = agent_turn(AgentTurnRequest(profile_id=1, model='test', messages=messages), self.db)
            self.assertIn('read_document', result['content'])
            self.assertEqual(complete.call_args.args[1], messages)
            with self.assertRaises(HTTPException):
                agent_turn(AgentTurnRequest(profile_id=1, model='hidden', messages=messages), self.db)
            with self.assertRaises(HTTPException):
                agent_turn(AgentTurnRequest(profile_id=1, model='test', messages=[{'role': 'tool', 'content': 'invalid'}]), self.db)

    def test_exchange_atomic_rollback(self):
        request = AgentExchangeRequest(conversation_id=1, profile_id=1, model='test', message='Edit fixture', answer='Done')
        with patch('backend.app.routers.ai._conversation_payload', return_value={}):
            self.db.execute("CREATE TRIGGER fail_answer BEFORE INSERT ON ai_messages WHEN NEW.role = 'assistant' BEGIN SELECT RAISE(ABORT, 'injected'); END")
            self.db.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                save_agent_exchange(request, self.db)
            self.assertEqual(self.db.execute('SELECT COUNT(*) FROM ai_messages').fetchone()[0], 0)
            self.db.execute('DROP TRIGGER fail_answer')
            save_agent_exchange(request, self.db)
            self.assertEqual(self.db.execute('SELECT COUNT(*) FROM ai_messages').fetchone()[0], 2)
            self.assertEqual(self.db.execute('SELECT title FROM ai_conversations').fetchone()[0], 'Edit fixture')

if __name__ == '__main__':
    unittest.main()

