"""The sandbox scripts against the fake engine (tests/fake_engine.py): setup pins what it built and probed, the
worker check plays its game, and the controls weaken one guarantee each. CI's sandbox job runs them for real."""

import json
import tempfile

import pytest
import setup
import verify_sandbox_controls as controls
import verify_worker
from fake_engine import IMAGE

from handshake import sandbox

CHECKS = {
    'no_network_interfaces',
    'root_read_only',
    'non_root',
    'no_capabilities',
    'only_game_data_mounted',
    'host_home_absent',
    'credentials_absent',
}


def value(args, flag):
    return args[args.index(flag) + 1]


def test_setup_builds_probes_and_pins_the_image_by_id(engine, monkeypatch):
    executable = engine()
    (sandbox.RUNTIME / 'image.json').unlink()  # setup writes its own
    monkeypatch.setattr(setup.shutil, 'which', lambda name: str(executable))
    setup.main(['--backend', 'docker'])
    assert json.loads((sandbox.RUNTIME / 'image.json').read_text()) == {
        'backend': 'docker',
        'executable': str(executable.resolve()),
        'image': IMAGE,
        'digest': IMAGE,
        'source_hash': sandbox.source_hash(),
    }
    assert sandbox.pinned()['image'] == IMAGE


def test_setup_refuses_an_image_whose_probe_fails(engine, monkeypatch):
    executable = engine('unsafe')
    (sandbox.RUNTIME / 'image.json').unlink()  # setup writes its own
    monkeypatch.setattr(setup.shutil, 'which', lambda name: str(executable))
    with pytest.raises(RuntimeError, match='Sandbox verification failed'):
        setup.main(['--backend', 'docker'])
    assert not (sandbox.RUNTIME / 'image.json').exists()


def test_the_worker_check_matches_the_game_on_the_host(engine, tmp_path, monkeypatch):
    engine()
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    result = verify_worker.verify()
    assert result['passed'], result['checks']
    assert result['model_calls'] >= 8  # at least one for each of mara's turns


def test_each_control_loosens_exactly_one_guarantee(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox.os, 'getuid', lambda: 1001)
    pin = {'backend': 'docker', 'executable': '/usr/bin/docker', 'image': IMAGE}
    base = sandbox.command(pin, tmp_path, 'handshake-control', probe=True)
    weakened = controls.controls(base, IMAGE, tmp_path)
    assert {check for _, check in weakened.values()} == CHECKS
    for name, (args, _) in weakened.items():
        assert args[-2:] == [IMAGE, '--probe'], name
    assert '--network' not in weakened['network attached'][0] and '--read-only' not in weakened['writable root'][0]
    assert value(weakened['root user'][0], '--user') == '0:0' and '--cap-drop' not in weakened['capabilities kept'][0]
    assert value(weakened['credential in environment'][0], '--env') == 'ANTHROPIC_API_KEY=placeholder'


def test_the_controls_fail_when_the_probe_misses_a_weakening(engine, monkeypatch):
    engine()

    def probe_that_misses_the_network(args):
        checks = dict.fromkeys(CHECKS, True)
        checks['root_read_only'] = '--read-only' in args
        checks['non_root'] = value(args, '--user') != '0:0'
        checks['no_capabilities'] = '--cap-drop' in args
        checks['only_game_data_mounted'] = not any('/mnt/host' in a or 'target=/home' in a for a in args)
        checks['host_home_absent'] = not any('target=/home' in a for a in args)
        checks['credentials_absent'] = '--env' not in args
        return {'verified': all(checks.values()), 'checks': checks}

    monkeypatch.setattr(controls, 'probe', probe_that_misses_the_network)
    report = controls.verify()
    assert not report['passed']
    assert [name for name, r in report['controls'].items() if not r['passed']] == ['network attached']
