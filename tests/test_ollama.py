"""Regression: preserve native Ollama tool calls across conversation turns."""
import json
import unittest
from unittest.mock import patch
from jarvis.llm.base import ChatMessage, ToolCall, messages_to_openai, messages_to_ollama
from jarvis.llm.providers import OllamaProvider


class TestOllamaMessages(unittest.TestCase):
    def setUp(self):
        self.args = {'query': 'SELECT COUNT(*) FROM users', 'filters': {'active': True}, 'tags': ['été']}
        self.messages = [ChatMessage('system', 'Assistant'), ChatMessage('user', 'Combien ?'),
                         ChatMessage('assistant', tool_calls=[ToolCall('call1', 'database.query', self.args)]),
                         ChatMessage('tool', '42', tool_call_id='call1', name='database.query')]

    def test_native_arguments_and_result_name(self):
        wire = json.loads(json.dumps(messages_to_ollama(self.messages)))
        self.assertEqual(wire[2]['tool_calls'][0]['function']['arguments'], self.args)
        self.assertEqual(wire[2]['content'], '')
        self.assertEqual(wire[3], {'role': 'tool', 'content': '42', 'tool_name': 'database.query'})
        self.assertEqual(self.messages[2].tool_calls[0].arguments, self.args)

    def test_openai_keeps_string_arguments(self):
        wire = messages_to_openai(self.messages)
        self.assertEqual(json.loads(wire[2]['tool_calls'][0]['function']['arguments']), self.args)
        self.assertEqual(wire[3]['tool_call_id'], 'call1')

    def test_multiple_and_empty_arguments(self):
        messages = [ChatMessage('assistant', tool_calls=[ToolCall('a', 'one', {}), ToolCall('b', 'two', self.args)])]
        calls = messages_to_ollama(messages)[0]['tool_calls']
        self.assertEqual([c['function']['arguments'] for c in calls], [{}, self.args])

    @patch('jarvis.llm.providers.http_json')
    def test_provider_tool_roundtrip(self, http):
        provider = OllamaProvider({'id': 'local', 'config': {'default_model': 'test'}}, lambda *a: '')
        http.side_effect = [(True, {'models': []}), (True, {'message': {'tool_calls': [
            {'function': {'name': 'database.query', 'arguments': self.args}}]}}),
            (True, {'models': []}), (True, {'message': {'content': '42 utilisateurs.'}})]
        messages = self.messages[:2]
        first = provider.chat(messages)
        self.assertTrue(first.ok)
        messages += [ChatMessage('assistant', tool_calls=first.tool_calls),
                     ChatMessage('tool', '42', name=first.tool_calls[0].name, tool_call_id=first.tool_calls[0].id)]
        second = provider.chat(messages)
        body = http.call_args.kwargs['body']
        self.assertEqual(body['messages'][2]['tool_calls'][0]['function']['arguments'], self.args)
        self.assertEqual(body['messages'][3]['tool_name'], 'database.query')
        self.assertEqual(second.text, '42 utilisateurs.')

if __name__ == '__main__':
    unittest.main()
