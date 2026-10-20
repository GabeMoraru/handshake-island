"""Evaluation mode: only a fresh game enters, every call rechecks the config and the deadline, and a restart closes
the game. Whole evaluated games are in test_runtime.py. No test calls a model."""

import hashlib
import json
import threading
import time

import pytest
from test_sandbox import game_dir

from handshake import runtime
from handshake.game import Stop
from handshake.runtime import Runtime


@pytest.fixture(autouse=True)
def evaluations(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'EVALUATIONS', tmp_path / 'evaluations')


def record():
    return json.loads((runtime.EVALUATIONS / 'game.json').read_text())


def echo(payload):
    return {'reply to': payload}


def unreachable(payload):
    raise AssertionError('no call may be made')


def test_a_deadline_is_at_most_1800_seconds(tmp_path):
    with pytest.raises(ValueError, match='at most 1800 seconds'):
        Runtime(game_dir(tmp_path)).begin_evaluation(seconds=1801)


def test_only_a_fresh_game_enters_and_its_record_pins_its_config(tmp_path):
    data = game_dir(tmp_path)
    game = Runtime(data)
    (data / 'journal.jsonl').write_text('')  # played outside evaluation mode
    with pytest.raises(RuntimeError, match='Only a fresh game enters evaluation, and game has started'):
        game.begin_evaluation()
    (data / 'journal.jsonl').unlink()
    game.begin_evaluation()
    assert record() == {
        'game': 'game',
        'config': json.loads((data / 'config.json').read_text()),
        'config_sha256': hashlib.sha256((data / 'config.json').read_bytes()).hexdigest(),
        'deadline_seconds': 1800,
        'worker_started': False,
        'reason': None,
        'active_seconds': None,
        'cleanup': None,
        'evidence': None,
    }
    with pytest.raises(RuntimeError, match='Only a fresh game'):
        Runtime(data).begin_evaluation()


def test_a_call_is_made_while_the_config_and_the_deadline_hold(tmp_path):
    evaluation = Runtime(game_dir(tmp_path)).begin_evaluation()
    assert evaluation.admit(echo, {'round': 1}) == {'reply to': {'round': 1}}
    assert record()['reason'] is None


def test_a_changed_config_closes_the_game_before_its_next_call(tmp_path):
    data = game_dir(tmp_path)
    evaluation = Runtime(data).begin_evaluation()
    changed = json.loads((data / 'config.json').read_text()) | {'seed': 2}
    (data / 'config.json').write_text(json.dumps(changed))
    with pytest.raises(Stop, match='^configuration_changed$'):
        evaluation.admit(unreachable, {})
    assert record()['reason'] == 'configuration_changed'


def test_the_deadline_closes_the_game_before_a_call(tmp_path):
    evaluation = Runtime(game_dir(tmp_path)).begin_evaluation(seconds=0.05)
    time.sleep(0.1)
    with pytest.raises(Stop, match='^deadline$'):
        evaluation.admit(unreachable, {})
    assert record()['reason'] == 'deadline' and record()['active_seconds'] >= 0.05


def test_a_call_still_running_at_the_deadline_closes_the_game_and_is_not_waited_for(tmp_path):
    evaluation = Runtime(game_dir(tmp_path)).begin_evaluation(seconds=0.2)
    release = threading.Event()

    def slow(payload):
        release.wait(10)
        return echo(payload)

    started = time.monotonic()
    with pytest.raises(Stop, match='^deadline$'):
        evaluation.admit(slow, {})
    release.set()
    assert time.monotonic() - started < 2
    with pytest.raises(Stop, match='^deadline$'):  # the deadline is never extended
        evaluation.admit(unreachable, {})
    assert record()['reason'] == 'deadline'


def test_a_call_back_after_the_host_slept_through_the_deadline_closes_the_game(tmp_path, monkeypatch):
    evaluation = Runtime(game_dir(tmp_path)).begin_evaluation(seconds=60)

    def asleep(payload):  # the wall clock moves on while the host sleeps; the wait's own clock may not
        monkeypatch.setattr(time, 'time', lambda: evaluation.started + 61)
        return echo(payload)

    with pytest.raises(Stop, match='^deadline$'):
        evaluation.admit(asleep, {})
    assert (record()['reason'], record()['active_seconds']) == ('deadline', 61)


def test_a_stop_from_the_call_closes_the_game_with_its_reason(tmp_path):
    evaluation = Runtime(game_dir(tmp_path)).begin_evaluation()

    def over_budget(payload):
        raise Stop('budget')

    with pytest.raises(Stop, match='^budget$'):
        evaluation.admit(over_budget, {})
    with pytest.raises(Stop, match='^budget$'):
        evaluation.admit(unreachable, {})
    assert record()['reason'] == 'budget'


def test_a_restart_closes_the_game_as_interrupted(tmp_path):
    evaluation = Runtime(game_dir(tmp_path)).begin_evaluation()
    evaluation.claim_worker()
    with pytest.raises(RuntimeError, match='The worker of evaluated game game cannot restart'):
        evaluation.claim_worker()
    assert record()['worker_started'] and record()['reason'] == 'interrupted'
