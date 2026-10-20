"""Prove the isolation probe catches a weakened sandbox. Each control loosens one guarantee of the pinned image's
command and expects the probe to fail that check; the unchanged command must pass. Only the probe runs, never a
game.
"""

import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from handshake import sandbox  # noqa: E402


def without(args, flag, count=1):
    """args without flag and the count of values that follow it."""
    at = args.index(flag)
    return args[:at] + args[at + 1 + count :]


def replacing(args, flag, value):
    at = args.index(flag)
    return [*args[: at + 1], value, *args[at + 2 :]]


def adding(args, image, *extra):
    at = args.index(image)
    return [*args[:at], *extra, *args[at:]]


def controls(args, image, scratch):
    """Each weakened command and the check that must catch it."""
    home = scratch / 'home'
    (home / 'someone').mkdir(parents=True)
    extra = scratch / 'extra'
    extra.mkdir()
    as_root = replacing(args, '--user', '0:0')
    return {
        'network attached': (without(args, '--network'), 'no_network_interfaces'),
        'writable root': (without(args, '--read-only', 0), 'root_read_only'),
        'root user': (as_root, 'non_root'),
        'capabilities kept': (without(as_root, '--cap-drop'), 'no_capabilities'),
        'extra host directory': (
            adding(args, image, '--mount', f'type=bind,source={extra},target=/mnt/host,readonly'),
            'only_game_data_mounted',
        ),
        'host home directory': (
            adding(args, image, '--mount', f'type=bind,source={home},target=/home,readonly'),
            'host_home_absent',
        ),
        'credential in environment': (
            adding(args, image, '--env', 'ANTHROPIC_API_KEY=placeholder'),
            'credentials_absent',
        ),
    }


def probe(args):
    args = replacing(args, '--name', 'handshake-control-' + uuid.uuid4().hex[:12])
    run = subprocess.run(args, capture_output=True, text=True, timeout=180)
    try:
        return json.loads(run.stdout)
    except json.JSONDecodeError:
        return {'error': (run.stderr or run.stdout)[-2000:], 'exit': run.returncode}


def verify():
    pin = sandbox.pinned()
    with tempfile.TemporaryDirectory(prefix='handshake-controls-') as scratch:
        scratch = Path(scratch)
        data = scratch / 'data'
        data.mkdir()
        data.chmod(0o777)  # Apple Container's guest runs as uid 1000, not as the host user
        args = sandbox.command(pin, data, 'handshake-control', probe=True)
        baseline = probe(args)
        passed = baseline.get('verified') is True
        results = {'unchanged': {'check': 'verified', 'passed': passed, 'error': baseline.get('error')}}
        for name, (weakened, check) in controls(args, pin['image'], scratch).items():
            result = probe(weakened)
            caught = result.get('verified') is False and result.get('checks', {}).get(check) is False
            results[name] = {'check': check, 'passed': caught, 'error': result.get('error')}
    return {
        'backend': pin['backend'],
        'image': pin['image'],
        'controls': results,
        'passed': all(r['passed'] for r in results.values()),
    }


def main():
    report = verify()
    print(json.dumps(report, indent=2))
    sys.exit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
