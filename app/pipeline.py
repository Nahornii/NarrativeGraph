import json
import re
import time

from pydantic import ValidationError

from app.agent import run_agent
from app.llm import LLMClient
from app.nlp import is_exact_quote, split_sentences
from app.schemas import AgentEvent, AnalysisResult, Extraction, Review

EXTRACT_SYSTEM = '''You are an NLP information-extraction engine. Analyze a short literary narrative in Ukrainian or English.
Return ONLY a JSON object with arrays: characters, mentions, relations.
CHARACTERS: [{"id":"C1","name":"canonical name used in story","aliases":["other exact names used"]}]. Include only people or person-like agents (no inanimate objects).
MENTIONS: [{"id":"M1","sentence_id":0,"text":"exact substring mentioning character including pronouns","character_id":"C1" or null,"candidates":["C1","C2"],"reason":"short reason"}]. Every actual mention you identify should have exact spelling from sentence. If a pronoun is ambiguous, character_id=null. When clear, resolve it. Do not confuse a sentence-initial generic capitalized word with a character.
RELATIONS: [{"id":"R1","source_id":"C1","target_id":"C2","kind":"conversation|conflict|support|family|transfer|other","label":"short action or relationship in ORIGINAL language","evidence":[{"sentence_id":0,"quote":"exact contiguous substring of sentence"}]}]. Only explicit relationships or interactions supported by direct evidence. No relation just because characters co-occur. A sentence can support multiple relations. For unclear endpoints do NOT invent relation.
Sentence IDs are ZERO based. Quotes must match the text verbatim. Characters and relation endpoints must refer to declared IDs. Use empty arrays if none. Never add events or facts not stated. No Markdown.'''


def _extract(llm: LLMClient, sentences: list[str]) -> Extraction:
    input_text = json.dumps({'sentences': [{'id': i, 'text': s} for i, s in enumerate(sentences)]}, ensure_ascii=False)
    for attempt in range(2):
        payload = llm.json_answer(EXTRACT_SYSTEM, input_text)
        try:
            return Extraction.model_validate(payload)
        except ValidationError as exc:
            if attempt == 1:
                raise ValueError(f'LLM повернула некоректну структуру сутностей: {exc.errors()[0]["msg"]}') from exc
            input_text += '\nPrevious response failed schema validation. Correct all fields and IDs; output only JSON.'
    raise AssertionError('Unreachable')


def validate(extraction: Extraction, sentences: list[str], review: Review) -> tuple[Extraction, list[str]]:
    warnings: list[str] = []
    character_ids: set[str] = set()
    characters = []
    for char in extraction.characters:
        if char.id in character_ids or not char.id.strip() or not char.name.strip():
            warnings.append(f'Пропущено некоректний запис персонажа {char.id!r}.')
            continue
        patterns = [char.name, *char.aliases]
        direct_match = any(label.casefold() in sentence.casefold() for sentence in sentences for label in patterns if label.strip())
        stem_match = False
        for label in patterns:
            head = label.strip().split()[0] if label.strip() else ''
            if len(head) < 3:
                continue
            prefix = head.casefold()[:max(3, len(head)-2)]
            if any(re.search(r'(?<!\w)' + re.escape(prefix) + r'\w*', sentence.casefold()) for sentence in sentences):
                stem_match = True
                break
        if not direct_match and not stem_match:
            warnings.append(f'Персонаж «{char.name}» не має прямої згадки в тексті — пропущено.')
            continue
        character_ids.add(char.id)
        characters.append(char)

    approved_resolutions = {r.mention_id: r for r in review.resolutions}
    mention_ids: set[str] = set()
    mentions = []
    for mention in extraction.mentions:
        if mention.id in mention_ids or mention.sentence_id not in range(len(sentences)):
            warnings.append(f'Пропущено згадку {mention.id}: неправильний ID або речення.')
            continue
        if not is_exact_quote(mention.text, sentences[mention.sentence_id]):
            warnings.append(f'Згадка {mention.id} не знайдена в оригінальному реченні.')
            continue
        mention_ids.add(mention.id)
        if mention.character_id and mention.character_id not in character_ids:
            mention.character_id = None
            warnings.append(f'Невідомий персонаж у згадці {mention.id}.')
        if mention.character_id is None:
            resolution = approved_resolutions.get(mention.id)
            if resolution and resolution.character_id in character_ids:
                mention.character_id = resolution.character_id
                mention.reason = resolution.reason
        mention.candidates = [x for x in mention.candidates if x in character_ids]
        mentions.append(mention)

    discard = set(review.discard_relation_ids)
    relations = []
    relation_ids: set[str] = set()
    for relation in extraction.relations:
        if relation.id in relation_ids or relation.id in discard:
            warnings.append(f'Зв’язок {relation.id} вилучено після перевірки.')
            continue
        relation_ids.add(relation.id)
        if relation.source_id not in character_ids or relation.target_id not in character_ids:
            warnings.append(f'Зв’язок {relation.id} має невідомого персонажа — пропущено.')
            continue
        if relation.source_id == relation.target_id:
            warnings.append(f'Петлю {relation.id} пропущено: самозв’язки не показуємо.')
            continue
        valid = [e for e in relation.evidence if 0 <= e.sentence_id < len(sentences)
                 and is_exact_quote(e.quote, sentences[e.sentence_id])]
        if not valid or len(valid) != len(relation.evidence):
            warnings.append(f'Зв’язок {relation.id} без підтвердженої цитати — пропущено.')
            continue
        relations.append(relation)
    return Extraction(characters=characters, mentions=mentions, relations=relations), warnings


def analyze(text: str, llm: LLMClient) -> AnalysisResult:
    start = time.monotonic()
    sentences = split_sentences(text)
    trace: list[AgentEvent] = [AgentEvent(step='NLP: сегментація', detail=f'Виділено речень: {len(sentences)}.', kind='info')]
    llm.stage = 'виділення персонажів і зв’язків'
    extracted = _extract(llm, sentences)
    trace.append(AgentEvent(step='LLM: виділення сутностей', detail=f'{len(extracted.characters)} персонажів, {len(extracted.mentions)} згадок, {len(extracted.relations)} зв’язків.', kind='model'))
    llm.stage = 'агентна перевірка контексту'
    review, agent_events, agent_status = run_agent(llm, sentences, extracted)
    trace.extend(agent_events)
    validated, warnings = validate(extracted, sentences, review)
    if agent_status not in ('complete', 'not_required'):
        warnings.insert(0, 'Агентна перевірка контексту не завершена. Показано попередні висновки LLM; коректність семантичних зв’язків не підтверджена агентом.')
    trace.append(AgentEvent(step='Перевірка доказів', detail=f'Зв’язків із точними цитатами: {len(validated.relations)}; відхилено: {len(extracted.relations)-len(validated.relations)}.', kind='validation'))
    return AnalysisResult(
        sentences=sentences,
        characters=validated.characters,
        mentions=validated.mentions,
        relations=validated.relations,
        agent_events=trace,
        warnings=warnings,
        metadata={'model': llm.config.model, 'requests': llm.requests, 'tokens': llm.tokens,
                  'seconds': round(time.monotonic() - start, 2),
                  'unresolved': sum(m.character_id is None for m in validated.mentions),
                  'agent_status': agent_status},
    )
