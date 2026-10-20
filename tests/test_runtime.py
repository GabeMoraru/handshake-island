"""Runtime and scripts/play.py end to end: games in evaluation mode, each in the real worker over real pipes with
tests/fake_engine.py in place of a container, every model call through the broker to a fake Messages API, and one
spend ledger. No test calls a model."""

import io
import itertools
import json

import httpx2
import play
import pytest
from test_broker import COST, THINK, message
from test_sandbox import game_dir

from handshake import broker, island, policies, providers, replay, runtime
from handshake.evaluation import digest
from handshake.game import Session
from handshake.island import IDS
from handshake.runtime import Runtime

tool_ids = itertools.count(1)


def idle(fields):
    """A reply that thinks, then calls act with every field empty: valid whenever nothing is due."""
    call = {'id': f'toolu_{next(tool_ids)}', 'name': 'act', 'input': {k: [] for k in fields}}
    return {'native': [THINK, {'type': 'tool_use', **call}], 'calls': [call], 'stop': 'tool_use'}


def fake_api(request):
    """The Messages API, answering every request with the idle act of its tool."""
    fields = json.loads(request.content)['tools'][0]['input_schema']['properties']
    return httpx2.Response(200, json=message(*idle(fields)['native']))


@pytest.fixture
def host(tmp_path, engine, monkeypatch):
    """The fake engine pinned, the games, the evaluation records and the ledger in tmp_path, and the fake API behind
    every broker's client."""
    engine()
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')
    monkeypatch.setattr(play, 'GAMES', tmp_path / 'games')
    monkeypatch.setattr(runtime, 'EVALUATIONS', tmp_path / 'runtime' / 'evaluations')
    monkeypatch.setattr(broker, 'LEDGER', tmp_path / 'runtime' / 'spend.jsonl')
    client = providers.Anthropic.client
    monkeypatch.setattr(providers.Anthropic, 'client', lambda transport=None: client(httpx2.MockTransport(fake_api)))


def record(data):
    return json.loads((runtime.EVALUATIONS / f'{data.name}.json').read_text())


def on_host(cfg):
    """The end line of the same game played in process, every model seat taking the idle act."""
    journal = io.StringIO()
    Session(cfg, lambda payload: idle(island.fields(cfg['arm'])), journal).play()
    return json.loads(journal.getvalue().splitlines()[-1])


# ------------------------------------------------------------------------------------------------ the runtime


def test_an_evaluated_game_records_its_outcome_and_its_worker_cannot_restart(tmp_path, host):
    data = game_dir(tmp_path, 'handshake-talk', dict.fromkeys(IDS, 'script:Smart'))
    game = Runtime(data)
    game.begin_evaluation()
    assert game.play() == 'complete'
    journal = replay.lines(data / 'journal.jsonl')
    end = journal[-1]
    assert end['scores'] == policies.play(1, 'handshake-talk', {a: policies.Smart for a in IDS}).result()['scores']
    recorded = record(data)
    assert recorded['reason'] == 'complete' and recorded['worker_started'] and recorded['cleanup']['confirmed']
    assert recorded['evidence'] == {
        'journal_lines': len(journal),
        'final_state_sha256': digest(end),
        'scores': end['scores'],
    }
    with pytest.raises(RuntimeError, match='cannot restart'):
        game.play()
    assert record(data) == recorded  # the first close stands


def test_a_worker_that_fails_its_probe_closes_its_game_as_a_sandbox_error(tmp_path, host, engine):
    engine('unsafe')
    data = game_dir(tmp_path, 'handshake-talk', dict.fromkeys(IDS, play.HAIKU))
    game = Runtime(data)
    game.begin_evaluation()
    assert game.play() == 'sandbox_error'
    [error] = replay.lines(data / 'host.jsonl')
    assert error['kind'] == 'error' and 'The worker refused to start' in error['error']
    recorded = record(data)
    assert (recorded['reason'], recorded['cleanup']['confirmed']) == ('sandbox_error', True)
    assert recorded['evidence'] == {'journal_lines': 0, 'final_state_sha256': None, 'scores': None}


# ------------------------------------------------------------------------------------------------ scripts/play.py


def test_a_cast_seats_one_model_or_one_script_at_seed_mod_6_among_haiku():
    haiku = dict.fromkeys(IDS, play.HAIKU)
    assert play.seats('haiku', 7, 'binding-talk') == haiku
    assert play.seats('sonnet', 7, 'binding-talk') == dict.fromkeys(IDS, play.SONNET)
    assert play.seats('sonnet-seat', 7, 'binding-talk') == haiku | {'rowan': play.SONNET}
    assert play.seats('script-seat', 6, 'binding-silent') == haiku | {'mara': 'script:Smart'}
    assert play.seats('script-seat', 6, 'handshake-silent') == haiku | {'mara': 'script:SmartCautious'}


def test_max_cost_is_more_than_0_and_at_most_the_project_budget(capsys):
    for wrong in ('0', '-1', 'nan', 'inf', '25.01'):
        with pytest.raises(SystemExit):
            play.main(['--seeds', '1', '--max-cost', wrong])
        assert '--max-cost must be more than 0 and at most the project budget' in capsys.readouterr().err


def test_a_run_plays_every_game_in_evaluation_mode_and_records_it(host, capsys):
    arms, casts = ['binding-talk', 'handshake-talk'], ['haiku', 'script-seat']
    argv = ['--seeds', '1', '--arms', *arms, '--casts', *casts, '--parallel', '2', '--max-cost', '1']
    assert play.main(argv) == 0
    recordings = sorted(play.GAMES.glob('*/game-*.json.gz'))
    expected = {f'game-1-{arm}-{cast}.json.gz' for arm in arms for cast in casts}
    assert {path.name for path in recordings} == expected
    calls = 0
    for path in recordings:
        data, recording = path.parent, replay.read(path)
        end = recording['journal'][-1]
        assert (recording['game'], recording['reason'], recording['host']) == (data.name, 'complete', [])
        assert recording['config'] == json.loads((data / 'config.json').read_text())
        assert recording['journal'] == replay.lines(data / 'journal.jsonl')
        assert end == on_host(recording['config'])
        replay.verify(recording)
        recorded = record(data)
        assert recorded['reason'] == 'complete' and recorded['worker_started'] and recorded['cleanup']['confirmed']
        assert recorded['evidence'] == {
            'journal_lines': len(recording['journal']),
            'final_state_sha256': digest(end),
            'scores': end['scores'],
        }
        calls += sum(line['kind'] == 'call' for line in recording['journal'])
    ledger = replay.lines(broker.LEDGER)
    assert len(ledger) == calls >= island.ROUNDS * (2 * len(IDS) + 2 * (len(IDS) - 1))  # a call a model seat's turn
    assert {(line['model'], line['cost_usd']) for line in ledger} == {(play.HAIKU, COST)}
    assert f'This run charged ${calls * COST:.4f} of its $1.00.' in capsys.readouterr().out


def test_a_run_stops_short_of_its_max_cost_and_the_game_it_cuts_closes_as_budget(host, capsys):
    # A Haiku call here may cost up to about $0.009, so a few calls fit under $0.015.
    argv = ['--seeds', '1', '2', '--arms', 'binding-talk', '--max-cost', '0.015']
    assert play.main(argv) == 1
    [path] = play.GAMES.glob('*/game-*.json.gz')
    data, recording = path.parent, replay.read(path)
    assert (path.name, recording['reason']) == ('game-1-binding-talk-haiku.json.gz', 'budget')
    calls = sum(line['kind'] == 'call' for line in recording['journal'])
    assert calls == len(replay.lines(broker.LEDGER)) and 0 < calls * COST <= 0.015
    [stop] = recording['host']
    assert (stop['kind'], stop['reason']) == ('stop', 'budget')
    assert record(data)['reason'] == 'budget'
    assert record(data)['evidence']['scores'] == recording['journal'][-1]['scores']
    replay.verify(recording)
    assert 'seed 2 binding-talk haiku: not started' in capsys.readouterr().out


def test_games_played_at_once_never_take_the_run_past_its_max_cost(host):
    argv = ['--seeds', '1', '2', '3', '4', '--arms', 'binding-talk', '--parallel', '4', '--max-cost', '0.02']
    assert play.main(argv) == 1
    reasons = [replay.read(path)['reason'] for path in play.GAMES.glob('*/game-*')]
    assert set(reasons) == {'budget'}  # a game cut before it starts has no recording
    assert sum(line['cost_usd'] for line in replay.lines(broker.LEDGER)) <= 0.02


def test_a_game_that_fails_keeps_its_recording_and_the_run_goes_on(host, monkeypatch, capsys):
    def broken(payload, game_id):  # before any call is billed
        raise KeyError('system')

    monkeypatch.setattr(providers.Anthropic, 'render', broken)
    assert play.main(['--seeds', '1', '2', '--arms', 'binding-talk', '--max-cost', '1']) == 1
    recordings = [replay.read(path) for path in play.GAMES.glob('*/game-*')]
    assert [recording['reason'] for recording in recordings] == ['error', 'error']
    for recording in recordings:
        assert recording['journal'][-1]['kind'] == 'game'  # the worker stopped at its first call
        replay.verify(recording)
    out = capsys.readouterr().out
    assert "seed 1 binding-talk haiku failed: KeyError('system')" in out and 'seed 2 binding-talk haiku: error' in out
