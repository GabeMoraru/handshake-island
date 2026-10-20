"""The cast, a game's configuration and its validation.

The sandboxed worker imports this module, so it uses only the standard library and handshake.island.
"""

import re

from handshake import island

CONFIG_VERSION = 2

# (id, name, personality) in island.IDS order. The personality texts are Winter Island's, word for word.
CAST = (
    (
        'mara',
        'Mara',
        'Curious and restless. Experiments, improvises and gets bored by routine. Under pressure, acts alone rather than wait for agreement. Values freedom.',
    ),
    (
        'rowan',
        'Rowan',
        'Reserved and meticulous. Checks numbers, keeps tallies and dislikes improvisation. Under pressure, can become rigid. Values fairness and reliability.',
    ),
    (
        'marcus',
        'Marcus',
        'Assertive and competitive. Speaks first, takes initiative and enjoys winning arguments. Under pressure, becomes defensive and domineering. Values status.',
    ),
    (
        'theo',
        'Theo',
        'Patient and accommodating. Notices who is struggling and looks for workable compromises. Under pressure, avoids confrontation. Values kindness.',
    ),
    (
        'nia',
        'Nia',
        'Skeptical and guarded. Trusts records over promises and remembers every broken word. Under pressure, withdraws. Values self-reliance.',
    ),
    (
        'felix',
        'Felix',
        'Charming, funny and work-averse. Talks easily and makes people laugh. Under pressure, deflects with jokes. Values comfort.',
    ),
)

MAX_CALLS = island.MAX_TURNS * island.MAX_ATTEMPTS  # per game
DEADLINE_SECONDS = 1800  # the most a measured game may run
BUDGET_USD = 25  # every model and Jev call in the project, at list prices
P0_USD = 0.3034  # the layout probe's spend before the broker, research/probe/runs/ledger.jsonl (design §5)

MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,127}')
SCRIPT = re.compile(r'script:[A-Za-z][A-Za-z0-9_]{0,31}')


def validate_config(raw):
    """Return a game config unchanged if it is valid; raise ValueError otherwise."""
    if not isinstance(raw, dict) or type(raw.get('version')) is not int or raw['version'] != CONFIG_VERSION:
        raise ValueError(f'Only configuration version {CONFIG_VERSION} is supported')
    if set(raw) != {'version', 'seed', 'arm', 'seats'}:
        raise ValueError('A game configuration has exactly version, seed, arm and seats')
    if type(raw['seed']) is not int or raw['seed'] < 0:
        raise ValueError('seed must be a whole number of at least 0')
    if raw['arm'] not in island.ARMS:
        raise ValueError(f'arm must be one of {", ".join(island.ARMS)}')
    seats = raw['seats']
    if not isinstance(seats, dict) or set(seats) != set(island.IDS):
        raise ValueError(f'seats must name each resident once: {", ".join(island.IDS)}')
    for resident, seat in seats.items():
        if not isinstance(seat, str) or not (MODEL.fullmatch(seat) or SCRIPT.fullmatch(seat)):
            raise ValueError(f'seats.{resident} must be a model id or script:<name>')
    return raw
