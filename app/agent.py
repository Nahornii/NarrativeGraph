import json
from typing import Any

import httpx

from app.llm import LLMClient, read_json
from app.nlp import is_exact_quote
from app.schemas import AgentEvent, Extraction, Review


AGENT_REQUEST_TIMEOUT = 180.0


class AgentTools:
    def __init__(self, sentences: list[str], extraction: Extraction):
        self.sentences = sentences
        self.extraction = extraction

    def execute(self, name: str, arguments: dict[str, Any]) -> dict:
        if name == 'read_context':
            index = arguments.get('sentence_id')
            window = arguments.get('window', 1)
            if type(index) is not int or not 0 <= index < len(self.sentences):
                return {'error': 'Invalid sentence_id'}
            if type(window) is not int:
                window = 1
            window = max(0, min(window, 2))
            return {'sentences': [
                {'id': i, 'text': self.sentences[i]}
                for i in range(max(0, index - window), min(len(self.sentences), index + window + 1))
            ]}
        if name == 'find_mentions':
            character = next(
                (item for item in self.extraction.characters if item.id == arguments.get('character_id')),
                None,
            )
            if character is None:
                return {'error': 'Unknown character_id'}
            patterns = [character.name, *character.aliases]
            matches = [
                {'sentence_id': i, 'text': sentence}
                for i, sentence in enumerate(self.sentences)
                if any(pattern.casefold() in sentence.casefold() for pattern in patterns if pattern.strip())
            ]
            return {'character': character.name, 'matches': matches[:12]}
        if name == 'check_evidence':
            relation = next(
                (item for item in self.extraction.relations if item.id == arguments.get('relation_id')),
                None,
            )
            if relation is None:
                return {'error': 'Unknown relation_id'}
            checks = [
                {
                    'sentence_id': evidence.sentence_id,
                    'quote': evidence.quote,
                    'valid': 0 <= evidence.sentence_id < len(self.sentences)
                    and is_exact_quote(evidence.quote, self.sentences[evidence.sentence_id]),
                }
                for evidence in relation.evidence
            ]
            return {
                'relation_id': relation.id,
                'checks': checks,
                'all_valid': bool(checks) and all(check['valid'] for check in checks),
            }
        return {'error': 'Unknown tool'}


PLANNER_SYSTEM = '''You are a narrative NLP verification agent. You can choose which local tools the application should execute.
Select up to THREE useful actions to resolve the listed uncertain character mentions or questionable relation evidence.
Available tools:
read_context({"sentence_id": integer, "window": integer 0..2}) - read nearby original sentences.
find_mentions({"character_id": string}) - find other mentions of one character.
check_evidence({"relation_id": string}) - verify exact quotations for a relationship.
Return ONLY JSON with the structure:
{"actions":[{"tool":"read_context","arguments":{"sentence_id":0,"window":1}}]}
You may return an empty actions array if tools cannot help. Choose based on input. Never invent IDs.'''

REVIEW_SYSTEM = '''You verify narrative relations and ambiguous pronouns using the original evidence returned by tools.
Only resolve mentions listed as unresolved and only to supplied character IDs. Do NOT resolve based on grammatical gender alone.
If the context does not prove a reference, leave it unresolved. Reject relations when evidence is false or they claim interactions not supported by text.
Return ONLY JSON:
{"resolutions":[{"mention_id":"M1","character_id":"C1" or null,"reason":"short explanation"}],"discard_relation_ids":["R2"],"notes":["short observation"]}
Return empty arrays if there is no supported change. Do not invent people, IDs or facts.'''


def _issues(sentences: list[str], extraction: Extraction) -> tuple[list[dict], list[str]]:
    unresolved = [
        {'id': mention.id, 'sentence_id': mention.sentence_id, 'text': mention.text,
         'candidates': mention.candidates}
        for mention in extraction.mentions if mention.character_id is None
    ]
    suspect = []
    for relation in extraction.relations:
        if not relation.evidence or any(
            not 0 <= evidence.sentence_id < len(sentences)
            or not is_exact_quote(evidence.quote, sentences[evidence.sentence_id])
            for evidence in relation.evidence
        ):
            suspect.append(relation.id)
    return unresolved, suspect


def _incomplete(events: list[AgentEvent], detail: str, status: str):
    events.append(AgentEvent(step='Агентна перевірка не завершена', detail=detail, kind='validation'))
    return Review(), events, status


def run_agent(llm: LLMClient, sentences: list[str], extraction: Extraction) -> tuple[Review, list[AgentEvent], str]:
    unresolved, suspect = _issues(sentences, extraction)
    if not unresolved and not suspect:
        return Review(), [AgentEvent(
            step='Додаткова агентна перевірка не потрібна',
            detail='Початковий аналіз не містить невизначених згадок або помилок цитування. Виконано стандартну перевірку доказів.',
            kind='info',
        )], 'not_required'

    events = [AgentEvent(
        step='Агентна перевірка контексту',
        detail=f'Потрібно дослідити згадки: {len(unresolved)}; сумнівні цитати: {len(suspect)}.',
        kind='model',
    )]
    payload = {
        'unresolved_mentions': unresolved,
        'suspect_relation_ids': suspect,
        'characters': [{'id': character.id, 'name': character.name, 'aliases': character.aliases}
                       for character in extraction.characters],
        'relations': [relation.model_dump() for relation in extraction.relations],
        'sentence_count': len(sentences),
    }
    try:
        plan_response = llm.complete([
            {'role': 'system', 'content': PLANNER_SYSTEM},
            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
        ], request_timeout=AGENT_REQUEST_TIMEOUT)
        plan = read_json(plan_response.content)
    except httpx.TimeoutException:
        return _incomplete(events, f'Gemini не відповів під час вибору інструментів ({int(AGENT_REQUEST_TIMEOUT)} с). Граф побудовано без агентного підтвердження.', 'timeout')
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403, 429):
            raise
        return _incomplete(events, f'Помилка API HTTP {exc.response.status_code} під час вибору інструментів.', 'api_error')
    except (ValueError, TypeError):
        return _incomplete(events, 'Агент повернув некоректний план інструментів.', 'invalid_plan')

    actions = plan.get('actions')
    if not isinstance(actions, list):
        return _incomplete(events, 'Агент повернув план без масиву actions.', 'invalid_plan')

    tool_executor = AgentTools(sentences, extraction)
    results = []
    for action in actions[:3]:
        if not isinstance(action, dict):
            continue
        name = action.get('tool')
        arguments = action.get('arguments', {})
        if not isinstance(name, str) or not isinstance(arguments, dict):
            continue
        result = tool_executor.execute(name, arguments)
        results.append({'tool': name, 'arguments': arguments, 'result': result})
        events.append(AgentEvent(
            step={
                'read_context': 'Читання контексту',
                'find_mentions': 'Пошук згадок',
                'check_evidence': 'Перевірка цитати',
            }.get(name, 'Невідомий інструмент'),
            detail=f'{name}({json.dumps(arguments, ensure_ascii=False)})',
            kind='tool',
        ))

    if not results:
        events.append(AgentEvent(
            step='Агент не знайшов потрібних інструментів',
            detail='Невизначені згадки залишено без примусового розв’язання.',
            kind='info',
        ))
        return Review(), events, 'complete'

    try:
        review_response = llm.complete([
            {'role': 'system', 'content': REVIEW_SYSTEM},
            {'role': 'user', 'content': json.dumps({
                'unresolved_mentions': unresolved,
                'suspect_relation_ids': suspect,
                'characters': payload['characters'],
                'relations': payload['relations'],
                'tool_results': results,
            }, ensure_ascii=False)},
        ], request_timeout=AGENT_REQUEST_TIMEOUT)
        review = Review.model_validate(read_json(review_response.content))
    except httpx.TimeoutException:
        return _incomplete(events, f'Gemini не відповів під час перевірки результатів інструментів ({int(AGENT_REQUEST_TIMEOUT)} с).', 'timeout')
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403, 429):
            raise
        return _incomplete(events, f'Помилка API HTTP {exc.response.status_code} на етапі підсумкового рішення.', 'api_error')
    except (ValueError, TypeError):
        return _incomplete(events, 'Агент повернув некоректне рішення.', 'invalid_review')

    for note in review.notes[:4]:
        events.append(AgentEvent(step='Висновок агента', detail=note[:300], kind='model'))
    return review, events, 'complete'
