"""The turn loop against a scripted fake model. No test calls a model."""

import io
import itertools
import json

import pytest

from handshake import config, game, island, policies
from handshake.game import CALL_ACT, ONE_CALL
from handshake.island import IDS, SETTLEMENT

MODEL = 'claude-haiku-5-5'
THINK = {'type': 'thinking', 'thinking': '', 'signature': 'EpYBCkgIBxABGAIiQ'}  # an empty thinking still goes back
tool_ids = itertools.count(1)


def json_values(value):
    return json.loads(json.dumps(value))


def reply(stop, *blocks):
    """A reply as the broker returns it: the content as it arrived, with its calls and text parsed out, its usage, its cost at
    Haiku's list prices, how long it took and how many retries came before it."""
    calls = [{'id': b['id'], 'name': b['name'], 'input': b['input']} for b in blocks if b['type'] == 'tool_use']
    usage = {'input_tokens': 100, 'cache_write_tokens': 0, 'cache_read_tokens': 300, 'output_tokens': 50}
    return {
        'native': list(blocks),
        'calls': calls,
        'text': ''.join(b['text'] for b in blocks if b['type'] == 'text'),
        'stop': stop,
        'usage': usage,
        'cost_usd': 3.8e-05,
        'seconds': 2.0,
        'retries': 0,
    }


def use(act):
    return {'type': 'tool_use', 'id': f'toolu_{next(tool_ids)}', 'name': 'act', 'input': act}


def text(words):
    return {'type': 'text', 'text': words}


class Fake:
    """A scripted model: script maps (resident, round, attempt) to a reply, or to an exception to raise; any other
    call gets the act Smart would take. It keeps each payload and reply as JSON values, as stdio carries them."""

    def __init__(self, script=None):
        self.script, self.payloads, self.replies = script or {}, [], []
        self.smart = {a: policies.Smart(a) for a in IDS}
        self.game = None

    def __call__(self, payload):
        self.payloads.append(json_values(payload))
        key = payload['resident'], payload['round'], payload['attempt']
        answer = self.script.get(key) or reply('tool_use', THINK, use(self.smart[key[0]].act(self.game)))
        if isinstance(answer, Exception):
            raise answer
        self.replies.append(json_values(answer))
        return answer

    def history(self, resident, rnd, attempt):
        return next(
            p['history'] for p in self.payloads if (p['resident'], p['round'], p['attempt']) == (resident, rnd, attempt)
        )


def play(arm='handshake-talk', seed=1, script=None, script_seats=()):
    """Play a game with the fake in every seat but script_seats, which play Smart; return the fake, the reason and the
    journal's lines."""
    seats = {a: 'script:Smart' if a in script_seats else MODEL for a in IDS}
    cfg = {'version': config.CONFIG_VERSION, 'seed': seed, 'arm': arm, 'seats': seats}
    fake, journal = Fake(script), io.StringIO()
    session = game.Session(cfg, fake, journal)
    fake.game = session.game
    reason = session.play()
    return fake, reason, [json.loads(line) for line in journal.getvalue().splitlines()]


def idle(arm):
    return {k: [] for k in island.fields(arm)}


def every_kind(arm):
    """Mara's first four turns, one reply kind per call, each turn defaulting on a different kind; every other call
    is Smart's act."""
    to_herself = {'to': 'mara', 'give': policies.bundle(fish=1), 'get': policies.bundle(rope=1)}

    def invalid():
        return reply('tool_use', THINK, use(idle(arm) | {'offers': [to_herself]}))

    def cut():
        return reply('max_tokens', THINK, use({'answers': []}))

    return {
        ('mara', 1, 1): reply('end_turn', THINK, text('I will wait and see.')),
        ('mara', 1, 2): invalid(),
        ('mara', 1, 3): cut(),
        ('mara', 2, 1): reply('refusal', text('I would rather not.'), use({'answers': []})),
        ('mara', 3, 1): reply('max_tokens', {'type': 'thinking', 'thinking': 'Rope is', 'signature': 'EpYBCkgIC'}),
        ('mara', 3, 2): reply('end_turn', THINK, text('Let me look again.')),
        ('mara', 3, 3): invalid(),
        ('mara', 4, 1): invalid(),
        ('mara', 4, 2): cut(),
        ('mara', 4, 3): reply('end_turn', THINK, text('I will pass.')),
    }


def test_the_first_request_carries_rules_persona_tool_and_the_opening_state():
    fake, _, _ = play()
    first = fake.payloads[0]
    assert (first['resident'], first['round'], first['attempt'], first['model']) == ('mara', 1, 1, MODEL)
    assert first['system'] == [
        island.rules('handshake-talk'),
        'You are Mara (id mara), one of the six residents.\n'
        'Personality: Curious and restless. Experiments, improvises and gets bored by routine. Under pressure, acts '
        'alone rather than wait for agreement. Values freedom.\n'
        'Your private project: smokehouse (wood 4, nails 2, rope 2). It is worth 24 points to you if you hold all of '
        'it at the end. Nobody else knows your project or its value unless you tell them.\n'
        'Your score is all that counts.\n'
        'Answer every turn by calling act exactly once; text outside act is discarded and commits nothing. Before '
        'you call it, think through your project, your holdings and what the others actually did.',
    ]
    assert first['tool'] == island.act_tool('handshake-talk')
    [opening] = first['history']
    assert opening['results'] == []
    assert opening['text'].startswith('The game begins. Since it started: nothing reached you.\n\nSTATE - round 1 of 8')
    assert opening['reminder'] == (
        'Reminder: you are Mara. Curious and restless. Experiments, improvises and gets bored by routine. Under '
        'pressure, acts alone rather than wait for agreement. Values freedom. Your project is smokehouse, worth 24 to '
        'you, and your score is all that counts; take this turn by calling act exactly once.'
    )


@pytest.mark.parametrize('talk', ['talk', 'silent'])
def test_a_plain_arm_drops_only_that_the_score_is_all_that_counts(talk):
    usual, plain = (play(arm)[0].payloads[0] for arm in (f'handshake-{talk}', f'handshake-plain-{talk}'))
    assert plain['system'] == [usual['system'][0], usual['system'][1].replace('Your score is all that counts.\n', '')]
    assert plain['history'][0]['reminder'] == usual['history'][0]['reminder'].replace(
        ', and your score is all that counts', ''
    )
    assert plain['tool'] == usual['tool'] and plain['history'][0]['text'] == usual['history'][0]['text']
    assert 'all that counts' not in json.dumps(plain)


def test_the_receipt_events_and_state_answer_the_last_act_inside_its_tool_result():
    fake, _, _ = play()
    history = fake.history('mara', 2, 1)
    [result] = history[-1]['results']
    assert (result['call_id'], result['is_error']) == (history[-2]['call_ids'][0], False)
    assert result['content'].startswith('Act applied (round 1):\n- ')
    assert '\n\nSince your last turn:\n- ' in result['content']
    assert '\n\nSTATE - round 2 of 8' in result['content']
    assert history[-1]['text'] is None
    assert history[-1]['reminder'].startswith('Reminder: you are Mara.')


def test_every_reply_kind():
    kinds = every_kind('handshake-talk')
    fake, reason, journal = play(script=kinds)
    assert reason == 'complete'
    fix = "offers: to must be another resident (rowan, marcus, theo, nia, felix), not 'mara'."

    def opening(rnd):
        return fake.history('mara', rnd, 1)[-1]

    def call_id(rnd, attempt):
        return kinds['mara', rnd, attempt]['calls'][0]['id']

    # Text only: the next attempt asks for act.
    assert fake.history('mara', 1, 2)[-1] == {'results': [], 'text': CALL_ACT, 'reminder': None}
    # An invalid act: the next attempt answers it with is_error and the kernel's exact fix.
    assert fake.history('mara', 1, 3)[-1] == {
        'results': [{'call_id': call_id(1, 2), 'content': fix, 'is_error': True}],
        'text': None,
        'reminder': None,
    }
    # A cut act on the third attempt: the default; its receipt answers the cut call when the next turn opens.
    [result] = opening(2)['results']
    assert (result['call_id'], result['is_error']) == (call_id(1, 3), True)
    assert result['content'].startswith(
        'Your turn in round 1 failed 3 times (last: Your reply stopped (max_tokens) before a complete act. Call act '
        'again.). The default act was applied: nothing to answer or deliver.\n\nSince your last turn:'
    )
    # A refusal: the default at once, never retried; its receipt answers the refusal's call when the next turn opens.
    assert not any((p['resident'], p['round'], p['attempt']) == ('mara', 2, 2) for p in fake.payloads)
    [result] = opening(3)['results']
    assert (result['call_id'], result['is_error']) == (call_id(2, 1), True)
    assert result['content'].startswith('Your turn in round 2 ended in a refusal. The default act was applied: ')
    # A max_tokens stop with no call: the next attempt asks for act.
    assert fake.history('mara', 3, 2)[-1] == {'results': [], 'text': CALL_ACT, 'reminder': None}
    # An invalid act on the third attempt: the default's receipt answers it with is_error, naming the kernel's fix.
    [result] = opening(4)['results']
    assert (result['call_id'], result['is_error']) == (call_id(3, 3), True)
    assert result['content'].startswith(
        'Your turn in round 3 failed 3 times (last: answers: missing O18. Answer every offer due (O18) with accept '
        f'true or false.\n{fix}). The default act was applied: declined O18.\n\n'
    )
    # Text only on the third attempt: with no call to answer, the default's receipt opens the next turn as text.
    assert opening(5)['results'] == []
    assert opening(5)['text'].startswith('Your turn in round 4 failed 3 times (last: text_only). The default act ')
    assert opening(6)['results'][0]['content'].startswith('Act applied (round 5):\n- ')

    turns = [line for line in journal if line['kind'] == 'turn' and line['resident'] == 'mara']
    assert [t['act'] is None for t in turns[:5]] == [True, True, True, True, False]
    calls = [line for line in journal if line['kind'] == 'call']
    assert [c['reply'] for c in calls] == fake.replies


@pytest.mark.parametrize('arm', island.ARMS)
def test_each_request_is_the_last_one_plus_its_reply_plus_one_new_user_turn(arm):
    fake, _, _ = play(arm, script=every_kind(arm))
    for a in IDS:
        mine = [(p, r) for p, r in zip(fake.payloads, fake.replies, strict=True) if p['resident'] == a]
        for (before, answer), (after, _) in itertools.pairwise(mine):
            appended = {'native': answer['native'], 'call_ids': [c['id'] for c in answer['calls']]}
            assert after['history'][:-1] == before['history'] + [appended]
            assert set(after['history'][-1]) == {'results', 'text', 'reminder'}
            assert (after['system'], after['tool']) == (before['system'], before['tool'])


def test_an_incomplete_reply_is_never_appended_so_the_same_request_goes_again():
    cut = reply('incomplete', {'type': 'reasoning', 'id': 'rs_1', 'summary': []})  # cut off before an act
    fake, reason, journal = play(script={('mara', 1, attempt): cut for attempt in (1, 2, 3)})
    assert reason == 'complete'
    first = fake.history('mara', 1, 1)
    assert fake.history('mara', 1, 2) == fake.history('mara', 1, 3) == first
    turns = [c['turn'] for c in journal if c['kind'] == 'call' and c['resident'] == 'mara'][:3]
    assert turns == [first[0], None, None]  # the journal says which attempts sent no new user turn
    # The default's receipt opens the next turn as text, after the user turn no reply answered.
    [*before, opening] = fake.history('mara', 2, 1)
    assert before == first and opening['results'] == []
    assert opening['text'].startswith(
        'Your turn in round 1 failed 3 times (last: Your reply was cut off before a complete act.). The default act '
        'was applied: '
    )


def test_a_reply_with_several_calls_applies_none_and_each_call_gets_the_error():
    calls = reply('tool_calls', THINK, use(idle('handshake-talk')), use(idle('handshake-talk')))
    fake, reason, journal = play(script={('mara', 1, 1): calls})
    assert reason == 'complete'
    # Had the kernel applied a call, the turn would have ended at the first attempt.
    assert fake.history('mara', 1, 2)[-1] == {
        'results': [{'call_id': c['id'], 'content': ONE_CALL, 'is_error': True} for c in calls['calls']],
        'text': None,
        'reminder': None,
    }
    [turn] = [line for line in journal if line['kind'] == 'turn' and (line['resident'], line['round']) == ('mara', 1)]
    assert turn['act'] == fake.replies[1]['calls'][0]['input']  # the second attempt's act


@pytest.mark.parametrize(
    ('answer', 'reason'),
    [(reply('end_turn'), 'empty_reply'), (reply('model_context_window_exceeded', THINK), 'context_exceeded')],
)
def test_a_reply_that_cannot_be_answered_stops_the_game_and_keeps_its_trace(answer, reason):
    _, stopped, journal = play(script={('rowan', 1, 1): answer})
    call, end = journal[-2:]
    assert stopped == end['reason'] == reason
    assert (call['kind'], call['resident'], call['reply']) == ('call', 'rowan', json_values(answer))


def test_a_provider_error_stops_the_game():
    _, stopped, journal = play(script={('rowan', 1, 1): game.Stop('provider_error')})
    assert stopped == journal[-1]['reason'] == 'provider_error'
    assert [line['resident'] for line in journal if line['kind'] in ('call', 'turn')] == ['mara', 'mara']


def test_model_seats_answering_as_smart_reach_the_all_smart_result():
    """Seed 6 ends with settlement turns for model and script seats alike."""
    fake, reason, journal = play('handshake-silent', seed=6, script_seats=('rowan', 'theo', 'felix'))
    assert reason == 'complete'
    assert {p['resident'] for p in fake.payloads} == {'mara', 'marcus', 'nia'}
    assert {p['resident'] for p in fake.payloads if p['round'] == SETTLEMENT} == {'mara', 'marcus', 'nia'}
    all_smart = policies.play(6, 'handshake-silent', {a: policies.Smart for a in IDS})
    assert journal[-1]['hold'] == all_smart.hold
    assert journal[-1]['broken'] == all_smart.result()['broken']

    replayed = island.Game(6, 'handshake-silent')  # the journal's turns alone replay the game
    for line in journal:
        if line['kind'] == 'turn' and line['act'] is None:
            replayed.default_act()
        elif line['kind'] == 'turn':
            replayed.act(line['act'])
    assert replayed.result() == {k: journal[-1][k] for k in ('scores', 'complete', 'broken', 'optimum')}


def test_a_script_seat_must_name_a_script():
    seats = dict.fromkeys(IDS, 'script:bundle')  # policies.bundle exists but is not a script
    cfg = {'version': config.CONFIG_VERSION, 'seed': 1, 'arm': 'binding-talk', 'seats': seats}
    with pytest.raises(ValueError, match='script:bundle names no script'):
        game.Session(cfg, Fake(), io.StringIO())
