"""Evaluation mode, the runner of every measured game (decision 0006).

Runtime.begin_evaluation admits only a fresh game and creates its record in runtime/evaluations/, outside the
directory the guest can write. The record pins the hash of the game's config.json and a wall-clock deadline before
the worker starts. Every model call then passes through admit, which rechecks both: a changed config or a passed
deadline closes the game, and so does a call still running at the deadline, which is never extended. The worker
cannot restart; a restart closes the game as interrupted. Once the worker has exited, finish records the closing
reason, the active and cleanup seconds, and the final evidence the game's journal holds.
"""

import concurrent.futures
import hashlib
import json
import time

from handshake import config
from handshake.game import Stop
from handshake.protocol import strict_json_loads


def digest(value):
    """The sha256 of a JSON value, whatever its key order."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def file_hash(path):
    """The sha256 of a file's bytes, or None if it cannot be read, so a deleted config counts as changed."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def capture_evidence(journal):
    """The final evidence in a game's journal, read once the worker has exited: its length, and the hash and scores
    of its end line, which holds the final holdings and scores (None if the game never got there)."""
    lines = journal.read_text().splitlines() if journal.exists() else []
    try:
        end = strict_json_loads(lines[-1])
    except (IndexError, ValueError):
        end = {}
    ended = end.get('kind') == 'end'
    return {
        'journal_lines': len(lines),
        'final_state_sha256': digest(end) if ended else None,
        'scores': end.get('scores') if ended else None,
    }


class Evaluation:
    """One measured game's record, kept at path. It pins the game's config.json, at config_path, and a deadline of
    seconds from now. An existing record is never reset or replaced."""

    def __init__(self, path, game_id, config_path, seconds):
        if type(seconds) not in (int, float) or not 0 < seconds <= config.DEADLINE_SECONDS:
            raise ValueError(f'An evaluation lasts more than 0 and at most {config.DEADLINE_SECONDS} seconds')
        raw = config_path.read_bytes()
        self.path, self.config_path = path, config_path
        self.record = {
            'game': game_id,
            'config': json.loads(raw),
            'config_sha256': hashlib.sha256(raw).hexdigest(),
            'deadline_seconds': seconds,
            'worker_started': False,
            'reason': None,
            'active_seconds': None,
            'cleanup': None,
            'evidence': None,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x') as stream:
            json.dump(self.record, stream, indent=2)
        self.started = time.time()  # wall-clock: on macOS time.monotonic stops while the host sleeps

    def remaining(self):
        return self.started + self.record['deadline_seconds'] - time.time()

    def claim_worker(self):
        """Mark the game's only worker as started; a second start is a restart, which closes the game."""
        if self.record['worker_started']:
            self.close('interrupted')
            raise RuntimeError(f'The worker of evaluated game {self.record["game"]} cannot restart')
        self.record['worker_started'] = True
        self.save()

    def admit(self, call, payload):
        """call(payload), once the config and the deadline hold. A Stop it raises closes the game with its reason. A
        call still running at the deadline closes the game. It finishes in its own thread, which the interpreter
        waits for at exit, so the ledger is still charged; its reply is dropped."""
        if file_hash(self.config_path) != self.record['config_sha256']:
            self.close('configuration_changed')
        elif self.remaining() <= 0:
            self.close('deadline')
        if self.record['reason']:
            raise Stop(self.record['reason'])
        pool = concurrent.futures.ThreadPoolExecutor(1)
        future = pool.submit(call, payload)
        pool.shutdown(wait=False)
        done, _ = concurrent.futures.wait([future], timeout=self.remaining())
        if not done or self.remaining() <= 0:  # the wait's own clock can stop while the host sleeps
            self.close('deadline')
            raise Stop('deadline')
        try:
            return future.result()
        except Stop as stop:
            self.close(str(stop))
            raise

    def close(self, reason):
        """Record the closing reason and the active seconds; the first close stands."""
        if self.record['reason'] is None:
            self.record |= {'reason': reason, 'active_seconds': round(time.time() - self.started, 3)}
            self.save()

    def finish(self, reason, confirmed, journal):
        """Close the game with reason unless it closed earlier, and record whether the worker's exit was confirmed,
        how long after the close it came and, once it is confirmed, the final evidence in journal."""
        self.close(reason)
        seconds = time.time() - self.started - self.record['active_seconds']
        self.record['cleanup'] = {'seconds': round(seconds, 3), 'confirmed': confirmed}
        if confirmed:
            self.record['evidence'] = capture_evidence(journal)
        self.save()

    def save(self):
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.record, indent=2))
        temporary.replace(self.path)
