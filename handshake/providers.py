"""The three model adapters (design §2.3). Each renders a native request from the worker's payload, sends it and parses
the reply into the shape the game loop reads, so the worker never depends on a provider; the journal keeps each reply's
native content as sent.

- Anthropic Messages: the default and measured path.
- OpenAI Responses: built and tested on a fake transport, but unmeasured.
- Local Qwen through oMLX's chat completions: experimental until one local game completes, and never measured.

ADAPTERS picks a seat's adapter by its model. Only the host imports this module; the worker never sees an SDK, an
endpoint or a key. Hosted URLs are fixed and the oMLX URL is loopback, so neither a payload nor the environment can
redirect egress, and no client retries, follows a redirect or reads proxy settings. Prices are list prices, used for
the spend ledger; the provider's bill is authoritative.
"""

import json
import math
import os
from pathlib import Path

import anthropic
import openai

from handshake.protocol import strict_json_loads

QWEN = 'Qwen-3.8-27B-8bit'  # oMLX's alias for the model directory below
# USD per million tokens at list price, read 2026-10-18 (design §2.3); cache_write is Anthropic's 5-minute write and
# OpenAI's 30-minute one.
PRICES = {
    'claude-haiku-5-5': {'input': 0.10, 'cache_write': 0.125, 'cache_read': 0.01, 'output': 0.50},
    'claude-sonnet-5-5': {'input': 2.00, 'cache_write': 2.50, 'cache_read': 0.10, 'output': 10.00},
    'gpt-6-luna': {'input': 0.10, 'cache_write': 0.125, 'cache_read': 0.01, 'output': 0.50},
    QWEN: {'input': 0, 'cache_write': 0, 'cache_read': 0, 'output': 0},  # local
}
# A prompt over this many input tokens, cache reads and writes included, makes the whole request dearer: each input
# and cache price times the first factor, the output price times the second. A game peaks at about 22k.
LONG_PROMPT = {
    'claude-haiku-5-5': (100_000, 5, 5),
    'gpt-6-luna': (272_000, 2, 1.5),
}


def cost(model, usage):
    """USD at list price."""
    price = PRICES[model]
    prompt = usage['input_tokens'] + usage['cache_write_tokens'] + usage['cache_read_tokens']
    limit, scale, output_scale = LONG_PROMPT.get(model, (0, 1, 1))
    if prompt <= limit:
        scale = output_scale = 1
    inputs = (
        usage['input_tokens'] * price['input']
        + usage['cache_write_tokens'] * price['cache_write']
        + usage['cache_read_tokens'] * price['cache_read']
    )
    # One sum times scale, in this order, rounds as Anthropic's costs always have where the two scales are equal.
    total = scale * (inputs + output_scale / scale * usage['output_tokens'] * price['output'])
    if not 0 <= total < math.inf:  # a NaN in the shared ledger would switch off its cap for every later call
        raise ValueError(f'No finite cost for {usage}')
    return round(total / 1_000_000, 8)


class Anthropic:
    """The Messages API: content blocks in, content blocks out."""

    provider = 'anthropic'
    url = 'https://api.anthropic.com'  # fixed: neither a payload nor the environment can redirect egress
    beta = 'thinking-binding-controls-2026-08-01'  # lets a request set block binding, and adds input_transformations
    timeout = 600  # seconds
    incomplete = False  # a cut-off reply stops at max_tokens and is appended, so a reply follows each user turn
    # Locked for every call (decision 0005): never a forced tool, never thinking off, never another effort. Block
    # binding in "error" mode turns any edit of the history into a 400, and the top-level cache_control is automatic
    # caching, which moves the last breakpoint to the newest block.
    settings = {
        'max_tokens': 16_000,
        'tool_choice': {'type': 'auto', 'disable_parallel_tool_use': True},
        'thinking': {
            'type': 'adaptive',
            'display': 'summarized',
            'block_binding': {'prefix_mismatch_behavior': 'error'},
        },
        'output_config': {'effort': 'medium'},
        'cache_control': {'type': 'ephemeral'},
    }
    max_output = settings['max_tokens']

    @staticmethod
    def render(payload, game_id):
        """The Messages request for a worker's payload, a pure function of it. The rules block ends in the breakpoint
        the six residents of one model share; the persona follows it. Caching needs no key, so game_id goes unused."""
        rules, persona = payload['system']
        messages = []
        for turn in payload['history']:
            if 'native' in turn:
                messages.append({'role': 'assistant', 'content': turn['native']})
            else:
                messages += Anthropic.user_messages(turn)
        return {
            'model': payload['model'],
            'system': [
                {'type': 'text', 'text': rules, 'cache_control': {'type': 'ephemeral'}},
                {'type': 'text', 'text': persona},
            ],
            'tools': [payload['tool']],
            'messages': messages,
            **Anthropic.settings,
        }

    @staticmethod
    def user_messages(turn):
        """A neutral user turn: a result for each call of the last reply, or the text when it made none, then the
        reminder as a system message."""
        content = [
            {'type': 'tool_result', 'tool_use_id': r['call_id'], 'content': r['content'], 'is_error': r['is_error']}
            for r in turn['results']
        ]
        if turn['text'] is not None:
            content.append({'type': 'text', 'text': turn['text']})
        messages = [{'role': 'user', 'content': content}]
        if turn['reminder'] is not None:
            messages.append({'role': 'system', 'content': turn['reminder']})
        return messages

    @staticmethod
    def client(transport=None):
        """The SDK client for the fixed URL with the host's key. It never retries, since the broker does, never
        follows a redirect, never reads proxy settings and opens no spans: the broker opens its own, which carry no
        prompt text (design §3.6). A test passes a fake transport."""
        # The SDK sends this variable's headers over its own, so they could replace the key or add another credential.
        if 'ANTHROPIC_CUSTOM_HEADERS' in os.environ:
            raise RuntimeError(
                "Unset ANTHROPIC_CUSTOM_HEADERS: the broker sends only the host's key and its own headers"
            )
        return anthropic.Anthropic(
            api_key=os.environ['ANTHROPIC_API_KEY'],
            base_url=Anthropic.url,
            max_retries=0,
            timeout=Anthropic.timeout,
            open_telemetry=False,
            http_client=anthropic.DefaultHttpxClient(transport=transport, follow_redirects=False, trust_env=False),
        )

    @staticmethod
    def send(api, request):
        """The reply to a rendered request as the HTTP body carried it: an SDK object's dump can drop fields."""
        return api.beta.messages.with_raw_response.create(betas=[Anthropic.beta], **request).json()

    @staticmethod
    def parse(model, body):
        """The reply the worker gets: the content exactly as it arrived, with its calls, text, stop and usage parsed
        out."""
        native, used = body['content'], body['usage']
        usage = {
            'input_tokens': used['input_tokens'],
            'cache_write_tokens': used.get('cache_creation_input_tokens') or 0,  # these three may be null
            'cache_read_tokens': used.get('cache_read_input_tokens') or 0,
            'output_tokens': used['output_tokens'],
            'thinking_tokens': (used.get('output_tokens_details') or {}).get('thinking_tokens', 0),
        }
        return {
            'native': native,
            'calls': Anthropic.calls(native),
            'text': ''.join(b['text'] for b in native if b['type'] == 'text'),
            'stop': body['stop_reason'],
            'stop_details': body.get('stop_details'),
            'usage': usage,
            'cost_usd': cost(model, usage),
            'input_transformations': body.get('input_transformations'),
        }

    @staticmethod
    def calls(native):
        return [{'id': b['id'], 'name': b['name'], 'input': b['input']} for b in native if b['type'] == 'tool_use']

    @staticmethod
    def reasoning(native):
        """The text of each thinking block that carries any."""
        return [b['thinking'] for b in native if b['type'] == 'thinking' and b['thinking']]


def arguments(text):
    """A call's input from its JSON arguments: the object, or the text itself if it holds none, which the kernel then
    rejects with the fix."""
    try:
        return strict_json_loads(text)
    except ValueError:
        return text


def result(r):
    """A kernel result where the API has no error flag: an error says so first."""
    return f'ERROR: {r["content"]}' if r['is_error'] else r['content']


def function(tool):
    """The act tool as a function of the OpenAI-shaped APIs."""
    return {'name': tool['name'], 'description': tool['description'], 'parameters': tool['input_schema']}


def openai_client(url, key, timeout, transport):
    """An OpenAI SDK client for a fixed URL, set up as the Anthropic one is."""
    # The SDK sends these as headers: custom ones could replace or add a credential, as ANTHROPIC_CUSTOM_HEADERS
    # could, and the organization and project would choose who is billed.
    unset = [name for name in ('OPENAI_CUSTOM_HEADERS', 'OPENAI_ORG_ID', 'OPENAI_PROJECT_ID') if name in os.environ]
    if unset:
        raise RuntimeError(f"Unset {', '.join(unset)}: the broker sends only the host's key and its own headers")
    # httpx would fail on such a key while it builds the header, with the whole header in the error
    if not key.isascii() or not key.isprintable():
        raise RuntimeError('The OpenAI or oMLX key holds a character an HTTP header cannot carry')
    return openai.OpenAI(
        api_key=key,
        base_url=url,
        max_retries=0,
        timeout=timeout,
        http_client=openai.DefaultHttpxClient(transport=transport, follow_redirects=False, trust_env=False),
    )


class Responses:
    """OpenAI's Responses API: input items in, output items out. Every output item goes back as it arrived, its
    encrypted reasoning included, since nothing is stored (store false)."""

    provider = 'openai'
    url = 'https://api.openai.com/v1'  # fixed
    timeout = 600  # seconds
    incomplete = True  # a cut-off reply is never appended, so two user turns can meet
    max_output = 25_000

    @staticmethod
    def render(payload, game_id):
        """The Responses request for a worker's payload, a pure function of it and the game's id. The system blocks
        and the reminder are developer messages, and the cache key keeps one resident's calls together."""
        model = payload['model']
        items = [{'role': 'developer', 'content': text} for text in payload['system']]
        for turn in payload['history']:
            if 'native' in turn:
                items += turn['native']
                continue
            items += [
                {'type': 'function_call_output', 'call_id': r['call_id'], 'output': result(r)} for r in turn['results']
            ]
            if turn['text'] is not None:
                items.append({'role': 'user', 'content': turn['text']})
            if turn['reminder'] is not None:
                items.append({'role': 'developer', 'content': turn['reminder']})
        # Locked as Anthropic's are (decision 0005), with no sampling parameters.
        # Luna is not documented to keep reasoning across turns, so no context is asked; it reports the one it used.
        reasoning = {'effort': 'medium', 'summary': 'auto'}
        return {
            'model': model,
            'input': items,
            'tools': [{'type': 'function', **function(payload['tool']), 'strict': True}],
            'tool_choice': 'auto',
            'parallel_tool_calls': False,
            'reasoning': reasoning,
            'max_output_tokens': Responses.max_output,
            'include': ['reasoning.encrypted_content'],
            'store': False,
            'service_tier': 'default',  # auto takes the project's tier, and Fast bills twice what the ledger charges
            'prompt_cache_key': f'{game_id}/{payload["resident"]}',
        }

    @staticmethod
    def client(transport=None):
        return openai_client(Responses.url, os.environ['OPENAI_API_KEY'], Responses.timeout, transport)

    @staticmethod
    def send(api, request):
        return api.responses.with_raw_response.create(**request).http_response.json()

    @staticmethod
    def parse(model, body):
        """The reply the worker gets. A cut-off response keeps OpenAI's status, incomplete, and is never appended;
        reasoning_context records the reasoning memory the response says it used."""
        native, used = body['output'], body['usage']
        cached = used['input_tokens_details']  # input_tokens counts cache reads and writes too
        usage = {
            'input_tokens': used['input_tokens'] - cached['cached_tokens'] - cached['cache_write_tokens'],
            'cache_write_tokens': cached['cache_write_tokens'],
            'cache_read_tokens': cached['cached_tokens'],
            'output_tokens': used['output_tokens'],
            'thinking_tokens': used['output_tokens_details']['reasoning_tokens'],
        }
        parts = [part for item in native if item['type'] == 'message' for part in item['content']]
        stop = body['status']
        if stop == 'completed' and any(part['type'] == 'refusal' for part in parts):
            stop = 'refusal'
        return {
            'native': native,
            'calls': Responses.calls(native),
            'text': ''.join(part['text'] for part in parts if part['type'] == 'output_text'),
            'stop': stop,
            'stop_details': body['incomplete_details'],
            'usage': usage,
            'cost_usd': cost(model, usage),
            'input_transformations': None,
            'reasoning_context': (body.get('reasoning') or {}).get('context'),  # both documented as optional
        }

    @staticmethod
    def calls(native):
        return [
            {'id': i['call_id'], 'name': i['name'], 'input': arguments(i['arguments'])}
            for i in native
            if i['type'] == 'function_call'
        ]

    @staticmethod
    def reasoning(native):
        """The text of each reasoning summary."""
        return [s['text'] for i in native if i['type'] == 'reasoning' for s in i['summary']]


class Chat:
    """Local Qwen through oMLX's chat completions on loopback: messages in, one message out. The model runs at its own
    defaults (decision 0005): the chat template thinks at effort xhigh, so no chat_template_kwargs, and it samples as
    its generation_config.json says, since oMLX applies only its global sampling. Without strict schemas, the
    kernel validates each act."""

    provider = 'omlx'
    url = 'http://127.0.0.1:8000/v1'  # loopback only
    timeout = 1800  # seconds: 32,768 tokens at about 21 a second, plus prefill
    incomplete = True  # as on OpenAI
    max_output = 32_768
    home = Path.home() / '.omlx'
    directory = 'Qwen3.8-27B-oQ8e-fp16-mtp'  # the model behind QWEN in oMLX's models
    sampling = ('temperature', 'top_p', 'top_k', 'min_p', 'presence_penalty', 'repetition_penalty')

    @staticmethod
    def render(payload, game_id):
        """The chat request for a worker's payload, with the model's sampling. Its chat template takes one system
        message, first, so the system blocks share it and the reminder joins the user text. oMLX caches prefixes
        without a key."""
        messages = [{'role': 'system', 'content': '\n\n'.join(payload['system'])}]
        for turn in payload['history']:
            if 'native' in turn:
                messages += turn['native']
                continue
            messages += [{'role': 'tool', 'tool_call_id': r['call_id'], 'content': result(r)} for r in turn['results']]
            text = [t for t in (turn['text'], turn['reminder']) if t is not None]
            if text:
                messages.append({'role': 'user', 'content': '\n\n'.join(text)})
        config = json.loads((Chat.home / 'models' / Chat.directory / 'generation_config.json').read_text())
        return {
            'model': payload['model'],
            'messages': messages,
            'tools': [{'type': 'function', 'function': function(payload['tool'])}],  # no strict: oMLX has none
            'tool_choice': 'auto',
            'max_tokens': Chat.max_output,
            **{k: config[k] for k in Chat.sampling if k in config},
        }

    @staticmethod
    def client(transport=None):
        """The oMLX client, with the key oMLX's settings file sets, or with none set a placeholder that oMLX does not
        check: given no key, the SDK would send OPENAI_API_KEY, the hosted key, to oMLX."""
        settings = json.loads((Chat.home / 'settings.json').read_text())
        return openai_client(Chat.url, settings['auth'].get('api_key') or 'unset', Chat.timeout, transport)

    @staticmethod
    def send(api, request):
        # The SDK's create() has no top_k, min_p or repetition_penalty, so the body goes as rendered.
        body = json.loads(api.post('/chat/completions', body=request, cast_to=str))
        if 'error' in body:  # past oMLX's 2 s keepalive grace the status is already 200, so a failure comes in the body
            raise openai.APIError(body['error']['message'], None, body=body['error'])
        return body

    @staticmethod
    def parse(model, body):
        """The reply the worker gets: native is the message as returned, in a list; oMLX omits content when it is
        null. A reply cut off at max_tokens (length) while it thinks or writes text is incomplete, as on OpenAI; a
        cut or malformed call comes as an error instead. A local call costs nothing."""
        [choice] = body['choices']
        message, used = choice['message'], body['usage']
        usage = {
            'input_tokens': used['prompt_tokens'],
            'cache_write_tokens': 0,  # oMLX's prefix cache is free
            'cache_read_tokens': 0,
            'output_tokens': used['completion_tokens'],
            'thinking_tokens': (used.get('completion_tokens_details') or {}).get('reasoning_tokens', 0),
        }
        finish = choice['finish_reason']
        return {
            'native': [message],
            'calls': Chat.calls([message]),
            'text': message.get('content') or '',
            'stop': 'incomplete' if finish == 'length' else finish,
            'stop_details': None,
            'usage': usage,
            'cost_usd': cost(model, usage),
            'input_transformations': None,
        }

    @staticmethod
    def calls(native):
        return [
            {'id': c['id'], 'name': c['function']['name'], 'input': arguments(c['function']['arguments'])}
            for m in native
            for c in m.get('tool_calls') or []
        ]

    @staticmethod
    def reasoning(native):
        """The reasoning of each message that carries any, the field the chat template reads back."""
        return [m['reasoning_content'] for m in native if m.get('reasoning_content')]


ADAPTERS = {
    'claude-haiku-5-5': Anthropic,
    'claude-sonnet-5-5': Anthropic,
    'gpt-6-luna': Responses,
    QWEN: Chat,
}
