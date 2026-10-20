"""A read-only MCP server over the recorded games in samples/ and data/games/, over stdio.

    claude mcp add handshake-island -- uv run --directory /path/to/handshake-island python scripts/mcp_server.py

Five tools: list_games, get_game, verify_game (replay), search_events and get_turn. A game is named by the id
list_games gives it, never by a path. Every tool only reads recordings: none writes, calls a model or reaches the
network. The residents' words and thinking come back verbatim, as data.
"""

import sys
from collections import Counter
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from handshake import replay  # noqa: E402
from handshake.island import IDS  # noqa: E402

SAMPLES, GAMES = ROOT / 'samples', ROOT / 'data' / 'games'
PAGE = 50  # events per search_events page
READ = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)

server = FastMCP('handshake-island')


def recordings():
    """Each recording's path by game id, samples first. A game's directory in data/games/ is named by its id."""
    found = {replay.read(path)['game']: path for path in sorted(SAMPLES.glob('*.json.gz'))}
    for path in sorted(GAMES.glob('*/game-*.json.gz')):
        found.setdefault(path.parent.name, path)
    return found


def load(game):
    path = recordings().get(game)
    if path is None:
        raise ValueError(f'No game {game!r}; list_games names every game')
    return replay.read(path)


@server.tool(annotations=READ)
def list_games() -> list[dict]:
    """Every recorded game: its id, seed, arm, the player of each seat and how it closed."""
    games = []
    for game, path in recordings().items():
        recording = replay.read(path)
        cfg = recording['config']
        sample = path.parent == SAMPLES
        games.append(
            {
                'game': game,
                'seed': cfg['seed'],
                'arm': cfg['arm'],
                'seats': cfg['seats'],
                'reason': recording['reason'],
                'sample': sample,
            }
        )
    return games


@server.tool(annotations=READ)
def get_game(game: str) -> dict:
    """One game's config, secret projects and values, starting holdings, final holdings, scores, completed projects,
    unpaid sides and efficiency (completed value over the best possible, for a complete game), with counts of its
    turns, calls and events by kind, and the cost of its calls at list prices."""
    recording = load(game)
    journal = recording['journal']
    out = {'game': game, 'config': recording['config'], 'reason': recording['reason']}
    start = next((line for line in journal if line['kind'] == 'game'), None)
    if start:
        out |= {'projects': start['projects'], 'values': start['values'], 'start': start['hold']}
    if journal and journal[-1]['kind'] == 'end':  # a stopped game's end line holds the standings where it stopped
        end = journal[-1]
        out |= {key: end[key] for key in ('hold', 'scores', 'complete', 'broken', 'optimum')}
        if recording['reason'] == 'complete':
            out['efficiency'] = sum(start['values'][r] for r in end['complete']) / end['optimum']
    calls = [line['reply'] for line in journal if line['kind'] == 'call']
    out['metrics'] = {
        'turns': sum(line['kind'] == 'turn' for line in journal),
        'calls': len(calls),
        'events': dict(Counter(line['event'] for line in journal if line['kind'] == 'event')),
        'cost_usd': round(sum(reply['cost_usd'] for reply in calls), 6),
    }
    return out


@server.tool(annotations=READ)
def verify_game(game: str) -> dict:
    """Replay the game through the game loop and say whether every journal line matches its recording."""
    try:
        replay.verify(load(game))
    except replay.ReplayError as error:
        return {'game': game, 'verified': False, 'error': str(error)}
    return {'game': game, 'verified': True}


@server.tool(annotations=READ)
def search_events(
    game: str,
    resident: Literal[IDS] | None = None,
    kind: str | None = None,
    round: int | None = None,
    offset: int = 0,
) -> dict:
    """A game's events in order, a page of 50 from offset, filtered by the resident who acted, the kind (offer,
    accept, decline, lapse, swap, swap_failed, deliver, hold, say, default) and the round (settlement is round 9)."""
    events = [
        line
        for line in load(game)['journal']
        if line['kind'] == 'event'
        and resident in (None, line['actor'])
        and kind in (None, line['event'])
        and round in (None, line['round'])
    ]
    return {'total': len(events), 'offset': offset, 'events': events[offset : offset + PAGE]}


@server.tool(annotations=READ)
def get_turn(game: str, resident: Literal[IDS], round: int) -> dict:
    """The decision behind one turn: each attempt's request (the text and tool results the resident was sent) and
    reply (its thinking, tool call, stop reason, usage and cost), then the act the kernel applied and its receipt."""
    journal = load(game)['journal']
    mine = [line for line in journal if line.get('resident') == resident and line.get('round') == round]
    calls = [line for line in mine if line['kind'] == 'call']
    turn = next((line for line in mine if line['kind'] == 'turn'), None)
    if turn is None and not calls:
        raise ValueError(f'{resident} has no turn in round {round} of {game}')
    return {
        'attempts': [{'attempt': c['attempt'], 'request': c['turn'], 'reply': c['reply']} for c in calls],
        'act': turn and turn['act'],
        'receipt': turn and turn['receipt'],
    }


if __name__ == '__main__':
    server.run()
