"""The host's only egress for one game's model calls.

The worker's payloads are untrusted. The broker refuses any the game loop would not send: the model must be the one
the config seats, system, tool and reminder must be the game's, each earlier reply must come back exactly as the
broker returned it, and the user turns may hold only text. Then it renders the request with the adapter, holds its
worst-case cost against the spend ledger's cap while it runs, calls the provider with the host's key, charges the
ledger and returns the reply. Each call is one OpenTelemetry span (handshake.telemetry).

It retries a 429, a 5xx, a timeout or a connection error (refused, reset or dropped) at most twice, journaling each
retry. Anything else that fails stops the game with its closing reason, journaled with the error: provider_error (a
send or parse that fails outside the API's errors among them, charged its worst case since it may have been billed),
serializer_error (a block-binding 400, or a reply whose input was transformed) or budget.
"""

import contextlib
import hashlib
import itertools
import json
import threading
import time
from datetime import UTC, datetime

import anthropic
import openai

from handshake import config, game, island, providers, telemetry
from handshake.game import Stop
from handshake.protocol import encode
from handshake.sandbox import RUNTIME

LEDGER = RUNTIME / 'spend.jsonl'  # shared by every model and Jev call in the project
RETRIES = 2
WAIT = 10  # seconds before a retry, unless a 429 says how long
BINDING = 'bound to a different conversation'  # the 400 an edited history gets in "error" mode
PAYLOAD = {'resident', 'round', 'attempt', 'model', 'system', 'tool', 'history'}
STATUS = (anthropic.APIStatusError, openai.APIStatusError)
CONNECTION = (anthropic.APIConnectionError, openai.APIConnectionError)  # a timeout is one too
LOCK = threading.Lock()  # for the ledger and running, shared by the games this process plays at once
running = 0.0  # USD held for the calls in flight, each at its worst case


def spent():
    """The project's total so far at list prices: P0's, then every call in the ledger."""
    lines = LEDGER.read_text().splitlines() if LEDGER.exists() else []
    return config.P0_USD + sum(json.loads(line)['cost_usd'] for line in lines)


def charge(model, cost):
    """Write one call's cost to the spend ledger."""
    line = {'time': datetime.now(UTC).isoformat(timespec='seconds'), 'model': model, 'cost_usd': cost}
    LEDGER.parent.mkdir(parents=True, exist_ok=True)  # runtime/ is made by setup, which Jev's script may run without
    with LOCK, LEDGER.open('a') as ledger:
        ledger.write(json.dumps(line) + '\n')


def worst(model, request):
    """The most a request can cost: each of its bytes an input token at the dearest input price, which bounds its
    input tokens, and the adapter's output cap (max_output)."""
    usage = {
        'input_tokens': 0,
        'cache_write_tokens': len(encode(request).encode()),
        'cache_read_tokens': 0,
        'output_tokens': providers.ADAPTERS[model].max_output,
    }
    return providers.cost(model, usage)


def retryable(error):
    return isinstance(error, CONNECTION) or (
        isinstance(error, STATUS) and (error.status_code == 429 or error.status_code >= 500)
    )


def wait(error):
    """Seconds before a retry: what a 429's retry-after asks for, up to a minute, or WAIT."""
    after = error.response.headers.get('retry-after', '') if isinstance(error, STATUS) else ''
    return min(int(after), 60) if after.isdigit() else WAIT


def is_reply(turn):
    return isinstance(turn, dict) and 'native' in turn


def text_only(turn):
    """Whether a user turn has a string wherever the request takes text, so it carries no image or document for the
    provider to fetch."""
    if not isinstance(turn, dict) or set(turn) != {'results', 'text', 'reminder'}:
        return False
    results = turn['results']
    return (
        all(text is None or isinstance(text, str) for text in (turn['text'], turn['reminder']))
        and isinstance(results, list)
        and all(isinstance(r, dict) and set(r) == {'call_id', 'content', 'is_error'} for r in results)
        and all(isinstance(r['call_id'], str) and isinstance(r['content'], str) for r in results)
        and all(isinstance(r['is_error'], bool) for r in results)
    )


class Broker:
    """One game's model calls: game_id names the game in its spans, cfg is its pinned config, journal a text file
    for retries and stops, and transport replaces the network in tests. A call is refused unless the ledger and the
    calls still running leave room under cap for its worst case, so games played at once never take the ledger past
    cap, which never exceeds the project's budget. Call it with a payload to get the reply."""

    def __init__(self, game_id, cfg, journal, transport=None, cap=config.BUDGET_USD):
        cfg = config.validate_config(cfg)
        self.cap = min(cap, config.BUDGET_USD)
        self.models = {a: seat for a, seat in cfg['seats'].items() if not seat.startswith('script:')}
        unpriced = sorted(set(self.models.values()) - set(providers.PRICES))
        if unpriced:
            raise ValueError(f'No price for {", ".join(unpriced)}; models: {", ".join(providers.PRICES)}')
        g = island.Game(cfg['seed'], cfg['arm'])
        self.system = {a: game.system(g, a) for a in self.models}
        self.tool = island.act_tool(g.arm)
        self.reminder = {a: game.reminder(g, a) for a in self.models}
        self.replies = {a: [] for a in self.models}  # each resident's assistant turns, as the worker appends them
        self.clients = {m: providers.ADAPTERS[m].client(transport) for m in set(self.models.values())}
        self.game_id, self.journal = game_id, journal

    def __call__(self, payload):
        self.check(payload)
        where = {k: payload[k] for k in ('resident', 'round', 'attempt')}
        model = payload['model']
        adapter = providers.ADAPTERS[model]
        request = adapter.render(payload, self.game_id)
        most = worst(model, request)
        with self.hold(where, most), telemetry.span(self.game_id, payload):
            try:
                body, seconds, retries = self.send(where, request)
                reply = adapter.parse(model, body)
            # a send or parse that fails outside the API's errors may have been billed: its worst case
            except (AttributeError, KeyError, RecursionError, TypeError, ValueError) as error:
                charge(model, most)
                raise self.stop(where, 'provider_error', error=f'{type(error).__name__}: {error}') from error
            reply |= {
                'request_sha256': hashlib.sha256(encode(request).encode()).hexdigest(),
                'seconds': round(seconds, 3),
                'retries': retries,
            }
            charge(model, reply['cost_usd'])
            telemetry.record(model, body, reply)
            if reply['input_transformations']:
                raise self.stop(where, 'serializer_error', reply=reply)
        if reply['stop'] != 'incomplete':  # the game loop never appends a cut-off reply
            self.replies[payload['resident']].append(
                {'native': reply['native'], 'call_ids': [c['id'] for c in reply['calls']]}
            )
        return reply

    def check(self, payload):
        """Refuse a payload the game loop would not send."""
        if not isinstance(payload, dict) or set(payload) != PAYLOAD:
            raise ValueError(f'A model request has exactly {", ".join(sorted(PAYLOAD))}')
        me, history = payload['resident'], payload['history']
        if me not in self.models or payload['model'] != self.models[me]:
            raise ValueError(f'{me} is not seated with {payload["model"]}')
        if payload['system'] != self.system[me] or payload['tool'] != self.tool:
            raise ValueError(f'The system blocks and tool of {me} are fixed by the game config')
        if not isinstance(history, list) or not history or is_reply(history[-1]):
            raise ValueError(f'The history of {me} must end with a new user turn')
        if is_reply(history[0]) or any(is_reply(a) and is_reply(b) for a, b in itertools.pairwise(history)):
            raise ValueError(f'The history of {me} must put a user turn before each reply')
        # two user turns meet only where a cut-off reply was never appended
        if not providers.ADAPTERS[payload['model']].incomplete and any(
            not is_reply(a) and not is_reply(b) for a, b in itertools.pairwise(history)
        ):
            raise ValueError(f'The history of {me} must put a reply between its user turns')
        replies = [t for t in history if is_reply(t)]
        users = [t for t in history if not is_reply(t)]
        if replies != self.replies[me]:
            raise ValueError(f'The history of {me} must carry back every reply exactly as it was returned')
        if not all(text_only(t) for t in users):
            raise ValueError(f'The user turns of {me} may hold only text')
        if any(t['reminder'] not in (None, self.reminder[me]) for t in users):
            raise ValueError(f'The reminder of {me} is fixed by the game config')

    @contextlib.contextmanager
    def hold(self, where, cost):
        """Hold cost against the cap while a call runs, or stop the game as budget if there is no room for it."""
        global running
        with LOCK:
            if spent() + running + cost > self.cap:
                raise self.stop(
                    where, 'budget', error=f'A call of up to ${cost:.4f} could take the ledger past ${self.cap:.2f}'
                )
            running += cost
        try:
            yield
        finally:
            with LOCK:
                running -= cost

    def send(self, where, request):
        """The reply body, the seconds the successful attempt took, and how many retries came before it."""
        model = request['model']
        for retry in range(RETRIES + 1):
            started = time.monotonic()
            try:
                return providers.ADAPTERS[model].send(self.clients[model], request), time.monotonic() - started, retry
            except (anthropic.APIError, openai.APIError) as error:
                if retry == RETRIES or not retryable(error):
                    reason = 'serializer_error' if BINDING in str(error) else 'provider_error'
                    # OpenAI's 401 repeats a masked part of the key it got, which no journal keeps
                    said = f'Error code: 401 - {error.code}' if isinstance(error, openai.AuthenticationError) else error
                    raise self.stop(where, reason, error=str(said)) from error
                self.write('retry', **where, retry=retry + 1, error=str(error))
                telemetry.retry(error)
                time.sleep(wait(error))

    def stop(self, where, reason, **why):
        """Journal why the game stops; return the Stop to raise."""
        self.write('stop', **where, reason=reason, **why)
        return Stop(reason)

    def write(self, kind, **line):
        self.journal.write(json.dumps({'kind': kind, **line}) + '\n')
        self.journal.flush()  # a game that fails keeps its trace
