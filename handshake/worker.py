"""The game's side of the sandbox, run inside the container as `python -m handshake.worker`.

It probes its own isolation and refuses to run if any check fails; with --probe it only prints the probe. Then
it plays the game configured in /data/config.json, writing /data/journal.jsonl, and asks the host for every model
call over stdio, one line each:

    worker -> host  {"kind": "ready", "probe"} (or "refused", and it exits),
                    then {"kind": "model_request", "id", "payload"} per call, then {"kind": "end", "reason"}
    host -> worker  {"kind": "model_response", "id", "payload": reply} or {..., "error": closing reason}

Only this module talks to the host. Model output is never executed. It uses only the standard library.
"""

import json
import os
import re
import socket
import sys
import uuid
from pathlib import Path

from handshake import game
from handshake.protocol import MAX_LINE, encode, strict_json_loads

DATA = Path('/data')
# Variable names that hold or point to credentials, and proxy settings, whose URLs may carry a password; Docker's
# client adds those from the host user's ~/.docker/config.json. The guest is given none; the image's own GPG_KEY is
# a public signing-key fingerprint and doesn't match.
CREDENTIALS = re.compile(
    r'API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|PROXY|proxy|^(AWS|AZURE|GOOGLE|OPENAI|ANTHROPIC|OMLX)_|'
    r'^(SSH_AUTH_SOCK|IDENTITY_ENDPOINT|MSI_ENDPOINT)$'
)
# Mount points a guest may have: the read-only root, the game's data, scratch space, the three files Docker and
# Podman generate, and Podman's description of the container, besides the kernel's own trees. Anything else is
# host data.
EXPECTED_MOUNTS = {'/', '/data', '/tmp', '/etc/hostname', '/etc/hosts', '/etc/resolv.conf', '/run/.containerenv'}


def emit(message):
    print(encode(message), flush=True)


def receive():
    line = sys.stdin.readline(MAX_LINE + 1)
    if not line.endswith('\n'):
        raise EOFError('The host closed the channel or sent a line over the limit')
    return strict_json_loads(line)


def model_call(payload):
    """Ask the host for one model call; a closing reason in its answer stops the game."""
    request_id = str(uuid.uuid4())
    emit({'kind': 'model_request', 'id': request_id, 'payload': payload})
    response = receive()
    if response.get('kind') != 'model_response' or response.get('id') != request_id:
        raise ValueError('Unexpected message from the host')
    if 'error' in response:
        raise game.Stop(response['error'])
    return response['payload']


def mount_points():
    return [line.split()[4].replace('\\040', ' ') for line in Path('/proc/self/mountinfo').read_text().splitlines()]


def unexpected_mounts(points):
    return [p for p in points if p not in EXPECTED_MOUNTS and p.split('/')[1] not in ('proc', 'sys', 'dev')]


def connects(address, family):
    try:
        with socket.socket(family, socket.SOCK_STREAM) as conn:
            conn.settimeout(0.3)
            conn.connect(address)
        return True
    except OSError:
        return False


def isolation_probe():
    """Inspect the guest's enforced properties and attempt what it must not be able to do."""
    interfaces = sorted(p.name for p in Path('/sys/class/net').iterdir())
    status = Path('/proc/self/status').read_text()
    capabilities = next(line.split(':', 1)[1].strip() for line in status.splitlines() if line.startswith('CapEff:'))
    points = mount_points()
    checks = {
        'linux_guest': sys.platform == 'linux',
        'non_root': os.getuid() != 0,
        'no_network_interfaces': interfaces == ['lo'],
        'no_capabilities': int(capabilities, 16) == 0,
        # No home tree of any host user: macOS keeps them in /Users, Linux in /home.
        'host_home_absent': not Path('/Users').exists()
        and not (Path('/home').is_dir() and any(Path('/home').iterdir())),
        'credentials_absent': not any(CREDENTIALS.search(name) for name in os.environ),
        'only_game_data_mounted': '/data' in points and not unexpected_mounts(points),
    }
    try:
        Path('/app/escape-probe').write_text('should fail')
        checks['root_read_only'] = False
    except OSError:
        # A non-root user can't write /app on a writable root either, so check the mount itself.
        checks['root_read_only'] = bool(os.statvfs('/').f_flag & os.ST_RDONLY)
    probe = DATA / '.write-probe'
    try:
        probe.write_text('ok')
        probe.unlink()
        checks['game_data_writable'] = True
    except OSError:
        checks['game_data_writable'] = False
    for label, addresses, family in [
        ('internet_ipv4_blocked', [('1.1.1.1', 443)], socket.AF_INET),
        ('internet_ipv6_blocked', [('2606:4700:4700::1111', 443)], socket.AF_INET6),
        # The host at each engine's default gateway: Apple Container's, Docker's and Podman's.
        ('host_service_blocked', [('192.168.64.1', 8000), ('172.17.0.1', 8000), ('10.88.0.1', 8000)], socket.AF_INET),
    ]:
        checks[label] = not any(connects(address, family) for address in addresses)
    return {
        'verified': all(checks.values()),
        'checks': checks,
        'interfaces': interfaces,
        'uid': os.getuid(),
        'capabilities': capabilities,
        'unexpected_mounts': unexpected_mounts(points),
    }


def main():
    probe = isolation_probe()
    if '--probe' in sys.argv:
        emit(probe)
        return
    if not probe['verified']:
        emit({'kind': 'refused', 'probe': probe})
        sys.exit(1)
    emit({'kind': 'ready', 'probe': probe})
    cfg = json.loads((DATA / 'config.json').read_text())
    with (DATA / 'journal.jsonl').open('x') as journal:  # a game is played once; a restart finds its journal
        reason = game.Session(cfg, model_call, journal).play()
    emit({'kind': 'end', 'reason': reason})


if __name__ == '__main__':
    main()
