"""The Anthropic adapter: the request it renders, the client it sends with, the reply it parses and the cost. A fake
transport stands in for the network."""

import json
import math

import anthropic
import httpx2
import pytest

from handshake import island, providers

MODEL = 'claude-haiku-5-5'
TOOL = island.act_tool('handshake-talk')
THINK = {'type': 'thinking', 'thinking': 'Rope is scarce, so I wait.', 'signature': 'EpYBCkgIBxABGAIiQ'}
CALL = {
    'type': 'tool_use',
    'id': 'toolu_01',
    'name': 'act',
    'input': {'answers': [], 'deliveries': [], 'offers': [], 'say': []},
    'caller': {'type': 'direct'},
}
REMINDER = 'Reminder: you are Mara.'
# Mara's second turn, on its second attempt: her first act applied, then a reply with no call.
PAYLOAD = {
    'resident': 'mara',
    'round': 2,
    'attempt': 2,
    'model': MODEL,
    'system': ['RULES', 'PERSONA'],
    'tool': TOOL,
    'history': [
        {'results': [], 'text': 'The game begins.', 'reminder': REMINDER},
        {'native': [THINK, CALL], 'call_ids': ['toolu_01']},
        {
            'results': [{'call_id': 'toolu_01', 'content': 'Act applied.', 'is_error': False}],
            'text': None,
            'reminder': REMINDER,
        },
        {'native': [THINK, {'type': 'text', 'text': 'I will wait.'}], 'call_ids': []},
        {'results': [], 'text': 'Call act to take your turn.', 'reminder': None},
    ],
}
# A reply P0 recorded (research/probe/runs), its thinking and signature shortened.
BODY = {
    'model': MODEL,
    'id': 'msg_011CgAG4X9S571xCtPiMaxRf',
    'type': 'message',
    'role': 'assistant',
    'container': None,
    'content': [THINK, CALL],
    'stop_reason': 'tool_use',
    'stop_sequence': None,
    'stop_details': None,
    'usage': {
        'input_tokens': 2,
        'cache_creation_input_tokens': 104,
        'cache_read_input_tokens': 3066,
        'cache_creation': {'ephemeral_5m_input_tokens': 104, 'ephemeral_1h_input_tokens': 0},
        'output_tokens': 944,
        'output_tokens_details': {'thinking_tokens': 586},
        'service_tier': 'standard',
        'inference_geo': 'global',
        'speed': 'standard',
    },
    'input_transformations': [],
    'diagnostics': None,
}


def test_a_request_has_the_locked_settings_and_the_conversation_as_messages():
    assert providers.Anthropic.render(PAYLOAD, 'g0001') == {
        'model': MODEL,
        'system': [
            {'type': 'text', 'text': 'RULES', 'cache_control': {'type': 'ephemeral'}},
            {'type': 'text', 'text': 'PERSONA'},
        ],
        'tools': [TOOL],
        'messages': [
            {'role': 'user', 'content': [{'type': 'text', 'text': 'The game begins.'}]},
            {'role': 'system', 'content': REMINDER},
            {'role': 'assistant', 'content': [THINK, CALL]},
            {
                'role': 'user',
                'content': [
                    {'type': 'tool_result', 'tool_use_id': 'toolu_01', 'content': 'Act applied.', 'is_error': False}
                ],
            },
            {'role': 'system', 'content': REMINDER},
            {'role': 'assistant', 'content': [THINK, {'type': 'text', 'text': 'I will wait.'}]},
            {'role': 'user', 'content': [{'type': 'text', 'text': 'Call act to take your turn.'}]},
        ],
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


def test_a_request_goes_to_the_fixed_url_with_the_hosts_key_and_the_binding_beta(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')
    monkeypatch.setenv('ANTHROPIC_AUTH_TOKEN', 'another-credential')
    monkeypatch.setenv('ANTHROPIC_BASE_URL', 'https://elsewhere.example')
    seen = []

    def api(request):
        seen.append(request)
        return httpx2.Response(200, json=BODY)

    request = providers.Anthropic.render(PAYLOAD, 'g0001')
    assert providers.Anthropic.send(providers.Anthropic.client(httpx2.MockTransport(api)), request) == BODY
    [sent] = seen
    assert str(sent.url) == 'https://api.anthropic.com/v1/messages?beta=true'
    assert sent.headers['x-api-key'] == 'test-key' and 'authorization' not in sent.headers
    assert sent.headers['anthropic-beta'] == 'thinking-binding-controls-2026-08-01'
    assert json.loads(sent.content) == request


def test_no_client_is_made_while_custom_headers_could_replace_the_key(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')
    monkeypatch.setenv('ANTHROPIC_CUSTOM_HEADERS', 'X-Api-Key: other\nAuthorization: Bearer x')
    with pytest.raises(RuntimeError, match='Unset ANTHROPIC_CUSTOM_HEADERS'):
        providers.Anthropic.client()


def test_a_redirect_is_never_followed(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')
    seen = []

    def api(request):
        seen.append(request)
        return httpx2.Response(307, headers={'location': 'https://elsewhere.example/v1/messages'})

    with pytest.raises(anthropic.APIStatusError):
        providers.Anthropic.send(
            providers.Anthropic.client(httpx2.MockTransport(api)), providers.Anthropic.render(PAYLOAD, 'g0001')
        )
    assert len(seen) == 1


def test_a_reply_keeps_its_content_as_it_arrived_and_parses_out_the_rest():
    assert providers.Anthropic.parse(MODEL, BODY) == {
        'native': [THINK, CALL],  # the call's caller field included
        'calls': [{'id': 'toolu_01', 'name': 'act', 'input': CALL['input']}],
        'text': '',
        'stop': 'tool_use',
        'stop_details': None,
        'usage': {
            'input_tokens': 2,
            'cache_write_tokens': 104,
            'cache_read_tokens': 3066,
            'output_tokens': 944,
            'thinking_tokens': 586,
        },
        'cost_usd': 0.00051586,  # as P0's ledger charged it
        'input_transformations': [],
    }


def test_a_refusal_keeps_its_stop_details():
    details = {'type': 'refusal', 'category': None, 'explanation': 'The request was declined.'}
    refused = {'content': [{'type': 'text', 'text': 'I would rather not.'}], 'stop_reason': 'refusal'}
    reply = providers.Anthropic.parse(MODEL, BODY | refused | {'stop_details': details})
    assert reply['stop'] == 'refusal' and reply['stop_details'] == details
    assert reply['calls'] == [] and reply['text'] == 'I would rather not.'


def usage(input_tokens, output_tokens):
    return {
        'input_tokens': input_tokens,
        'cache_write_tokens': 0,
        'cache_read_tokens': 0,
        'output_tokens': output_tokens,
    }


def test_cost_is_at_list_price_per_million_tokens():
    every = {'input_tokens': 10**6, 'cache_write_tokens': 10**6, 'cache_read_tokens': 10**6, 'output_tokens': 10**6}
    assert providers.cost('claude-sonnet-5-5', every) == 2.00 + 2.50 + 0.10 + 10.00
    assert providers.cost('claude-sonnet-5-5', usage(1000, 1000)) == 0.012


def test_haiku_costs_five_times_as_much_above_a_100k_prompt():
    assert providers.cost('claude-haiku-5-5', usage(100_000, 1000)) == 0.0105
    assert providers.cost('claude-haiku-5-5', usage(100_001, 1000)) == 0.0525005
    # Five times the whole sum, not the input and output apart, which round 1e-8 higher here.
    cached = {'input_tokens': 2, 'cache_write_tokens': 1147, 'cache_read_tokens': 100_273, 'output_tokens': 1479}
    assert providers.cost('claude-haiku-5-5', cached) == 0.00942902


@pytest.mark.parametrize('count', [math.nan, math.inf])
def test_a_usage_count_that_is_not_finite_has_no_cost(count):
    with pytest.raises(ValueError, match='No finite cost'):
        providers.cost('claude-haiku-5-5', usage(1000, 1000) | {'output_tokens': count})
