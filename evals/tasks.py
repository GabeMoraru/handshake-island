"""The tasks of the decision evals (design §4.10), frozen from E1's recordings into evals/tasks.json.gz:

    uv run python scripts/eval.py freeze RECORDING...

A task is one resident's turn in an E1 game: the payload of its first call exactly as E1 sent it, but with every
thinking block removed, the acts before the turn, from which kernel(task) deals and plays the game to that turn, and
E1's reply. A first call is a candidate where E1's reply was a valid act and the kernel at the call puts it in a
stratum, judged by the graders' own predicates. The draw is fixed: candidates in arm, seed, round and seat order, one
random.Random(0), the strata in STRATA's order and each one's arms in island.E1_ARMS order, each pool shuffled and its
first tasks taken from games not used yet, so the 40 tasks come from 40 games. Every second task, from the first, is
also a Sonnet task.

Freezing refuses a recording that does not replay, one not all Haiku or not closed complete, and one whose calls do
not render to the requests E1 sent.
"""

import gzip
import hashlib
import io
import json
import random
from pathlib import Path

from evals import arms, graders
from handshake import config, game, island, providers, replay
from handshake.island import E1_ARMS, IDS, ROUNDS, SETTLEMENT
from handshake.protocol import encode

TASKS = Path(__file__).resolve().parent / 'tasks.json.gz'
HAIKU = 'claude-haiku-5-5'
HANDSHAKE = tuple(a for a in E1_ARMS if a.startswith('handshake'))
TALK = tuple(a for a in E1_ARMS if a.endswith('talk'))
# (kind, the game arms it draws from, tasks from each), in the order of the draw
STRATA = (
    ('dominant', E1_ARMS, 2),
    ('harmful', E1_ARMS, 2),
    ('owed_settlement', HANDSHAKE, 3),
    ('owed_in_play', HANDSHAKE, 3),
    ('words', TALK, 6),
)
THINKING = ('thinking', 'redacted_thinking')


def sha256(payload, game_id):
    """The hash of the request a payload renders to, as the broker records it."""
    request = providers.ADAPTERS[payload['model']].render(payload, game_id)
    return hashlib.sha256(encode(request).encode()).hexdigest()


def kinds(g, opening):
    """The strata of a first call, from the kernel at the call and the user turn that opens the turn."""
    rnd, me = g.turn
    playing = rnd <= ROUNDS
    out = []
    if playing and any(graders.dominant(g, o) for o in g.due(me)):
        out.append('dominant')
    if playing and any(graders.harmful(g, me, o['give'], o['get']) for o in g.due(me)):
        out.append('harmful')
    if graders.paid(g):
        out.append('owed_settlement' if rnd == SETTLEMENT else 'owed_in_play')
    if playing and g.arm in TALK and any(line.startswith(f'- {b} -> ') for line in arms.news(opening) for b in IDS):
        out.append('words')
    return out


def candidates(path):
    """The candidates of one E1 recording, each with the order it is drawn in."""
    recording = replay.read(path)
    replay.verify(recording)
    cfg, journal = recording['config'], recording['journal']
    if set(cfg['seats'].values()) != {HAIKU} or recording['reason'] != 'complete':
        raise ValueError(f'{path.name} is not an E1 game: every seat Haiku, closed complete')
    calls, acts, before = [], [], []  # each call's line, each turn's act, and the turns before each call
    for line in journal:
        if line['kind'] == 'call':
            calls.append(line)
            before.append(len(acts))
        elif line['kind'] == 'turn':
            acts.append(line['act'])
    found, made = [], iter(range(len(calls)))

    def model_call(payload):
        i = next(made)
        reply, g = calls[i]['reply'], session.game
        if sha256(payload, recording['game']) != reply['request_sha256']:
            raise ValueError(f'{path.name}: call {i + 1} does not render to the request E1 sent')
        if payload['attempt'] == 1 and (stratum := kinds(g, payload['history'][-1])):
            if graders.effects(g, reply) is not None:
                rnd, me = g.turn
                found.append(
                    {
                        'order': (E1_ARMS.index(g.arm), g.seed, rnd, g.at),
                        'kinds': stratum,
                        'id': f'{g.arm}/s{g.seed}/r{rnd}/{me}',
                        'source': path.name,
                        'seed': g.seed,
                        'arm': g.arm,
                        'payload': payload | {'history': list(payload['history'])},  # history only ever grows
                        'acts': acts[: before[i]],
                        'recorded': {'stop': reply['stop'], 'calls': reply['calls']},
                    }
                )
        return reply

    session = game.Session(cfg, model_call, io.StringIO())
    session.play()
    return found


def strip(history):
    """The history with every thinking block removed from its replies."""
    out = []
    for turn in history:
        if 'native' in turn:
            native = [b for b in turn['native'] if b['type'] not in THINKING]
            if not native:
                raise ValueError('Removing thinking leaves a reply empty')
            turn = turn | {'native': native}
        out.append(turn)
    return out


def select(found):
    """The tasks drawn from every candidate (see the module's docstring)."""
    found = sorted(found, key=lambda c: c['order'])
    rng, used, tasks = random.Random(0), set(), []
    for kind, game_arms, n in STRATA:
        for arm in game_arms:
            pool = [c for c in found if kind in c['kinds'] and c['arm'] == arm]
            rng.shuffle(pool)
            taken = 0
            for c in pool:
                if taken < n and (arm, c['seed']) not in used:
                    used.add((arm, c['seed']))
                    taken += 1
                    tasks.append(as_task(c, kind, sonnet=len(tasks) % 2 == 0))
            if taken < n:
                raise ValueError(f'{arm} has {taken} games with a {kind} task, not {n}')
    return tasks


def as_task(c, kind, sonnet):
    """A candidate drawn for kind, as the task file keeps it."""
    p = c['payload']
    return {
        'id': c['id'],
        'kind': kind,
        'sonnet': sonnet,
        'source': c['source'],
        'seed': c['seed'],
        'arm': c['arm'],
        'payload': p | {'history': strip(p['history'])},
        'acts': c['acts'],
        'recorded': c['recorded'],
    }


def freeze(paths):
    """The task file's bytes, the same for the same recordings."""
    found = [c for path in paths for c in candidates(path)]
    document = {'version': config.CONFIG_VERSION, 'tasks': select(found)}
    return gzip.compress(json.dumps(document).encode(), mtime=0)


def kernel(task):
    """The game at the task's turn: dealt from its seed, with the acts before the turn applied."""
    g = island.Game(task['seed'], task['arm'])
    for act in task['acts']:
        g.act(act) if act is not None else g.default_act()
    return g


def load(path=TASKS):
    """The tasks, refused unless they are this version's, their acts still replay to their turns, and their system
    blocks, tool, reminders and the state that opens the turn are what the game renders now."""
    document = json.loads(gzip.decompress(path.read_bytes()))
    if document['version'] != config.CONFIG_VERSION:
        raise ValueError(
            f"{path.name} is configuration version {document['version']}, not {config.CONFIG_VERSION}; once E1's "
            'recordings are deleted with their version (decision 0004) it cannot be frozen again'
        )
    for task in document['tasks']:
        try:
            g = kernel(task)
        except (island.Reject, ValueError) as error:
            raise ValueError(f'The acts of {task["id"]} no longer replay: {error}') from error
        p = task['payload']
        me, opening = p['resident'], p['history'][-1]
        if g.turn != (p['round'], me):
            raise ValueError(f'The acts of {task["id"]} lead to {g.turn}, not its turn')
        reminders = {t['reminder'] for t in p['history'] if 'native' not in t} - {None}
        if (
            p['system'] != game.system(g, me)
            or p['tool'] != island.act_tool(g.arm)
            or reminders != {game.reminder(g, me)}
            # the replay never empties an inbox, so only the state, not the news, can be compared
            or (opening['text'] or opening['results'][0]['content']).partition(arms.STATE)[2]
            != g.view().partition(arms.STATE)[2]
        ):
            raise ValueError(
                f'The system blocks, tool, reminders or state of {task["id"]} are not what the game renders now'
            )
    return document['tasks']
