import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class AgentResponse:
    content: str = ''
    calls: list[ToolCall] = field(default_factory=list)
    assistant_message: dict[str, Any] = field(default_factory=dict)


def read_json(text: str) -> dict:
    value = text.strip()
    value = re.sub(r'^```(?:json)?\s*|\s*```$', '', value, flags=re.IGNORECASE).strip()
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        start = value.find('{')
        end = value.rfind('}')
        if start < 0 or end <= start:
            raise ValueError('LLM повернула не JSON-об’єкт.') from None
        data = json.loads(value[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError('Очікувався JSON-об’єкт.')
    return data


class LLMClient:
    def __init__(self, config: Settings):
        self.config = config
        self.client = httpx.Client(timeout=httpx.Timeout(connect=15.0, read=180.0, write=30.0, pool=15.0))
        self.stage = 'початковий запит' 
        self.requests = 0
        self.tokens = 0

    def complete(self, messages: list[dict], tools: list[dict] | None = None, request_timeout: float | None = None) -> AgentResponse:
        kwargs: dict = {'model': self.config.model, 'messages': messages}
        if self.config.provider == 'gemini':
            kwargs['reasoning_effort'] = 'low'
        if tools:
            kwargs['tools'] = tools
            kwargs['tool_choice'] = 'auto'
        self.requests += 1
        started = time.monotonic()
        logger.info('LLM request #%s: stage=%s model=%s tool_mode=%s', self.requests, self.stage, self.config.model, bool(tools))
        try:
            request_options: dict[str, Any] = {}
            if request_timeout is not None:
                request_options['timeout'] = httpx.Timeout(connect=10.0, read=request_timeout, write=20.0, pool=10.0)
            response = self.client.post(
                self.config.base_url.rstrip('/') + '/chat/completions',
                json=kwargs,
                headers={'Authorization': f'Bearer {self.config.api_key}', 'Content-Type': 'application/json'},
                **request_options,
            )
        except httpx.TimeoutException:
            logger.warning('LLM timeout: stage=%s after %.1f s', self.stage, time.monotonic() - started)
            raise
        logger.info('LLM response: stage=%s status=%s duration=%.1f s', self.stage, response.status_code, time.monotonic() - started)
        response.raise_for_status()
        data = response.json()
        usage = data.get('usage') or {}
        self.tokens += usage.get('total_tokens') or 0
        choices = data.get('choices') or []
        if not choices:
            raise ValueError('Модель повернула порожню відповідь.')
        msg = choices[0]['message']
        calls = []
        for tool in msg.get('tool_calls') or []:
            try:
                args = json.loads(tool['function'].get('arguments') or '{}')
            except json.JSONDecodeError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            calls.append(ToolCall(id=tool['id'], name=tool['function']['name'], arguments=args))
        content = msg.get('content') or ''
        msg_dict: dict[str, Any] = {'role': 'assistant', 'content': content}
        if msg.get('tool_calls'):
            msg_dict['tool_calls'] = msg['tool_calls']
        if msg.get('extra_content'):
            msg_dict['extra_content'] = msg['extra_content']
        return AgentResponse(content=content, calls=calls, assistant_message=msg_dict)

    def json_answer(self, system: str, user: str) -> dict:
        messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]
        for attempt in range(2):
            result = self.complete(messages)
            try:
                return read_json(result.content)
            except (ValueError, json.JSONDecodeError) as exc:
                if attempt == 1:
                    raise ValueError(f'Некоректна відповідь LLM: {exc}') from exc
                messages.append({'role': 'assistant', 'content': result.content})
                messages.append({'role': 'user', 'content': 'Поверни лише валідний JSON-об’єкт згідно зі схемою. Без Markdown.'})
        raise AssertionError('Unreachable')
