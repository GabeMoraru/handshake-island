"""Play one game in the pinned worker image, five scripted seats and one behind a canned broker, and require it to
match the same game played on the host: the same requests and the same journal. No model is called.

The evidence stays in a new temporary directory, whose path is printed with the result.
"""

import io
import itertools
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from handshake import config, game, island, sandbox  # noqa: E402

ARM = 'handshake-talk'
SEATS = {
    'mara': 'canned',
    'rowan': 'script:Smart',
    'marcus': 'script:SmartCautious',
    'theo': 'script:SmartReneger',
    'nia': 'script:Gossip',
    'felix': 'script:CautiousRevealer',
}
CONFIG = {'version': config.CONFIG_VERSION, 'seed': 1, 'arm': ARM, 'seats': SEATS}
THINK = {'type': 'thinking', 'thinking': '', 'signature': 'EpYBCkgIBxABGAIiQ'}


class Canned:
    """A broker that answers every request with a thinking block and an act that does nothing. The kernel accepts
    it when nothing is due and otherwise returns its fix, until the turn takes the default action."""

    def __init__(self):
        self.requests, self.ids = [], itertools.count(1)

    def __call__(self, payload):
        self.requests.append(json.loads(json.dumps(payload)))  # the history keeps growing after the call
        call = {'id': f'toolu_{next(self.ids)}', 'name': 'act', 'input': {k: [] for k in island.fields(ARM)}}
        return {'native': [THINK, {'type': 'tool_use', **call}], 'calls': [call], 'stop': 'tool_use'}


def verify():
    output = Path(tempfile.mkdtemp(prefix='handshake-verify-'))
    data = output / 'data'
    data.mkdir()
    data.chmod(0o777)  # Apple Container's guest runs as uid 1000, not as the host user
    (data / 'config.json').write_text(json.dumps(CONFIG))
    on_host, host_journal = Canned(), io.StringIO()
    host_reason = game.Session(CONFIG, on_host, host_journal).play()
    in_container = Canned()
    box = sandbox.Sandbox(data, in_container)
    reason = box.play()
    journal = (data / 'journal.jsonl').read_text()
    end = json.loads(journal.splitlines()[-1])
    checks = {
        'isolation_verified': box.probe['verified'],
        'game_complete': reason == host_reason == 'complete',
        'model_calls_relayed': len(in_container.requests) > 0,
        'requests_match_the_host': in_container.requests == on_host.requests,
        'journal_matches_the_host': journal == host_journal.getvalue(),
    }
    return {
        'output': str(output),
        'passed': all(checks.values()),
        'checks': checks,
        'isolation': box.probe['checks'],
        'model_calls': len(in_container.requests),
        'scores': end['scores'],
        'optimum': end['optimum'],
    }


def main():
    result = verify()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
