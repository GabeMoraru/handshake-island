"""The worker's isolation probe and what it may import. The probe itself runs only in a container: CI's sandbox job
runs it under Docker and Podman, and scripts/verify_sandbox_controls.py requires it to catch each weakened control."""

import ast
import sys
from pathlib import Path

import pytest

from handshake import worker

ROOT = Path(__file__).resolve().parent.parent
# Every handshake module the worker loads; host modules such as handshake.sandbox never run in the guest.
GUEST = {
    'handshake.worker',
    'handshake.protocol',
    'handshake.game',
    'handshake.island',
    'handshake.policies',
    'handshake.config',
}
# Anything that could reach outside the process.
EGRESS = {'socket', 'ssl', 'http', 'urllib', 'ftplib', 'smtplib', 'xmlrpc', 'socketserver', 'asyncio', 'subprocess'}


def imports(module):
    """Every module a handshake module imports, at any depth of its source."""
    tree = ast.parse((ROOT / (module.replace('.', '/') + '.py')).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f'{module} uses a relative import'
            yield node.module
            if node.module == 'handshake':  # from handshake import game
                yield from (f'handshake.{alias.name}' for alias in node.names)


def closure(module):
    seen, todo = set(), [module]
    while todo:
        name = todo.pop()
        if name not in seen:
            seen.add(name)
            if name.startswith('handshake.'):
                todo.extend(imports(name))
    return seen


def test_the_worker_loads_only_the_standard_library_and_the_guest_modules():
    names = closure('handshake.worker')
    assert {n for n in names if n.startswith('handshake.')} == GUEST
    assert all(n.split('.')[0] in sys.stdlib_module_names for n in names if not n.startswith('handshake'))


def test_only_the_worker_imports_a_network_library_to_probe_that_its_egress_is_blocked():
    assert {n.split('.')[0] for n in closure('handshake.worker')} & EGRESS == {'socket'}
    assert [m for m in sorted(GUEST) if 'socket' in set(imports(m))] == ['handshake.worker']


@pytest.mark.parametrize(
    'name',
    [
        'ANTHROPIC_API_KEY',
        'ANTHROPIC_AUTH_TOKEN',
        'OPENAI_API_KEY',
        'AZURE_CLIENT_SECRET',
        'AWS_ACCESS_KEY_ID',
        'GOOGLE_APPLICATION_CREDENTIALS',
        'GITHUB_TOKEN',
        'DB_PASSWORD',
        'SSH_AUTH_SOCK',
        'OMLX_API_KEY',
        'HTTP_PROXY',
        'https_proxy',
    ],
)
def test_credential_variables_are_caught(name):
    assert worker.CREDENTIALS.search(name)


@pytest.mark.parametrize(
    'name', ['GPG_KEY', 'PYTHON_SHA256', 'PYTHON_VERSION', 'PATH', 'HOME', 'LANG', 'HOSTNAME', 'PYTHONUNBUFFERED']
)
def test_the_images_own_variables_are_not(name):
    assert not worker.CREDENTIALS.search(name)


# Mount points observed in the worker under Apple Container, and those Docker adds.
APPLE = ['/', '/proc', '/sys', '/dev', '/tmp', '/data', '/dev/mqueue', '/dev/shm', '/dev/pts', '/sys/fs/cgroup']
DOCKER = APPLE + ['/etc/resolv.conf', '/etc/hostname', '/etc/hosts', '/proc/kcore', '/sys/devices/virtual/powercap']


def test_only_the_games_data_and_the_engines_files_may_be_mounted():
    assert worker.unexpected_mounts(APPLE) == worker.unexpected_mounts(DOCKER) == []
    assert worker.unexpected_mounts(['/run/.containerenv']) == []
    for extra in ('/mnt/host', '/home', '/root/.ssh', '/var/run/docker.sock', '/run/secrets', '/data2', '/etc/passwd'):
        assert worker.unexpected_mounts(APPLE + [extra]) == [extra]
