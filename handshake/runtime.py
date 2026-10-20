"""One game on the host: its worker in one sandbox, its model calls through one broker.

A game's directory holds its config.json. The worker writes journal.jsonl there and the broker host.jsonl, its
retries and stops, to which Runtime adds any error that ended the sandbox. Every measured game runs in evaluation
mode (begin_evaluation), whose record lives in runtime/evaluations/<id>.json, outside the guest's directory.
"""

import functools
import json
from pathlib import Path

from handshake import config
from handshake.broker import Broker
from handshake.evaluation import Evaluation
from handshake.sandbox import RUNTIME, Sandbox, SandboxError

EVALUATIONS = RUNTIME / 'evaluations'


class Runtime:
    """One game, named by its directory. Its broker refuses any call that could take the spend ledger past cap."""

    def __init__(self, data_dir, cap=config.BUDGET_USD):
        self.data_dir, self.cap = Path(data_dir), cap
        self.id = self.data_dir.name
        self.config = config.validate_config(json.loads((self.data_dir / 'config.json').read_text()))
        self.evaluation = None  # set by begin_evaluation, which every game passes before it plays

    def begin_evaluation(self, seconds=config.DEADLINE_SECONDS):
        """Enter evaluation mode before the worker starts: pin the config's hash and a deadline. Only a fresh game
        enters, one with no record and neither journal."""
        path = EVALUATIONS / f'{self.id}.json'
        if path.exists() or any(self.data_dir.glob('*.jsonl')):
            raise RuntimeError(f'Only a fresh game enters evaluation, and {self.id} has started')
        self.evaluation = Evaluation(path, self.id, self.data_dir / 'config.json', seconds)
        return self.evaluation

    def play(self):
        """Play the game in evaluation mode and return its closing reason. If the relay or the broker refuses what
        the worker sent, the sandbox stops the worker and the game closes as sandbox_error. The evaluation record
        then gets the outcome and, once the worker has exited, the final evidence."""
        self.evaluation.claim_worker()
        reason, confirmed = 'error', False
        try:
            with (self.data_dir / 'host.jsonl').open('x') as journal:
                broker = Broker(self.id, self.config, journal, cap=self.cap)
                try:
                    reason = Sandbox(self.data_dir, functools.partial(self.evaluation.admit, broker)).play()
                except (SandboxError, ValueError) as error:
                    broker.write('error', error=str(error))
                    reason = 'sandbox_error'
                confirmed = True  # Sandbox.play stops its container before it returns or raises
            return reason
        finally:
            self.evaluation.finish(reason, confirmed, self.data_dir / 'journal.jsonl')
