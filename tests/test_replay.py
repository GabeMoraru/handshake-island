"""Replay of the committed recordings (tests/recordings.py) and of tampered copies of them. No test calls a model."""

import gzip
import json

import pytest
from recordings import FAKE, SMART, TALK

from handshake import config, replay
from handshake.island import SETTLEMENT
from handshake.replay import ReplayError


def first(recording, kind, **match):
    """The first journal line of a kind whose fields include match."""
    return next(line for line in recording['journal'] if line['kind'] == kind and match.items() <= line.items())


def test_the_committed_recordings_replay():
    for path in (SMART, FAKE, TALK):
        replay.verify(replay.read(path))
    smart, fake = replay.read(SMART), replay.read(FAKE)
    assert not any(line['kind'] == 'call' for line in smart['journal'])  # every seat a script
    turns = [line for line in fake['journal'] if line['kind'] == 'turn']
    assert any(turn['act'] is None for turn in turns)  # a default, after a refusal or three failed attempts
    assert any(turn['round'] == SETTLEMENT and turn['resident'] == 'rowan' for turn in turns)  # a model settles


def raise_a_score(recording):
    first(recording, 'end')['scores']['mara'] += 1


def change_a_model_act(recording):
    reply = first(recording, 'call', resident='rowan')['reply']
    reply['calls'][0]['input']['offers'].pop()
    reply['native'][-1]['input']['offers'].pop()


def change_a_model_act_in_its_native_content(recording):
    first(recording, 'call', resident='rowan')['reply']['native'][-1]['input']['offers'].pop()


def change_a_script_move(recording):
    first(recording, 'turn', resident='marcus')['act']['offers'].pop()


def drop_an_event(recording):
    recording['journal'].remove(first(recording, 'event', event='offer'))


def add_a_line_after_the_end(recording):
    recording['journal'].append(first(recording, 'event'))


def change_the_closing_reason(recording):
    recording['reason'] = 'budget'


def relabel_a_stopped_game_complete(recording):
    recording['journal'] = replay.replay(recording['config'], recording['journal'][:40], 'budget')
    recording['journal'][-1]['reason'] = recording['reason'] = 'complete'


def cut_the_end_line(recording):
    recording['journal'].pop()


def change_the_version(recording):
    recording['config']['version'] = config.CONFIG_VERSION + 1


@pytest.mark.parametrize(
    ('tamper', 'error'),
    [
        (raise_a_score, r'Journal line \d+ \(end\) differs from the replay in scores'),
        (change_a_model_act, r'Journal line 8 \(turn\) differs from the replay in act, receipt'),
        (change_a_model_act_in_its_native_content, 'The calls of the reply to rowan in round 1, attempt 1 are not'),
        (change_a_script_move, r'Journal line 11 \(turn\) differs from the replay in act$'),
        (drop_an_event, r'Journal line 9 \(event\) differs from the replay in offer'),
        (add_a_line_after_the_end, r'Journal line \d+ \(event\) comes after the replay ended'),
        (change_the_closing_reason, 'The recording closes as budget, but its journal as complete'),
        (relabel_a_stopped_game_complete, r'Journal line \d+ \(end\) differs from the replay in reason'),
        (cut_the_end_line, 'The recording closes as complete, but its journal has no end line'),
        (change_the_version, f'Only configuration version {config.CONFIG_VERSION} is supported'),
    ],
)
def test_a_tampered_copy_is_refused(tamper, error):
    recording = replay.read(FAKE)
    tamper(recording)
    with pytest.raises(ReplayError, match=error):
        replay.verify(recording)


def test_a_journal_its_worker_never_finished_is_recorded_and_replays_as_far_as_it_goes(tmp_path):
    played = replay.read(FAKE)
    data = tmp_path / 'fake'
    data.mkdir()
    whole = ''.join(json.dumps(line) + '\n' for line in played['journal'][:40])
    (data / 'journal.jsonl').write_text(whole + '{"kind": "ca')  # the worker was killed mid-line
    replay.write(data, played['config'], 'sandbox_error', tmp_path / 'cut.json.gz')
    recording = replay.read(tmp_path / 'cut.json.gz')
    assert recording['journal'] == played['journal'][:40]
    replay.verify(recording)


def test_a_journal_that_is_a_link_is_refused(tmp_path):
    (tmp_path / 'host-file.jsonl').write_text('{"kind": "game"}\n')
    data = tmp_path / 'game'
    data.mkdir()
    (data / 'journal.jsonl').symlink_to(tmp_path / 'host-file.jsonl')
    with pytest.raises(ValueError, match='journal.jsonl is not a plain file'):
        replay.write(data, replay.read(SMART)['config'], 'complete', tmp_path / 'game.json.gz')


def test_the_command_prints_the_verdict(capsys):
    replay.main([str(SMART)])
    assert capsys.readouterr().out == f'Verified {SMART}: seed 1, handshake-talk, closed as complete after 49 turns.\n'


def test_the_command_refuses_a_tampered_recording_or_another_file(tmp_path):
    recording = replay.read(SMART)
    raise_a_score(recording)
    tampered = tmp_path / SMART.name
    tampered.write_bytes(gzip.compress(json.dumps(recording).encode()))
    with pytest.raises(SystemExit, match=rf'Refused {tampered}: Journal line \d+ \(end\) differs from the replay'):
        replay.main([str(tampered)])
    with pytest.raises(SystemExit, match='is not a recording'):
        replay.main([str(tmp_path / 'missing.json.gz')])
    (tmp_path / 'list.json.gz').write_bytes(gzip.compress(b'[]'))
    with pytest.raises(SystemExit, match='is not a recording: it holds no JSON object'):
        replay.main([str(tmp_path / 'list.json.gz')])
