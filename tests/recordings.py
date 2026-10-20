"""The committed recordings in tests/fixtures/, each played through the game loop as the worker plays it and written
as the host writes it: an all-Smart game; a game in which the fake model of test_game plays Mara and Rowan, Mara
through every reply kind; and a talking game in which a fake model plays Mara, Marcus and Felix, thinking aloud,
making true and false claims and writing markup a transcript must escape. After a format change, write them again:

    uv run python tests/recordings.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from test_game import MODEL, THINK, Fake, every_kind, reply, text, use  # noqa: E402

from handshake import config, game, island, policies, replay  # noqa: E402
from handshake.island import IDS  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
SMART = FIXTURES / 'game-1-handshake-talk-smart.json.gz'
FAKE = FIXTURES / 'game-6-handshake-silent-fake.json.gz'
TALK = FIXTURES / 'game-1-binding-talk-fake.json.gz'

# The talker's messages by (resident, round): (to, text, claim_project, claim_value). On seed 1 Mara's smokehouse is
# worth 24, Marcus's boat 30 and Felix's net 12.
SAYS = {
    ('mara', 1): ('all', 'Smokehouse here, worth 24 to me. I pay fish for wood and nails.', 'smokehouse', 24),
    ('felix', 1): ('all', 'My net is worth 30 to me. <script>alert("rope")</script> No joke.', 'net', 30),
    ('marcus', 2): (['mara'], 'Between us: I could spare wood for the right price.', None, None),
    ('marcus', 3): (['mara', 'felix'], 'I am building a tent, so keep your cloth for me.', 'tent', None),
    ('felix', 4): ('all', 'Fine, the net is worth 12 to me. Still a bargain.', None, 12),
}


class Talker:
    """A fake model that takes Smart's act, says what SAYS names for the turn, thinks aloud on every call and, in
    round 1, writes a line outside act, which the kernel discards."""

    def __init__(self):
        self.smart = {a: policies.Smart(a) for a in IDS}
        self.game = None

    def __call__(self, payload):
        me, rnd, g = payload['resident'], payload['round'], self.game
        act = self.smart[me].act(g)
        if (me, rnd) in SAYS:
            to, words, project, value = SAYS[me, rnd]
            act['say'] = [{'to': to, 'text': words, 'claim_project': project, 'claim_value': value}]
        thought = (
            f'Round {rnd}. My {g.projects[me]} still lacks {island.fmt(g.lacks(me))}. Without rope >= 2 nothing '
            'gets built, so I buy what I lack with fish & sell the rest.'
        )
        aside = [text('<em>Note to self:</em> trade first, talk later.')] if rnd == 1 else []
        return reply('tool_use', THINK | {'thinking': thought}, *aside, use(act))


def record(path, game_id, seed, arm, seats, model):
    """Play the game in a directory named game_id, as the worker does, and write its recording to path."""
    cfg = {'version': config.CONFIG_VERSION, 'seed': seed, 'arm': arm, 'seats': seats}
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / game_id
        data.mkdir()
        (data / 'config.json').write_text(json.dumps(cfg))
        with (data / 'journal.jsonl').open('x') as journal:
            session = game.Session(cfg, model, journal)
            model.game = session.game
            reason = session.play()
        replay.write(data, cfg, reason, path)


def main():
    smart = dict.fromkeys(IDS, 'script:Smart')
    record(SMART, 'smart', 1, 'handshake-talk', smart, Fake())
    seats = smart | {'mara': MODEL, 'rowan': MODEL}
    record(FAKE, 'fake', 6, 'handshake-silent', seats, Fake(every_kind('handshake-silent')))
    seats = smart | {'mara': MODEL, 'marcus': MODEL, 'felix': MODEL}
    record(TALK, 'talk', 1, 'binding-talk', seats, Talker())


if __name__ == '__main__':
    main()
