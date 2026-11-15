from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.agent import AgentTools, run_agent
from app.llm import AgentResponse, ToolCall, read_json
from app.main import app
from app.nlp import is_exact_quote, split_sentences
from app.pipeline import analyze, validate
from app.schemas import Extraction, Review


TEXT = 'Марія зустріла Олега. Вона передала йому листа. Олег подякував Марії.'


def example_extraction():
    return Extraction.model_validate({
        'characters': [
            {'id': 'C1', 'name': 'Марія', 'aliases': []},
            {'id': 'C2', 'name': 'Олег', 'aliases': []},
        ],
        'mentions': [
            {'id': 'M1', 'sentence_id': 0, 'text': 'Марія', 'character_id': 'C1'},
            {'id': 'M2', 'sentence_id': 0, 'text': 'Олега', 'character_id': 'C2'},
            {'id': 'M3', 'sentence_id': 1, 'text': 'Вона', 'character_id': None, 'candidates': ['C1']},
        ],
        'relations': [
            {'id': 'R1', 'source_id': 'C1', 'target_id': 'C2', 'kind': 'conversation',
             'label': 'зустріла', 'evidence': [{'sentence_id': 0, 'quote': 'Марія зустріла Олега'}]},
            {'id': 'R2', 'source_id': 'C1', 'target_id': 'C2', 'kind': 'transfer',
             'label': 'передала листа', 'evidence': [{'sentence_id': 1, 'quote': 'вигаданий фрагмент'}]},
        ],
    })


class FakeLLM:
    def __init__(self):
        self.requests = 0
        self.tokens = 0
        self.config = SimpleNamespace(model='fake-for-tests')
        self.agent_round = 0

    def json_answer(self, system, user):
        self.requests += 1
        return example_extraction().model_dump()

    def complete(self, messages, tools=None, request_timeout=None):
        self.requests += 1
        self.agent_round += 1
        if self.agent_round == 1:
            return AgentResponse(content='{"actions":[{"tool":"read_context","arguments":{"sentence_id":1,"window":1}},{"tool":"check_evidence","arguments":{"relation_id":"R2"}}]}')
        assert 'tool_results' in messages[1]['content']
        return AgentResponse(content='{"resolutions":[{"mention_id":"M3","character_id":"C1","reason":"Марія у попередньому реченні"}],"discard_relation_ids":["R2"],"notes":["Перевірено займенник."]}')


def test_segmentation():
    assert len(split_sentences(TEXT)) == 3
    assert split_sentences(' One. Two! Three? ') == ['One.', 'Two!', 'Three?']


def test_json_from_fenced_code():
    assert read_json('```json\n{"a":1}\n```') == {'a': 1}


def test_quote_verification():
    assert is_exact_quote('Марія зустріла Олега', split_sentences(TEXT)[0])
    assert not is_exact_quote('Марія побила Олега', split_sentences(TEXT)[0])


def test_agent_tool_calls_and_resolutions():
    fake = FakeLLM()
    result = analyze(TEXT, fake)
    assert len(result.characters) == 2
    assert len(result.relations) == 1
    assert any(m.id == 'M3' and m.character_id == 'C1' for m in result.mentions)
    assert any(e.kind == 'tool' and e.step == 'Читання контексту' for e in result.agent_events)
    assert any('R2' in w for w in result.warnings)
    assert fake.requests == 3


def test_validation_rejects_hallucinated_evidence():
    extraction = example_extraction()
    verified, warnings = validate(extraction, split_sentences(TEXT), Review())
    assert [r.id for r in verified.relations] == ['R1']
    assert warnings


def test_unknown_character_cannot_be_resolved():
    extraction = example_extraction()
    verified, _ = validate(extraction, split_sentences(TEXT), Review.model_validate({
        'resolutions': [{'mention_id': 'M3', 'character_id': 'C999', 'reason': 'invalid'}]
    }))
    assert next(m for m in verified.mentions if m.id == 'M3').character_id is None


def test_tools_reject_invalid_ids():
    tool = AgentTools(split_sentences(TEXT), example_extraction())
    assert 'error' in tool.execute('read_context', {'sentence_id': -4})
    assert 'error' in tool.execute('find_mentions', {'character_id': 'Unknown'})
    assert tool.execute('check_evidence', {'relation_id': 'R2'})['all_valid'] is False


def test_api_status_and_no_key(monkeypatch):
    from app.config import Settings
    monkeypatch.setattr('app.main.settings', lambda: Settings('', 'gemini', 'model', 'url'))
    client = TestClient(app)
    assert client.get('/').status_code == 200
    assert client.get('/api/status').json()['configured'] is False
    response = client.post('/api/analyze', json={'text': TEXT})
    assert response.status_code == 400
    assert 'API_KEY' in response.json()['detail']


def test_inflected_name_is_accepted():
    extracted = Extraction.model_validate({
        'characters': [{'id': 'C1', 'name': 'Анна', 'aliases': []}, {'id': 'C2', 'name': 'Олег', 'aliases': []}],
        'mentions': [],
        'relations': [{'id': 'R1', 'source_id': 'C2', 'target_id': 'C1', 'kind': 'transfer',
                       'label': 'передав', 'evidence': [{'sentence_id': 0, 'quote': 'Олег передав Анні'}]}],
    })
    verified, issues = validate(extracted, ['Олег передав Анні листа.'], Review())
    assert len(verified.characters) == 2
    assert len(verified.relations) == 1
    assert not issues


def test_api_analyze_with_fake_model(monkeypatch):
    from app.config import Settings
    monkeypatch.setattr('app.main.settings', lambda: Settings('test-key', 'gemini', 'fake-for-tests', 'https://example.invalid'))
    monkeypatch.setattr('app.main.LLMClient', lambda config: FakeLLM())
    client = TestClient(app)
    response = client.post('/api/analyze', json={'text': TEXT})
    assert response.status_code == 200
    body = response.json()
    assert len(body['characters']) == 2
    assert len(body['relations']) == 1
    assert body['agent_events']
    assert body['metadata']['requests'] == 3


def test_gemini_thought_signature_is_preserved_in_tool_messages():
    import httpx
    from app.config import Settings
    from app.llm import LLMClient

    seen = []

    def responder(request: httpx.Request):
        payload = __import__('json').loads(request.content)
        seen.append(payload)
        if len(seen) == 1:
            return httpx.Response(200, json={
                'choices': [{'message': {
                    'role': 'assistant', 'content': '',
                    'extra_content': {'google': {'thought_signature': 'opaque-state'}},
                    'tool_calls': [{'id': 't1', 'type': 'function',
                                    'extra_content': {'google': {'thought_signature': 'opaque-tool-state'}},
                                    'function': {'name': 'read_context', 'arguments': '{"sentence_id":0}'}}]
                }}]
            })
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': '{"ok":true}'}}]})

    client = LLMClient(Settings('key', 'gemini', 'gemini-3.8-flash', 'https://example.invalid'))
    client.client = httpx.Client(transport=httpx.MockTransport(responder))
    first = client.complete([{'role': 'user', 'content': 'Check'}], [{'type': 'function', 'function': {'name': 'read_context', 'parameters': {'type': 'object', 'properties': {}}}}])
    assert first.assistant_message['extra_content']['google']['thought_signature'] == 'opaque-state'
    assert first.assistant_message['tool_calls'][0]['extra_content']['google']['thought_signature'] == 'opaque-tool-state'
    client.complete([{'role': 'user', 'content': 'Check'}, first.assistant_message, {'role': 'tool', 'tool_call_id': 't1', 'content': '{}'}])
    assert seen[1]['messages'][1]['extra_content']['google']['thought_signature'] == 'opaque-state'


def test_gemini_low_reasoning_is_set():
    import httpx
    from app.config import Settings
    from app.llm import LLMClient
    bodies = []

    def responder(request):
        bodies.append(__import__('json').loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': '{"ok":true}'}}]})

    client = LLMClient(Settings('key', 'gemini', 'gemini-3.8-flash', 'https://example.invalid'))
    assert client.client.timeout.read == 180.0
    client.client = httpx.Client(transport=httpx.MockTransport(responder))
    client.complete([{'role': 'user', 'content': 'Check'}])
    assert bodies[0]['reasoning_effort'] == 'low'


def test_agent_timeout_keeps_extracted_graph():
    import httpx

    class TimeoutDuringReview(FakeLLM):
        def complete(self, messages, tools=None, request_timeout=None):
            raise httpx.ReadTimeout('Simulated slow agent request')

    result = analyze(TEXT, TimeoutDuringReview())
    assert result.metadata['agent_status'] == 'timeout'
    assert len(result.characters) == 2
    assert len(result.relations) == 1
    assert any('не завершена' in w for w in result.warnings)
    assert any('Агентна перевірка не завершена' in e.step for e in result.agent_events)


def test_agent_payload_does_not_duplicate_full_text():
    class InspectAgent(FakeLLM):
        def complete(self, messages, tools=None, request_timeout=None):
            assert 'original_sentences' not in messages[1]['content']
            self.requests += 1
            self.agent_round += 1
            if self.agent_round == 1:
                return AgentResponse(content='{"actions":[]}')
            raise AssertionError('Agent must not make a second request without tool calls')

    result = analyze(TEXT, InspectAgent())
    assert result.metadata['agent_status'] == 'complete'


def test_agent_skips_without_uncertainties():
    class ClearText(FakeLLM):
        def json_answer(self, system, user):
            self.requests += 1
            extracted = example_extraction().model_dump()
            extracted['mentions'][2]['character_id'] = 'C1'
            extracted['relations'] = extracted['relations'][:1]
            return extracted

        def complete(self, messages, tools=None, request_timeout=None):
            raise AssertionError('No agent LLM call should be needed')

    result = analyze(TEXT, ClearText())
    assert result.metadata['agent_status'] == 'not_required'
    assert result.metadata['requests'] == 1
    assert len(result.relations) == 1
    assert not result.warnings


def test_agent_second_step_timeout_keeps_partial_graph():
    import httpx

    class FinalTimeout(FakeLLM):
        def complete(self, messages, tools=None, request_timeout=None):
            self.requests += 1
            self.agent_round += 1
            if self.agent_round == 1:
                return AgentResponse(content='{"actions":[{"tool":"read_context","arguments":{"sentence_id":1}}]}')
            raise httpx.ReadTimeout('Test final verification timeout')

    result = analyze(TEXT, FinalTimeout())
    assert result.metadata['agent_status'] == 'timeout'
    assert any(event.kind == 'tool' for event in result.agent_events)
    assert len(result.relations) == 1


def test_agent_uses_longer_timeout_for_both_requests():
    from app.agent import AGENT_REQUEST_TIMEOUT

    class CaptureTimeouts(FakeLLM):
        def __init__(self):
            super().__init__()
            self.timeouts = []

        def complete(self, messages, tools=None, request_timeout=None):
            self.timeouts.append(request_timeout)
            return super().complete(messages, tools=tools, request_timeout=request_timeout)

    fake = CaptureTimeouts()
    analyze(TEXT, fake)
    assert AGENT_REQUEST_TIMEOUT == 180.0
    assert fake.timeouts == [180.0, 180.0]
