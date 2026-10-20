"""The sandbox: the engine command, the image pin, the relay's caps, and whole games over real pipes, with
tests/fake_engine.py in place of Docker and a fake broker. CI's sandbox job runs the real engines."""

import io
import itertools
import json
import time
from types import SimpleNamespace

import pytest
from fake_engine import IMAGE

from handshake import config, game, island, policies, sandbox
from handshake.island import IDS, SETTLEMENT
from handshake.sandbox import Sandbox, SandboxError

MODEL = 'claude-haiku-5-5'
THINK = {'type': 'thinking', 'thinking': 'Rope is scarce, so I wait.', 'signature': 'EpYBCkgIBxABGAIiQ'}
IDLE = {k: [] for k in island.fields('binding-talk')}  # valid on every turn of a game in which nobody offers
SCRIPTS = {
    'mara': 'Smart',
    'rowan': 'SmartCautious',
    'marcus': 'SmartReneger',
    'theo': 'Gossip',
    'nia': 'CautiousRevealer',
    'felix': 'Lowball',
}


def value(args, flag):
    return args[args.index(flag) + 1]


def game_dir(tmp_path, arm='binding-talk', seats=None, version=config.CONFIG_VERSION):
    data = tmp_path / 'game'
    data.mkdir()
    seats = seats or dict.fromkeys(IDS, MODEL)
    (data / 'config.json').write_text(json.dumps({'version': version, 'seed': 1, 'arm': arm, 'seats': seats}))
    return data


def journal(data):
    return [json.loads(line) for line in (data / 'journal.jsonl').read_text().splitlines()]


class Broker:
    """A fake broker: every reply is the idle act, after a thinking block and some non-ASCII text."""

    def __init__(self):
        self.requests, self.replies = [], []

    def __call__(self, payload):
        self.requests.append(payload)
        call = {'id': f'toolu_{len(self.requests)}', 'name': 'act', 'input': IDLE}
        native = [THINK, {'type': 'text', 'text': 'Holz für Seil — später.'}, {'type': 'tool_use', **call}]
        self.replies.append({'native': native, 'calls': [call], 'stop': 'tool_use'})
        return self.replies[-1]


def unreachable(payload):
    raise AssertionError('no seat may call a model')


def request(resident='mara', rnd=1):
    return {'resident': resident, 'round': rnd, 'attempt': 1, 'model': MODEL, 'history': []}


def sent(*lines):
    """A worker process that has sent these lines and reads nothing."""
    return SimpleNamespace(stdout=io.BytesIO(b''.join(line + b'\n' for line in lines)), stdin=io.BytesIO())


# ------------------------------------------------------------------------------------------------ the command and the pin


FLAGS = {
    '--network': 'none',
    '--cap-drop': 'ALL',
    '--security-opt': 'no-new-privileges',
    '--user': '1001:118',
    '--pull': 'never',
    '--log-driver': 'none',
    '--pids-limit': '256',
    '--memory': '2g',
    '--cpus': '2',
}


@pytest.mark.parametrize('backend', ['docker', 'podman'])
def test_docker_and_podman_run_the_worker_with_the_same_guarantees(tmp_path, monkeypatch, backend):
    monkeypatch.setattr(sandbox.os, 'getuid', lambda: 1001)
    monkeypatch.setattr(sandbox.os, 'getgid', lambda: 118)
    pin = {'backend': backend, 'executable': f'/usr/bin/{backend}', 'image': IMAGE}
    args = sandbox.command(pin, tmp_path, 'handshake-1')
    assert args[:2] == [f'/usr/bin/{backend}', 'run'] and args[-1] == IMAGE
    assert {flag: value(args, flag) for flag in FLAGS} == FLAGS
    assert '--read-only' in args
    assert set(value(args, '--tmpfs').split(':')[1].split(',')) >= {'noexec', 'nosuid', 'nodev'}
    assert value(args, '--mount') == f'type=bind,source={tmp_path.resolve()},target=/data'
    assert sum('source=' in arg for arg in args) == 1 and '--env' not in args
    if backend == 'podman':
        at = args.index('--userns')
        assert args[at : at + 4] == ['--userns', 'keep-id', '--read-only-tmpfs=false', '--passwd=false']
    else:
        assert '--userns' not in args


def test_apple_container_runs_the_worker_as_its_own_user_with_no_dns(tmp_path):
    pin = {'backend': 'apple', 'executable': '/usr/local/bin/container', 'image': 'handshake-island-worker:0.1'}
    args = sandbox.command(pin, tmp_path, 'handshake-1', probe=True)
    assert args[-2:] == ['handshake-island-worker:0.1', '--probe']
    assert value(args, '--network') == 'none' and '--no-dns' in args and '--read-only' in args
    assert value(args, '--user') == '1000:1000' and value(args, '--cap-drop') == 'ALL'


def test_docker_and_podman_refuse_to_start_the_worker_as_root(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox.os, 'getuid', lambda: 0)
    for backend in ('docker', 'podman'):
        with pytest.raises(SandboxError, match='as root'):
            sandbox.command({'backend': backend, 'executable': backend, 'image': IMAGE}, tmp_path, 'handshake-1')


@pytest.mark.parametrize(
    ('changes', 'error'),
    [
        ({'backend': 'lxc'}, 'not prepared'),
        ({'executable': 'docker'}, 'not prepared'),  # not an absolute path
        ({'source_hash': 'stale'}, 'source changed'),
        ({'digest': 'sha256:' + 'b' * 64}, 'image changed'),
    ],
)
def test_a_game_starts_only_on_the_pinned_image_built_from_the_current_source(tmp_path, engine, changes, error):
    engine(**changes)
    with pytest.raises(SandboxError, match=error):
        Sandbox(game_dir(tmp_path), unreachable).play()
    assert not (tmp_path / 'game' / 'journal.jsonl').exists()


# ------------------------------------------------------------------------------------------------ the relay


def test_the_relay_refuses_a_fourth_attempt_at_a_turn(tmp_path):
    broker = Broker()
    box = Sandbox(tmp_path, broker)
    for _ in range(3):
        assert box.answer(request()) == {'payload': broker.replies[-1]}
    box.answer(request('mara', 2))  # another round is another turn
    with pytest.raises(SandboxError, match='Refused attempt 4 at the turn of mara in round 1'):
        box.answer(request())
    assert len(broker.requests) == 4


def test_the_relay_refuses_call_163(tmp_path):
    broker = Broker()
    box = Sandbox(tmp_path, broker)
    for resident, rnd, _ in itertools.product(IDS, range(1, SETTLEMENT + 1), range(3)):
        box.answer(request(resident, rnd))
    assert len(broker.requests) == config.MAX_CALLS == 162
    with pytest.raises(SandboxError, match='Refused call 163: a game makes at most 162'):
        box.answer(request())
    assert len(broker.requests) == 162


@pytest.mark.parametrize(
    'payload',
    [None, [], {}, request('zoe'), request(['mara']), request('mara', 0), request('mara', 10), request('mara', '1')],
)
def test_the_relay_refuses_a_request_that_names_no_turn(tmp_path, payload):
    with pytest.raises(SandboxError, match='names no resident and round'):
        Sandbox(tmp_path, unreachable).answer(payload)


READY = b'{"kind":"ready","probe":{"verified":true}}'


@pytest.mark.parametrize(
    ('lines', 'error'),
    [
        ([b'{"kind":"ready"}'], 'refused to start'),
        ([b'{"kind":"ready","probe":{"verified":false}}'], 'refused to start'),
        ([b'{"kind":"ready","probe":"verified"}'], 'refused to start'),
        ([b'ready'], 'not one ASCII JSON object'),
        ([READY, b'["end"]'], 'not one ASCII JSON object'),
        ([READY, '{"kind":"end","reason":"fertig für heute"}'.encode()], 'not one ASCII JSON object'),
        ([READY, b'{"kind":"end"}'], 'Unexpected message'),
        ([READY, b'{"kind":"end","reason":{"x":1}}'], 'Unexpected message'),
    ],
)
def test_the_relay_refuses_a_line_it_cannot_accept(tmp_path, lines, error):
    box = Sandbox(tmp_path, unreachable)
    box.process = sent(*lines)
    with pytest.raises(SandboxError, match=error):
        box.relay()


# ------------------------------------------------------------------------------------------------ games over stdio


def test_an_all_script_game_in_the_worker_ends_as_the_kernel_plays_it_on_the_host(tmp_path, engine):
    engine()
    data = game_dir(tmp_path, 'handshake-talk', {a: f'script:{s}' for a, s in SCRIPTS.items()})
    box = Sandbox(data, unreachable)
    assert box.play() == 'complete'
    assert box.probe == {'verified': True, 'checks': {'fake_engine': True}}
    end = journal(data)[-1]
    on_host = policies.play(1, 'handshake-talk', {a: getattr(policies, s) for a, s in SCRIPTS.items()})
    assert end['hold'] == on_host.hold
    assert {k: end[k] for k in ('scores', 'complete', 'broken', 'optimum')} == on_host.result()


def test_model_seats_play_over_stdio_and_their_content_comes_back_unchanged(tmp_path, engine):
    engine()
    data = game_dir(tmp_path)
    broker = Broker()
    assert Sandbox(data, broker).play() == 'complete'
    assert len(broker.requests) == 48  # one valid act a turn
    for a in IDS:
        mine = [(p, r) for p, r in zip(broker.requests, broker.replies, strict=True) if p['resident'] == a]
        for (before, reply), (after, _) in itertools.pairwise(mine):
            appended = {'native': reply['native'], 'call_ids': [reply['calls'][0]['id']]}
            assert after['history'][:-1] == before['history'] + [appended]
    assert [line['reply'] for line in journal(data) if line['kind'] == 'call'] == broker.replies


def test_a_closing_reason_from_the_broker_ends_the_game(tmp_path, engine):
    engine()
    data = game_dir(tmp_path)

    def broker(payload):
        raise game.Stop('provider_error')

    assert Sandbox(data, broker).play() == 'provider_error'
    assert journal(data)[-1]['reason'] == 'provider_error'


def test_a_worker_that_fails_its_isolation_probe_never_plays(tmp_path, engine):
    engine('unsafe')
    data = game_dir(tmp_path)
    with pytest.raises(SandboxError, match='The worker refused to start: .*"fake_engine":false'):
        Sandbox(data, unreachable).play()
    assert not (data / 'journal.jsonl').exists()


def test_a_worker_that_stops_leaves_its_traceback_in_the_log(tmp_path, engine):
    engine()
    box = Sandbox(game_dir(tmp_path, version=0), unreachable)
    with pytest.raises(SandboxError, match='The worker stopped'):
        box.play()
    assert f'ValueError: Only configuration version {config.CONFIG_VERSION} is supported' in box.log.read_text()


def test_a_game_is_never_played_twice_in_one_directory(tmp_path, engine):
    engine()
    data = game_dir(tmp_path)
    (data / 'journal.jsonl').write_text('{"kind": "game"}\n')
    box = Sandbox(data, unreachable)
    with pytest.raises(SandboxError, match='The worker stopped'):
        box.play()
    assert 'FileExistsError' in box.log.read_text()
    assert (data / 'journal.jsonl').read_text() == '{"kind": "game"}\n'


def test_a_silent_worker_is_stopped_and_removed_before_play_returns(tmp_path, engine, monkeypatch):
    engine('silent')
    monkeypatch.setattr(sandbox, 'SILENCE', 0.5)
    removed, kill = [], Sandbox.kill

    def slow_kill(box):  # an engine that takes a while to remove the container
        kill(box)
        time.sleep(0.5)
        removed.append(box.name)

    monkeypatch.setattr(Sandbox, 'kill', slow_kill)
    box = Sandbox(game_dir(tmp_path), unreachable)
    started = time.monotonic()
    with pytest.raises(SandboxError, match='silent for 0.5 s'):
        box.play()
    assert removed == [box.name]
    assert time.monotonic() - started < 10


def test_a_worker_that_takes_no_reply_is_stopped(tmp_path, engine, monkeypatch):
    engine('deaf')
    monkeypatch.setattr(sandbox, 'SILENCE', 0.5)
    started = time.monotonic()
    with pytest.raises(SandboxError, match='took no reply for 0.5 s'):
        Sandbox(game_dir(tmp_path), lambda payload: {'text': 'x' * 2**20}).play()  # more than any pipe holds
    assert time.monotonic() - started < 10
