import json
import time

import httpx

from app.agent import AGENT_REQUEST_TIMEOUT
from app.config import settings
from app.llm import LLMClient, read_json


def check(client, label, messages):
    print(f'{label}...', flush=True)
    start = time.monotonic()
    response = client.complete(messages, request_timeout=AGENT_REQUEST_TIMEOUT)
    print(f'  OK: {time.monotonic() - start:.1f} с; відповідь: {response.content[:160]!r}', flush=True)
    return response


def main():
    cfg = settings()
    if not cfg.api_key:
        print('API_KEY порожній у local_config.py.')
        return 1
    print(f'Провайдер: {cfg.provider}; модель: {cfg.model}')
    client = LLMClient(cfg)
    try:
        check(client, '1. Звичайний запит', [
            {'role': 'user', 'content': 'Return exactly OK.'},
        ])
        planned = check(client, '2. План інструментів агента (JSON, без native tool calling)', [
            {'role': 'system', 'content': 'Return ONLY JSON {"actions":[{"tool":"read_context","arguments":{"sentence_id":0,"window":1}}]} without additional fields.'},
            {'role': 'user', 'content': 'A pronoun in sentence 1 is ambiguous. Choose the read_context tool for sentence 0.'},
        ])
        plan = read_json(planned.content)
        if not isinstance(plan.get('actions'), list):
            print('Помилка: агент не повернув actions.')
            return 2
        reviewed = check(client, '3. Рішення агента за результатами інструмента', [
            {'role': 'system', 'content': 'Return ONLY JSON with resolutions, discard_relation_ids, notes arrays.'},
            {'role': 'user', 'content': json.dumps({
                'unresolved_mentions': [{'id': 'M3', 'text': 'Вона', 'candidates': ['C1']}],
                'tool_results': [{'tool': 'read_context', 'result': {
                    'sentences': [{'id': 0, 'text': 'Марія зустріла Олега.'}, {'id': 1, 'text': 'Вона усміхнулася.'}],
                }}],
            }, ensure_ascii=False)},
        ])
        result = read_json(reviewed.content)
        if not isinstance(result.get('resolutions'), list):
            print('Помилка: немає масиву resolutions.')
            return 2
        print('Усі три API-запити успішні. Повний аналіз може тривати довше.')
        return 0
    except httpx.TimeoutException as exc:
        print(f'Таймаут: {type(exc).__name__}. За назвою останнього етапу вище можна визначити, де зависає API.')
    except httpx.HTTPStatusError as exc:
        print(f'Помилка API HTTP {exc.response.status_code}: {exc.response.text[:500]}')
    except httpx.RequestError as exc:
        print(f'Мережева помилка: {type(exc).__name__}: {exc}')
    except (ValueError, KeyError, TypeError) as exc:
        print(f'Неочікуваний формат відповіді: {exc}')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
