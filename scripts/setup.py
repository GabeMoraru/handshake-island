"""Build the worker image, prove its isolation, and pin it in runtime/image.json. Run from the project directory.

Apple Container is the default on macOS; Docker or Podman (--backend) elsewhere.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from handshake import sandbox  # noqa: E402

TAG = 'handshake-island-worker:0.1'
APPLE_VERSION = '1.4.1'
APPLE_PACKAGE = f'https://github.com/apple/container/releases/download/{APPLE_VERSION}/container-{APPLE_VERSION}-installer-signed.pkg'
CONTAINER = Path.home() / f'.local/share/apple-container/{APPLE_VERSION}/bin/container'


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=sandbox.ENGINES, default='apple' if sys.platform == 'darwin' else 'docker')
    backend = parser.parse_args(argv).backend
    sandbox.RUNTIME.mkdir(parents=True, exist_ok=True)
    source = sandbox.source_hash()
    executable, version = prepare_apple() if backend == 'apple' else prepare_oci(backend)
    inspected = json.loads(subprocess.check_output([str(executable), 'image', 'inspect', TAG], text=True))
    digest = sandbox.image_id(backend, inspected)
    # Apple's local store resolves the build's tag, and every start checks its digest. Docker and Podman run the
    # image by its ID, so a retagged image is never started.
    pin = {
        'backend': backend,
        'executable': str(executable),
        'image': TAG if backend == 'apple' else digest,
        'digest': digest,
        'source_hash': source,
    }
    with tempfile.TemporaryDirectory(prefix='handshake-probe-') as scratch:
        data = Path(scratch)
        data.chmod(0o777)  # Apple Container's guest runs as uid 1000, not as the host user
        args = sandbox.command(pin, data, 'handshake-probe-' + uuid.uuid4().hex[:12], probe=True)
        probe = json.loads(subprocess.check_output(args, text=True, timeout=120))
    if not probe.get('verified'):
        raise RuntimeError('Sandbox verification failed: ' + json.dumps(probe))
    if sandbox.source_hash() != source:
        raise RuntimeError('The source changed during setup. Run it again once editing is done.')
    path = sandbox.RUNTIME / 'image.json'
    path.with_suffix('.tmp').write_text(json.dumps(pin, indent=2))
    path.with_suffix('.tmp').replace(path)
    print(f'Sandbox verified with {backend} {version}: non-root, read-only root, game data only, no network.')


def prepare_oci(backend):
    """Build with Docker or Podman, found on PATH and pinned by absolute path from here on."""
    found = shutil.which(backend)
    if not found:
        raise RuntimeError(f'{backend} is not installed or not on PATH')
    executable = Path(found).resolve()
    # Docker's daemon runs the container; Podman has no daemon, so its client is the engine.
    field = '{{.Server.Version}}' if backend == 'docker' else '{{.Client.Version}}'
    version = subprocess.check_output([str(executable), 'version', '--format', field], text=True).strip()
    print(f'Building the worker image with {backend} {version}. Progress: runtime/setup.log', flush=True)
    with (sandbox.RUNTIME / 'setup.log').open('a') as log:
        run([executable, 'build', '--file', 'Containerfile', '--tag', TAG, '.'], cwd=ROOT, stdout=log, stderr=log)
    return executable, version


def prepare_apple():
    """Install Apple Container if it is missing, start it, and build with it."""
    if not CONTAINER.exists():
        with tempfile.TemporaryDirectory(prefix='handshake-container-') as staging:
            package, expanded = Path(staging) / 'container.pkg', Path(staging) / 'expanded'
            urllib.request.urlretrieve(APPLE_PACKAGE, package)
            signature = subprocess.check_output(['pkgutil', '--check-signature', str(package)], text=True)
            if (
                'Apple Inc. - Containerization' not in signature
                or 'trusted by the Apple notary service' not in signature
            ):
                raise RuntimeError('Apple Container package signature could not be verified')
            run(['pkgutil', '--expand-full', package, expanded])
            shutil.copytree(expanded / 'Payload', CONTAINER.parent.parent, symlinks=True)
    settings = Path.home() / '.config/container/config.toml'
    if not settings.exists():
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text('[build]\nrosetta = false\n')
    print(
        f'Starting Apple Container {APPLE_VERSION} and building the worker image. Progress: runtime/setup.log',
        flush=True,
    )
    with (sandbox.RUNTIME / 'setup.log').open('a') as log:
        run([CONTAINER, 'system', 'start'], input='y\n', text=True, stdout=log, stderr=log)
        run(
            [CONTAINER, 'build', '--file', 'Containerfile', '--tag', TAG, '--progress', 'plain', '.'],
            cwd=ROOT,
            stdout=log,
            stderr=log,
        )
    return CONTAINER, APPLE_VERSION


if __name__ == '__main__':
    main()
