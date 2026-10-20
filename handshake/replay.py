"""A game's recording, and its replay through the game loop.

The host writes one recording per game when the game closes, whatever closed it: a gzipped JSON object

    {"game": id, "config": the game config, "reason": closing reason,
     "journal": the worker's journal lines, "host": the broker's journal lines}

with each journal as far as it got. Replay plays the game again through the game loop: a fresh kernel deals from the
seed, the scripts play their seats, and each model seat gets its recorded replies in order in place of a model. It
refuses the recording, with one ReplayError, if its config is another CONFIG_VERSION or if the replay writes any line
of the journal differently, so every act, default, script move, receipt, event, holding and score must match.

    uv run python -m handshake.replay data/games/<id>/game-1-handshake-talk-haiku.json.gz

prints the verdict, and exits with status 1 on a refusal.
"""

import argparse
import gzip
import io
import json
import stat
import sys
import zlib
from pathlib import Path

from handshake import config, game, providers

KEYS = ('game', 'config', 'reason', 'journal', 'host')


class ReplayError(ValueError):
    """The recording is not one this version replays, or differs from its replay."""


def lines(path):
    """A journal's whole lines, or none if it was never written. A last line cut off mid-write, by a killed worker or
    a full disk, has no newline yet and is left out."""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return []
    if not stat.S_ISREG(mode):  # the guest can write the game's directory, and a link there could reach a host file
        raise ValueError(f'{path} is not a plain file')
    return [json.loads(line) for line in path.read_text().splitlines(keepends=True) if line.endswith('\n')]


def write(data, cfg, reason, path):
    """Write the recording of the game in directory data, played with cfg and closed for reason, to path. The config
    is the host's own, since the guest can rewrite config.json once its last call is made."""
    recording = {
        'game': data.name,
        'config': cfg,
        'reason': reason,
        'journal': lines(data / 'journal.jsonl'),
        'host': lines(data / 'host.jsonl'),
    }
    path.write_bytes(gzip.compress(json.dumps(recording).encode(), mtime=0))


def read(path):
    """A recording as written, unchecked but for being a JSON object."""
    try:
        recording = json.loads(gzip.decompress(path.read_bytes()))
    except (OSError, EOFError, zlib.error, ValueError) as error:
        raise ReplayError(f'{path} is not a recording: {error}') from None
    if not isinstance(recording, dict):
        raise ReplayError(f'{path} is not a recording: it holds no JSON object')
    return recording


def verify(recording):
    """Replay a recording; raise ReplayError unless its config is this version's and the replay matches it."""
    try:  # the config's version first, so a recording of another version gets that error whatever its shape
        cfg = config.validate_config(recording.get('config'))
    except ValueError as error:
        raise ReplayError(str(error)) from None
    if set(recording) != set(KEYS):
        raise ReplayError(f'A recording has exactly {", ".join(KEYS)}')
    journal, reason = recording['journal'], recording['reason']
    if not isinstance(journal, list) or not all(isinstance(line, dict) for line in journal):
        raise ReplayError('The journal must be a list of JSON objects')
    replayed = replay(cfg, journal, reason)
    # A worker stopped before its end line left a shorter journal, which must match the replay as far as it goes.
    for n, (line, replayed_line) in enumerate(zip(journal, replayed, strict=False), 1):
        if line != replayed_line:
            keys = sorted(k for k in line.keys() | replayed_line.keys() if line.get(k) != replayed_line.get(k))
            raise ReplayError(f'Journal line {n} ({line.get("kind")}) differs from the replay in {", ".join(keys)}')
    if len(journal) > len(replayed):
        extra = journal[len(replayed)]
        raise ReplayError(f'Journal line {len(replayed) + 1} ({extra.get("kind")}) comes after the replay ended')
    if journal and journal[-1]['kind'] == 'end':
        if journal[-1]['reason'] != reason:
            raise ReplayError(f'The recording closes as {reason}, but its journal as {journal[-1]["reason"]}')
    elif reason == 'complete':
        raise ReplayError('The recording closes as complete, but its journal has no end line')


def hosted_only(recording):
    """Refuse a recording with a seat played by the local model, for the readers of hosted replies."""
    # shortcut: report.py and transcript.py are checked on Anthropic and OpenAI replies only, and jev_validate.py
    # loads through report.py; check them on Qwen's with the first Qwen cast
    for a, seat in recording['config']['seats'].items():
        if providers.ADAPTERS.get(seat) is providers.Chat:
            raise ReplayError(f'seat {a} is played by {seat}, and only hosted replies can be read')


def replay(cfg, journal, reason):
    """The journal lines the game loop writes for cfg when each model seat gets the journal's replies in order. Where
    the replies run out, the game stops with reason, as the recorded game did."""
    replies = iter([line.get('reply') for line in journal if line.get('kind') == 'call'])

    def model_call(payload):
        reply = next(replies, None)
        if reply is None:  # the recorded game stopped here, which a complete game never does
            raise game.Stop('incomplete' if reason == 'complete' else reason)
        # The kernel acts on the calls and later requests carry the native content, so the two must agree.
        if reply['calls'] != providers.ADAPTERS[payload['model']].calls(reply['native']):
            who = f'{payload["resident"]} in round {payload["round"]}, attempt {payload["attempt"]}'
            raise ReplayError(f'The calls of the reply to {who} are not those its content holds')
        return reply

    out = io.StringIO()
    try:
        game.Session(cfg, model_call, out).play()
    except ReplayError:
        raise
    except Exception as error:  # whatever a malformed recording makes the game loop raise, the recording is refused
        raise ReplayError(f'The game loop cannot replay the recording: {error!r}') from None
    return [json.loads(line) for line in out.getvalue().splitlines()]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('recording', type=Path)
    args = parser.parse_args(argv)
    try:
        recording = read(args.recording)
        verify(recording)
    except ReplayError as error:
        sys.exit(f'Refused {args.recording}: {error}')
    cfg, reason = recording['config'], recording['reason']
    turns = sum(line['kind'] == 'turn' for line in recording['journal'])
    print(f'Verified {args.recording}: seed {cfg["seed"]}, {cfg["arm"]}, closed as {reason} after {turns} turns.')


if __name__ == '__main__':
    main()
