"""The host's side of the sandbox: one game's worker in its own container, its model requests relayed to the broker.

Apple Container runs the worker on macOS, Docker or Podman on Linux; the worker never runs outside a container.
scripts/setup.py builds the image and pins it in runtime/image.json, and a sandbox starts only that image, built
from the current source. The worker's lines are untrusted: the relay checks them, and any line it cannot accept
stops the container.
"""

import collections
import contextlib
import hashlib
import json
import os
import subprocess
import threading
import uuid
from pathlib import Path

from handshake import config
from handshake.game import Stop
from handshake.island import IDS, MAX_ATTEMPTS, SETTLEMENT
from handshake.protocol import MAX_LINE, encode, strict_json_loads

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / 'runtime'  # the image pin, setup's log and the workers' logs; never mounted in a guest
ENGINES = ('apple', 'docker', 'podman')
SILENCE = 120  # seconds the host waits for the worker's next line, or for it to take a reply; never the broker's call
# Only what an engine's client needs to find its daemon; nothing here reaches the guest.
ENGINE_ENV = {'PATH', 'HOME', 'USER', 'TMPDIR', 'LANG', 'XDG_RUNTIME_DIR', 'DOCKER_HOST', 'CONTAINER_HOST'}


class SandboxError(RuntimeError):
    """The worker could not start, stopped, or sent something the relay refuses; its container is stopped."""


def source_hash():
    """The hash of what the image is built from."""
    digest = hashlib.sha256()
    for path in [ROOT / 'Containerfile', *sorted((ROOT / 'handshake').glob('*.py'))]:
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def image_id(backend, inspected):
    """The identity an engine reports for a local image: Apple's manifest digest, or Docker's and Podman's image ID."""
    return inspected[0]['configuration']['descriptor']['digest'] if backend == 'apple' else inspected[0]['Id']


def pinned():
    """The image pin, once its engine, its image and the source are what setup pinned."""
    path = RUNTIME / 'image.json'
    pin = json.loads(path.read_text()) if path.exists() else {}
    executable = Path(pin.get('executable', ''))
    if pin.get('backend') not in ENGINES or not executable.is_absolute() or not executable.exists():
        raise SandboxError('The sandbox is not prepared. Run scripts/setup.py first.')
    if pin['source_hash'] != source_hash():
        raise SandboxError('The source changed since the image was built. Run scripts/setup.py again.')
    inspect = subprocess.run([executable, 'image', 'inspect', pin['image']], capture_output=True, timeout=30)
    if inspect.returncode or image_id(pin['backend'], json.loads(inspect.stdout)) != pin['digest']:
        raise SandboxError('The pinned image changed or is gone. Run scripts/setup.py again.')
    return pin


def command(pin, data_dir, name, probe=False):
    """The engine command that runs the pinned worker image with data_dir as its only mount."""
    data_dir = Path(data_dir).resolve(strict=True)
    args = ['run', '--rm', '--interactive', '--name', name]
    args += ['--network', 'none', '--read-only', '--cap-drop', 'ALL', '--cpus', '2']
    if pin['backend'] == 'apple':
        args += ['--progress', 'none', '--no-dns', '--user', '1000:1000', '--memory', '2G', '--tmpfs', '/tmp:size=64m']
    else:
        # The guest runs as the host user, so it can write the game's directory without opening it to everyone.
        if os.getuid() == 0:
            raise SandboxError('Refusing to start the worker as root; run as an unprivileged user')
        args += ['--user', f'{os.getuid()}:{os.getgid()}', '--memory', '2g', '--pids-limit', '256']
        args += ['--tmpfs', '/tmp:rw,size=64m,noexec,nosuid,nodev']
        # Never pull at run time, keep no copy of the worker's output in the engine's log store, and let no process
        # gain privileges through setuid binaries.
        args += ['--pull', 'never', '--log-driver', 'none', '--security-opt', 'no-new-privileges']
        if pin['backend'] == 'podman':
            # Rootless Podman maps the host user to the same UID inside. By default it would also add writable tmpfs
            # mounts to a read-only root, and bind-mount generated /etc/passwd and /etc/group naming the host user.
            args += ['--userns', 'keep-id', '--read-only-tmpfs=false', '--passwd=false']
    args += ['--mount', f'type=bind,source={data_dir},target=/data', pin['image']]
    return [pin['executable'], *args, *(['--probe'] if probe else [])]


class Sandbox:
    """One game in one container: data_dir holds its config.json and receives its journal.jsonl, and model_call
    (the broker) answers its model requests. model_call raises Stop to end the game with that closing reason."""

    def __init__(self, data_dir, model_call):
        self.data_dir, self.model_call = Path(data_dir), model_call
        self.name = 'handshake-' + uuid.uuid4().hex[:12]
        self.log = RUNTIME / 'logs' / f'{self.name}.log'  # the worker's stderr
        self.probe, self.calls, self.attempts = None, 0, collections.Counter()

    def play(self):
        """Play the game to its end and return its closing reason. The container is stopped whatever happens."""
        self.pin = pinned()
        self.log.parent.mkdir(parents=True, exist_ok=True)
        with self.log.open('ab') as log:
            self.process = subprocess.Popen(
                command(self.pin, self.data_dir, self.name),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log,
                env={k: v for k, v in os.environ.items() if k in ENGINE_ENV},
            )
            try:
                return self.relay()
            finally:
                self.close()

    def relay(self):
        ready = self.read()
        probe = ready.get('probe')
        if ready.get('kind') != 'ready' or not isinstance(probe, dict) or probe.get('verified') is not True:
            raise SandboxError(f'The worker refused to start: {encode(ready)}')
        self.probe = probe
        while (message := self.read()).get('kind') == 'model_request':
            self.send({'kind': 'model_response', 'id': message.get('id'), **self.answer(message.get('payload'))})
        reason = message.get('reason')
        if message.get('kind') != 'end' or not isinstance(reason, str):
            raise SandboxError(f'Unexpected message from the worker: {encode(message)[:200]}')
        return reason

    def answer(self, payload):
        """The broker's reply to one model request, or the reason the game must stop. A request that names no turn,
        or one past either cap, is refused and stops the container: an honest worker never sends one."""
        if (
            not isinstance(payload, dict)
            or payload.get('resident') not in IDS
            or payload.get('round') not in range(1, SETTLEMENT + 1)
        ):
            raise SandboxError('Refused a model request that names no resident and round')
        resident, rnd = payload['resident'], payload['round']
        if self.calls == config.MAX_CALLS:
            raise SandboxError(f'Refused call {self.calls + 1}: a game makes at most {config.MAX_CALLS}')
        if self.attempts[resident, rnd] == MAX_ATTEMPTS:
            raise SandboxError(f'Refused attempt {MAX_ATTEMPTS + 1} at the turn of {resident} in round {rnd}')
        self.calls += 1
        self.attempts[resident, rnd] += 1
        try:
            return {'payload': self.model_call(payload)}
        except Stop as stop:
            return {'error': str(stop)}

    @contextlib.contextmanager
    def watchdog(self):
        """Stop the worker if the block takes SILENCE seconds, and leave only once a stop has removed the container."""
        timer = threading.Timer(SILENCE, self.kill)
        timer.start()
        try:
            yield
        finally:
            timer.cancel()
            timer.join()

    def read(self):
        """The worker's next message. A worker silent for SILENCE seconds is stopped."""
        with self.watchdog():
            line = self.process.stdout.readline(MAX_LINE + 1)
        if not line:
            raise SandboxError(f'The worker stopped, or was silent for {SILENCE} s. Its log: {self.log}')
        if not line.endswith(b'\n'):
            raise SandboxError(f'The worker sent a line over {MAX_LINE} bytes')
        try:
            return strict_json_loads(line.decode('ascii'))
        except ValueError as error:
            raise SandboxError(f'The worker sent a line that is not one ASCII JSON object: {error}') from None

    def send(self, message):
        """Hand the worker a reply. A worker that takes none for SILENCE seconds is stopped."""
        try:
            with self.watchdog():
                self.process.stdin.write(encode(message).encode() + b'\n')
                self.process.stdin.flush()
        except BrokenPipeError:
            raise SandboxError(f'The worker stopped, or took no reply for {SILENCE} s. Its log: {self.log}') from None

    def kill(self):
        """Kill the engine client, then the container: rm --force kills and removes it under every engine."""
        self.process.kill()
        subprocess.run([self.pin['executable'], 'rm', '--force', self.name], capture_output=True, timeout=30)

    def close(self):
        """Close the worker's input, which ends an honest worker, and stop the container if it has not exited."""
        with contextlib.suppress(BrokenPipeError):  # the worker has already gone
            self.process.stdin.close()
        try:
            self.process.wait(10)
        except subprocess.TimeoutExpired:
            self.kill()
            self.process.wait(10)
