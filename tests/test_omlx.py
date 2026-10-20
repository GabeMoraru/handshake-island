"""The local oMLX chat-completions adapter against a fake oMLX server on loopback: the request it renders, where it
sends it, the reply it parses, then whole games through the broker. No test calls a model."""

import http.server
import io
import json
import threading

import openai
import pytest
import test_broker
from test_openai import FIX, IDLE, REMINDER, TOOL, appended_only, play

from handshake import broker, game, island, replay
from handshake.game import Stop
from handshake.island import IDS
from handshake.providers import QWEN, Chat

MESSAGE = {
    'role': 'assistant',
    'content': '',
    'reasoning_content': 'Rope is scarce, so I wait.',
    'tool_calls': [{'id': 'call_01', 'type': 'function', 'function': {'name': 'act', 'arguments': json.dumps(IDLE)}}],
}
AGAIN = MESSAGE | {'tool_calls': [MESSAGE['tool_calls'][0] | {'id': 'call_02'}]}
# Mara's second turn, on its second attempt: her first act applied, then an invalid act.
PAYLOAD = {
    'resident': 'mara',
    'round': 2,
    'attempt': 2,
    'model': QWEN,
    'system': ['RULES', 'PERSONA'],
    'tool': TOOL,
    'history': [
        {'results': [], 'text': 'The game begins.', 'reminder': REMINDER},
        {'native': [MESSAGE], 'call_ids': ['call_01']},
        {
            'results': [{'call_id': 'call_01', 'content': 'Act applied.', 'is_error': False}],
            'text': None,
            'reminder': REMINDER,
        },
        {'native': [AGAIN], 'call_ids': ['call_02']},
        {'results': [{'call_id': 'call_02', 'content': FIX, 'is_error': True}], 'text': None, 'reminder': None},
    ],
}
SAMPLING = {'temperature': 1.0, 'top_k': 20, 'top_p': 0.95}


def completion(message, finish='tool_calls'):
    """A chat-completions body as oMLX sends it."""
    return {
        'id': 'chatcmpl-01',
        'object': 'chat.completion',
        'created': 1760800000,
        'model': QWEN,
        'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
        'usage': {'prompt_tokens': 4000, 'completion_tokens': 800, 'total_tokens': 4800},
    }


class Omlx(http.server.ThreadingHTTPServer):
    """oMLX on loopback. answer() gives each request's answer: a body, or a status to redirect with. It keeps each
    request's path and headers, and the request and reply bodies, as JSON values."""

    def __init__(self):
        self.answer, self.seen, self.requests, self.replies = None, [], [], []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                server.requests.append(json.loads(self.rfile.read(int(self.headers['content-length']))))
                server.seen.append((self.path, self.headers))
                answer = server.answer()
                if isinstance(answer, int):
                    self.send_response(answer)
                    self.send_header('location', 'http://127.0.0.1:9/v1/chat/completions')
                    self.send_header('content-length', '0')
                    self.end_headers()
                    return
                server.replies.append(answer)
                body = json.dumps(answer).encode()
                self.send_response(200)
                self.send_header('content-type', 'application/json')
                self.send_header('content-length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        super().__init__(('127.0.0.1', 0), Handler)


@pytest.fixture
def omlx(tmp_path, monkeypatch):
    """The fake oMLX at Chat.url, oMLX's settings with a key and the model's generation_config.json in its home, and
    the ledger in tmp_path."""
    model = tmp_path / 'models' / Chat.directory
    model.mkdir(parents=True)
    (model / 'generation_config.json').write_text(json.dumps({'do_sample': True, 'eos_token_id': [1, 2]} | SAMPLING))
    (tmp_path / 'settings.json').write_text(json.dumps({'auth': {'api_key': 'local-key'}}))
    monkeypatch.setattr(Chat, 'home', tmp_path)
    monkeypatch.setattr(broker, 'LEDGER', tmp_path / 'spend.jsonl')
    server = Omlx()
    monkeypatch.setattr(Chat, 'url', f'http://127.0.0.1:{server.server_port}/v1')
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def test_the_omlx_url_is_loopback():
    assert Chat.url == 'http://127.0.0.1:8000/v1'


def test_a_request_has_the_models_own_defaults_and_the_conversation_as_messages(omlx):
    assert Chat.render(PAYLOAD, 'g0001') == {
        'model': QWEN,
        'messages': [
            {'role': 'system', 'content': 'RULES\n\nPERSONA'},
            {'role': 'user', 'content': f'The game begins.\n\n{REMINDER}'},
            MESSAGE,
            {'role': 'tool', 'tool_call_id': 'call_01', 'content': 'Act applied.'},
            {'role': 'user', 'content': REMINDER},
            AGAIN,
            {'role': 'tool', 'tool_call_id': 'call_02', 'content': f'ERROR: {FIX}'},
        ],
        'tools': [
            {
                'type': 'function',
                'function': {'name': 'act', 'description': TOOL['description'], 'parameters': TOOL['input_schema']},
            }
        ],
        'tool_choice': 'auto',
        'max_tokens': 32_768,
        **SAMPLING,  # no chat_template_kwargs: the template thinks at its default effort
    }


def test_a_request_goes_to_omlx_with_its_key_whatever_the_environment_says(omlx, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'hosted-key')
    monkeypatch.setenv('OPENAI_BASE_URL', 'https://elsewhere.example/v1')
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:9')
    monkeypatch.setenv('ALL_PROXY', 'http://127.0.0.1:9')
    omlx.answer = lambda: completion(MESSAGE)
    request = Chat.render(PAYLOAD, 'g0001')
    assert Chat.send(Chat.client(), request) == completion(MESSAGE)
    [(path, headers)] = omlx.seen
    assert path == '/v1/chat/completions' and headers['authorization'] == 'Bearer local-key'
    assert omlx.requests == [request]


def test_with_no_key_in_omlxs_settings_the_hosted_key_never_goes_to_omlx(omlx, monkeypatch):
    (Chat.home / 'settings.json').write_text(json.dumps({'auth': {}}))
    monkeypatch.setenv('OPENAI_API_KEY', 'hosted-key')
    omlx.answer = lambda: completion(MESSAGE)
    Chat.send(Chat.client(), Chat.render(PAYLOAD, 'g0001'))
    [(_, headers)] = omlx.seen
    assert headers['authorization'] == 'Bearer unset'


def test_a_redirect_is_never_followed(omlx):
    omlx.answer = lambda: 307
    with pytest.raises(openai.APIStatusError):
        Chat.send(Chat.client(), Chat.render(PAYLOAD, 'g0001'))
    assert len(omlx.requests) == 1


def test_a_reply_keeps_its_message_as_it_arrived_and_costs_nothing():
    assert Chat.parse(QWEN, completion(MESSAGE)) == {
        'native': [MESSAGE],
        'calls': [{'id': 'call_01', 'name': 'act', 'input': IDLE}],
        'text': '',
        'stop': 'tool_calls',
        'stop_details': None,
        'usage': {
            'input_tokens': 4000,
            'cache_write_tokens': 0,
            'cache_read_tokens': 0,
            'output_tokens': 800,
            'thinking_tokens': 0,
        },
        'cost_usd': 0,
        'input_transformations': None,
    }


def test_a_reply_that_only_calls_act_has_no_content_and_no_text():
    called = {k: v for k, v in MESSAGE.items() if k != 'content'}  # oMLX leaves out a null field
    reply = Chat.parse(QWEN, completion(called))
    assert (reply['native'], reply['text'], reply['calls']) == (
        [called],
        '',
        [{'id': 'call_01', 'name': 'act', 'input': IDLE}],
    )


def test_a_reply_cut_off_while_it_thinks_is_incomplete():
    cut = {'role': 'assistant', 'reasoning_content': 'Rope is scarce, so'}
    reply = Chat.parse(QWEN, completion(cut, finish='length'))
    assert (reply['stop'], reply['text'], reply['calls']) == ('incomplete', '', [])


def test_an_error_in_a_200_body_stops_the_game_as_a_provider_error(omlx):
    cut = {'message': 'Model output contains an unrecoverable tool call.', 'type': 'server_error'}
    omlx.answer = lambda: {'error': cut | {'param': None, 'code': 'incomplete_tool_call'}}
    journal = io.StringIO()
    the_broker = broker.Broker('g0001', test_broker.cfg(seats=dict.fromkeys(IDS, QWEN)), journal)
    with pytest.raises(Stop, match='^provider_error$'):
        the_broker(test_broker.opening(model=QWEN))
    assert len(omlx.requests) == 1  # never retried
    [stop] = test_broker.lines(journal.getvalue())
    assert (stop['kind'], stop['reason'], stop['error']) == ('stop', 'provider_error', cut['message'])


def test_a_message_with_two_calls_goes_back_with_the_fix_for_each(omlx):
    """Local Qwen has no parallel_tool_calls switch, so a reply can call act twice (design §2.5)."""
    two = MESSAGE | {'tool_calls': MESSAGE['tool_calls'] + AGAIN['tool_calls']}
    history = PAYLOAD['history'][:1] + [
        {'native': [two], 'call_ids': ['call_01', 'call_02']},
        game.user_turn(['call_01', 'call_02'], game.ONE_CALL, is_error=True),
    ]
    fix = 'ERROR: ' + game.ONE_CALL
    assert Chat.render(PAYLOAD | {'history': history}, 'g0001')['messages'][-3:] == [
        two,
        {'role': 'tool', 'tool_call_id': 'call_01', 'content': fix},
        {'role': 'tool', 'tool_call_id': 'call_02', 'content': fix},
    ]


def test_an_empty_message_is_answered_as_text_only(omlx):
    empty = {'role': 'assistant', 'content': ''}  # native is the message in a list, so never empty
    stop = {'error': {'message': 'Enough.', 'type': 'server_error'}}
    omlx.answer = iter([completion(empty, finish='stop'), stop]).__next__
    cfg = test_broker.cfg(seats=dict.fromkeys(IDS, QWEN))
    session = game.Session(cfg, broker.Broker('g0001', cfg, io.StringIO()), io.StringIO())
    assert session.play() == 'provider_error'  # at the second call
    assert omlx.requests[1]['messages'][-2:] == [empty, {'role': 'user', 'content': game.CALL_ACT}]


def native(reply):
    """test_game's scripted reply as oMLX sends it: one message with its reasoning, text and calls, or for a
    max_tokens stop cut off (length) without its calls, since oMLX sends a cut call as an error."""
    blocks, cut = reply['native'], reply['stop'] == 'max_tokens'
    calls = [b for b in blocks if b['type'] == 'tool_use' and not cut]
    message = {
        'role': 'assistant',
        'content': ''.join(b['text'] for b in blocks if b['type'] == 'text'),
        'reasoning_content': ''.join(b['thinking'] for b in blocks if b['type'] == 'thinking'),
        'tool_calls': [
            {'id': b['id'], 'type': 'function', 'function': {'name': b['name'], 'arguments': json.dumps(b['input'])}}
            for b in calls
        ],
    }
    return completion(message, 'length' if cut else 'tool_calls' if calls else 'stop')


@pytest.mark.parametrize('arm', island.ARMS)
def test_each_message_goes_back_as_it_arrived_and_a_cut_off_reply_sends_the_same_request_again(omlx, arm):
    cfg, journal, reason = play(arm, QWEN, omlx, native)
    assert reason == 'complete'
    cut = appended_only(
        omlx,
        'messages',
        lambda body: [body['choices'][0]['message']],
        lambda body: body['choices'][0]['finish_reason'] == 'length',
    )
    assert cut == (3, 2)  # as on OpenAI
    assert replay.replay(cfg, journal, reason) == journal
