"""The OpenAI Responses adapter against a fake transport: the request it renders, the client it sends with, the reply it
parses and the cost, then whole games through the broker. No test calls a model."""

import io
import itertools
import json

import httpx2
import openai
import pytest
import report
import test_broker
import test_game
import transcript

from handshake import broker, game, island, providers, replay
from handshake.broker import Broker
from handshake.game import Stop
from handshake.island import IDS
from handshake.protocol import encode
from handshake.providers import Responses

GAME = 'g0001'
LUNA = 'gpt-6-luna'
TOOL = island.act_tool('handshake-talk')
IDLE = {'answers': [], 'deliveries': [], 'offers': [], 'say': []}
REASONING = {
    'id': 'rs_01',
    'type': 'reasoning',
    'summary': [{'type': 'summary_text', 'text': 'Rope is scarce, so I wait.'}],
    'encrypted_content': 'gAAAAABo8Xq3-encrypted-reasoning',
}
CALL = {
    'id': 'fc_01',
    'type': 'function_call',
    'status': 'completed',
    'arguments': json.dumps(IDLE),
    'call_id': 'call_01',
    'name': 'act',
}
WAIT = {
    'id': 'msg_01',
    'type': 'message',
    'status': 'completed',
    'role': 'assistant',
    'content': [{'type': 'output_text', 'text': 'I will wait.', 'annotations': [], 'logprobs': []}],
}
REMINDER = 'Reminder: you are Mara.'
FIX = 'offers: to must be another resident.'
# Mara's second turn, on its second attempt: her first act applied, then an invalid act.
PAYLOAD = {
    'resident': 'mara',
    'round': 2,
    'attempt': 2,
    'model': LUNA,
    'system': ['RULES', 'PERSONA'],
    'tool': TOOL,
    'history': [
        {'results': [], 'text': 'The game begins.', 'reminder': REMINDER},
        {'native': [REASONING, CALL], 'call_ids': ['call_01']},
        {
            'results': [{'call_id': 'call_01', 'content': 'Act applied.', 'is_error': False}],
            'text': None,
            'reminder': REMINDER,
        },
        {'native': [REASONING, WAIT, CALL | {'call_id': 'call_02'}], 'call_ids': ['call_02']},
        {'results': [{'call_id': 'call_02', 'content': FIX, 'is_error': True}], 'text': None, 'reminder': None},
    ],
}
USAGE = {
    'input_tokens': 4020,  # 20 uncached, 3,000 read from the cache and 1,000 written to it
    'input_tokens_details': {'cached_tokens': 3000, 'cache_write_tokens': 1000},
    'output_tokens': 800,
    'output_tokens_details': {'reasoning_tokens': 500},
    'total_tokens': 4820,
}


def response(*output, status='completed', details=None, model=LUNA, context='current_turn'):
    """A Responses body as the API sends it."""
    return {
        'id': 'resp_01',
        'object': 'response',
        'created_at': 1760800000,
        'status': status,
        'error': None,
        'incomplete_details': details,
        'model': model,
        'output': list(output),
        'parallel_tool_calls': False,
        'reasoning': {'effort': 'medium', 'summary': 'auto', 'context': context},
        'store': False,
        'usage': USAGE,
    }


@pytest.fixture(autouse=True)
def host(tmp_path, monkeypatch):
    """The host's key in the environment and the ledger in tmp_path."""
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    monkeypatch.setattr(broker, 'LEDGER', tmp_path / 'spend.jsonl')


def test_a_request_has_the_locked_settings_and_the_conversation_as_items():
    assert Responses.render(PAYLOAD, GAME) == {
        'model': LUNA,
        'input': [
            {'role': 'developer', 'content': 'RULES'},
            {'role': 'developer', 'content': 'PERSONA'},
            {'role': 'user', 'content': 'The game begins.'},
            {'role': 'developer', 'content': REMINDER},
            REASONING,
            CALL,
            {'type': 'function_call_output', 'call_id': 'call_01', 'output': 'Act applied.'},
            {'role': 'developer', 'content': REMINDER},
            REASONING,
            WAIT,
            CALL | {'call_id': 'call_02'},
            {'type': 'function_call_output', 'call_id': 'call_02', 'output': f'ERROR: {FIX}'},
        ],
        'tools': [
            {
                'type': 'function',
                'name': 'act',
                'description': TOOL['description'],
                'parameters': TOOL['input_schema'],
                'strict': True,
            }
        ],
        'tool_choice': 'auto',
        'parallel_tool_calls': False,
        'reasoning': {'effort': 'medium', 'summary': 'auto'},
        'max_output_tokens': 25_000,
        'include': ['reasoning.encrypted_content'],
        'store': False,
        'service_tier': 'default',
        'prompt_cache_key': 'g0001/mara',
    }


def test_luna_is_asked_for_no_reasoning_context_since_none_is_documented_for_it():
    assert Responses.render(PAYLOAD, GAME)['reasoning'] == {'effort': 'medium', 'summary': 'auto'}


def test_a_request_goes_to_the_fixed_url_with_the_hosts_key(monkeypatch):
    monkeypatch.setenv('OPENAI_BASE_URL', 'https://elsewhere.example/v1')
    seen = []

    def api(request):
        seen.append(request)
        return httpx2.Response(200, json=response(REASONING, CALL))

    request = Responses.render(PAYLOAD, GAME)
    assert Responses.send(Responses.client(httpx2.MockTransport(api)), request) == response(REASONING, CALL)
    [sent] = seen
    assert str(sent.url) == 'https://api.openai.com/v1/responses'
    assert sent.headers['authorization'] == 'Bearer test-key'
    assert json.loads(sent.content) == request


@pytest.mark.parametrize(
    ('name', 'value'),
    [('OPENAI_CUSTOM_HEADERS', 'Authorization: Bearer x'), ('OPENAI_ORG_ID', 'org-x'), ('OPENAI_PROJECT_ID', 'proj_x')],
)
def test_no_client_is_made_while_the_environment_could_add_headers(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=f'Unset {name}:'):
        Responses.client()


def test_no_client_is_made_with_a_key_a_header_cannot_carry(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key​')  # a zero-width space pasted with the key
    journal = io.StringIO()
    with pytest.raises(RuntimeError, match='cannot carry') as refused:
        Broker(GAME, test_broker.cfg(seats=dict.fromkeys(IDS, LUNA)), journal)
    assert 'test-key' not in str(refused.value) and journal.getvalue() == ''


def test_a_redirect_is_never_followed():
    seen = []

    def api(request):
        seen.append(request)
        return httpx2.Response(307, headers={'location': 'https://elsewhere.example/v1/responses'})

    with pytest.raises(openai.APIStatusError):
        Responses.send(Responses.client(httpx2.MockTransport(api)), Responses.render(PAYLOAD, GAME))
    assert len(seen) == 1


def test_a_reply_keeps_every_item_as_it_arrived_and_records_its_reasoning_memory():
    assert Responses.parse(LUNA, response(REASONING, WAIT, CALL)) == {
        'native': [REASONING, WAIT, CALL],  # the encrypted reasoning included
        'calls': [{'id': 'call_01', 'name': 'act', 'input': IDLE}],
        'text': 'I will wait.',
        'stop': 'completed',
        'stop_details': None,
        'usage': {
            'input_tokens': 20,
            'cache_write_tokens': 1000,
            'cache_read_tokens': 3000,
            'output_tokens': 800,
            'thinking_tokens': 500,
        },
        'cost_usd': test_broker.COST,  # Luna's list prices are Haiku's
        'input_transformations': None,
        'reasoning_context': 'current_turn',
    }


def test_a_reply_that_names_no_reasoning_memory_records_none():
    body = response(REASONING, CALL)
    body['reasoning'] = {'effort': 'medium', 'summary': 'auto'}
    assert Responses.parse(LUNA, body)['reasoning_context'] is None
    body['reasoning'] = None
    assert Responses.parse(LUNA, body)['reasoning_context'] is None
    del body['reasoning']
    assert Responses.parse(LUNA, body)['reasoning_context'] is None


def test_a_cut_off_reply_is_incomplete_with_its_reason():
    reply = Responses.parse(LUNA, response(REASONING, status='incomplete', details={'reason': 'max_output_tokens'}))
    assert (reply['stop'], reply['stop_details'], reply['calls']) == ('incomplete', {'reason': 'max_output_tokens'}, [])


def test_a_reply_with_no_output_items_stops_the_game_as_an_empty_reply():
    cfg = test_broker.cfg(seats=dict.fromkeys(IDS, LUNA))
    the_broker = Broker(GAME, cfg, io.StringIO(), httpx2.MockTransport(test_broker.answering(response())))
    assert game.Session(cfg, the_broker, io.StringIO()).play() == 'empty_reply'


def test_a_refusal_is_a_refusal():
    refused = WAIT | {'content': [{'type': 'refusal', 'refusal': 'I would rather not.'}]}
    reply = Responses.parse(LUNA, response(REASONING, refused))
    assert (reply['stop'], reply['calls'], reply['text']) == ('refusal', [], '')


def test_arguments_that_hold_no_object_reach_the_kernel_as_text():
    [call] = Responses.calls([CALL | {'arguments': '{"answers": ['}])
    assert call['input'] == '{"answers": ['
    with pytest.raises(island.Reject, match='act: the input must be an object'):
        island.Game(1, 'handshake-talk').act(call['input'])


def test_cost_is_at_list_price_and_a_prompt_over_272k_tokens_makes_the_whole_request_dearer():
    def usage(uncached):  # with 100,000 uncached, a prompt of 272,000 tokens
        return {
            'input_tokens': uncached,
            'cache_write_tokens': 100_000,
            'cache_read_tokens': 72_000,
            'output_tokens': 1000,
        }

    # Luna: 100,000 × $0.10 + 100,000 × $0.125 + 72,000 × $0.01 + 1,000 × $0.50 per million. One more token doubles
    # every input and cache price and multiplies output's by 1.5.
    assert (providers.cost(LUNA, usage(100_000)), providers.cost(LUNA, usage(100_001))) == (0.02372, 0.0471902)


def test_openai_errors_are_retried_or_stop_the_game_as_anthropic_ones_are(monkeypatch):
    monkeypatch.setattr(broker.time, 'sleep', lambda seconds: None)
    cfg = test_broker.cfg(seats=dict.fromkeys(IDS, LUNA))
    api = test_broker.answering(test_broker.error(429), test_broker.DROPPED, response(REASONING, CALL))
    assert Broker(GAME, cfg, io.StringIO(), httpx2.MockTransport(api))(test_broker.opening(model=LUNA))['retries'] == 2
    api = test_broker.answering(test_broker.error(400, 'Invalid input'))
    with pytest.raises(Stop, match='^provider_error$'):
        Broker(GAME, cfg, io.StringIO(), httpx2.MockTransport(api))(test_broker.opening(model=LUNA))


def test_a_rejected_key_stops_the_game_without_journaling_any_of_the_key():
    said = 'Incorrect API key provided: test-****-key. You can find your API key at https://platform.openai.com.'
    wrong = {'message': said, 'type': 'invalid_request_error', 'param': None, 'code': 'invalid_api_key'}
    api, journal = test_broker.answering(httpx2.Response(401, json={'error': wrong})), io.StringIO()
    cfg = test_broker.cfg(seats=dict.fromkeys(IDS, LUNA))
    with pytest.raises(Stop, match='^provider_error$'):
        Broker(GAME, cfg, journal, httpx2.MockTransport(api))(test_broker.opening(model=LUNA))
    [stop] = test_broker.lines(journal.getvalue())
    assert stop['error'] == 'Error code: 401 - invalid_api_key'


# ------------------------------------------------------------------------------------------------ whole games


def play(arm, model, api, native, transport=None):
    """A game in process with every seat played by model behind the broker. test_game's scripted model plays
    every_kind, and api answers each request with that reply made native by native(reply). Return the config, the
    journal's lines and the closing reason."""
    fake, payloads = test_game.Fake(test_game.every_kind(arm)), []
    api.answer = lambda: native(fake(payloads[-1]))
    cfg = test_broker.cfg(arm, dict.fromkeys(IDS, model))
    the_broker, journal = Broker(GAME, cfg, io.StringIO(), transport), io.StringIO()

    def model_call(payload):
        payloads.append(payload)
        return the_broker(payload)

    session = game.Session(cfg, model_call, journal)
    fake.game = session.game
    reason = session.play()
    return cfg, [json.loads(line) for line in journal.getvalue().splitlines()], reason


def appended_only(api, key, output, incomplete):
    """Assert that each resident's request[key] is its last one's, then the last reply's output(reply) exactly as the
    API sent it, unless the reply was incomplete, then any new user items, and that nothing else changes. Return how
    many replies were incomplete and how many requests went again unchanged."""
    resent = 0
    for a in IDS:
        mine = [(q, r) for q, r in zip(api.requests, api.replies, strict=True) if f'(id {a})' in encode(q[key][:2])]
        assert mine
        for (before, reply), (after, _) in itertools.pairwise(mine):
            n, out = len(before[key]), [] if incomplete(reply) else output(reply)
            assert after[key][: n + len(out)] == before[key] + out
            assert not any(item in output(reply) for item in after[key][n + len(out) :])
            assert {k: v for k, v in after.items() if k != key} == {k: v for k, v in before.items() if k != key}
            resent += after == before
    return sum(map(incomplete, api.replies)), resent


item_ids = itertools.count(1)


def native(reply):
    """test_game's scripted reply as the Responses API sends it: thinking as encrypted reasoning, a max_tokens stop as
    an incomplete response, a refusal's text as a refusal."""
    output = []
    for b in reply['native']:
        n = next(item_ids)
        if b['type'] == 'thinking':
            summary = [{'type': 'summary_text', 'text': b['thinking']}] if b['thinking'] else []
            output.append(REASONING | {'id': f'rs_{n}', 'summary': summary, 'encrypted_content': f'gAAAA{n}'})
        elif b['type'] == 'tool_use':
            output.append(CALL | {'id': f'fc_{n}', 'call_id': b['id'], 'arguments': json.dumps(b['input'])})
        else:
            words = {'type': 'output_text', 'text': b['text'], 'annotations': [], 'logprobs': []}
            part = {'type': 'refusal', 'refusal': b['text']} if reply['stop'] == 'refusal' else words
            output.append(WAIT | {'id': f'msg_{n}', 'content': [part]})
    if reply['stop'] == 'max_tokens':
        return response(*output, status='incomplete', details={'reason': 'max_output_tokens'})
    return response(*output)


@pytest.mark.parametrize('arm', island.ARMS)
def test_every_item_goes_back_as_it_arrived_and_an_incomplete_reply_sends_the_same_request_again(arm):
    api = test_broker.Api(None)
    cfg, journal, reason = play(arm, LUNA, api, native, httpx2.MockTransport(api))
    assert reason == 'complete'
    cut = appended_only(api, 'input', lambda body: body['output'], lambda body: body['status'] == 'incomplete')
    assert cut == (3, 2)  # Mara's replies cut off on attempts 3, 1 and 2; the last two go again within their turns
    calls = [line['reply'] for line in journal if line['kind'] == 'call']
    assert {reply['reasoning_context'] for reply in calls} == {'current_turn'}
    assert replay.replay(cfg, journal, reason) == journal


def test_the_report_and_the_transcript_read_a_luna_game_as_they_read_the_same_game_on_anthropic():
    api = test_broker.Api(None)
    cfg, luna, reason = play('handshake-talk', LUNA, api, native, httpx2.MockTransport(api))
    _, _, haiku = test_game.play('handshake-talk', script=test_game.every_kind('handshake-talk'))

    def read(journal, model):
        calls = [c for t in report.model_turns(journal) for c in t['calls']]
        # A refusal's words are text on Anthropic and a refusal part on OpenAI, so only Anthropic's count as discarded.
        return [
            (c['ending'], report.thought(c['reply'], model), c['ending'] != 'refusal' and report.discarded(c['reply']))
            for c in calls
        ]

    assert read(luna, LUNA) == read(haiku, test_game.MODEL)
    assert ('max_tokens', True, False) in read(luna, LUNA)  # an incomplete reply counts as cut off
    text = report.thinking(
        [c | {'player': LUNA, 'arm': cfg['arm'], 'cast': 'luna'} for t in report.model_turns(luna) for c in t['calls']]
    )
    assert f'- {LUNA}: current_turn in {len(read(luna, LUNA))} calls' in text
    page = transcript.page({'game': 'luna', 'config': cfg, 'reason': reason, 'journal': luna})
    assert 'cut off at max_tokens' in page and 'class="thought"' in page
