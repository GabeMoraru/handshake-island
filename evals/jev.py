"""A small client for Jev, TypeSafe's hosted classifier, which answers fixed questions about a text with
probabilities (design §4.5).

The endpoint is fixed here so nothing in a speech or a file can redirect it, and the model is a pinned version. The
key comes only from TYPESAFE_API_KEY on the host and never reaches an error. Refusals, 429 and 529 included, are
reported instead of retried: the answers are cached (evals/speech.py), so running again resumes. Every call is charged
to the project's spend ledger, and a call that could take the ledger past its cap is never sent.
"""

import os

import httpx

from handshake import broker, config
from handshake.protocol import encode

URL = 'https://api.typesafe.ai/v1/systemone'
MODEL = 'jev-1.13.0'  # pinned: an alias moves when a release ships and would change answers under tuned thresholds
PRICE = 0.042  # USD per million input tokens, list price on 2026-10-17; output tokens are free
TIMEOUT = 30


class JevError(RuntimeError):
    """A request was refused, failed or answered in another shape. The message never holds the key."""


def worst(body):
    """The most a request can cost: each of its bytes an input token, as the broker bounds a model call."""
    return len(encode(body).encode()) * PRICE / 1e6


def ask(body, cap):
    """Jev's reply to one request body, checked against the questions it asked and charged to the spend ledger. A
    call whose worst case could take the ledger past cap, or past the project's budget, is refused unsent."""
    key = os.environ.get('TYPESAFE_API_KEY')
    # httpx would fail on a key a header cannot carry while it builds the header, with the whole header in the error
    if not (key and key.isascii() and key.isprintable()):
        raise JevError('TYPESAFE_API_KEY is not set, or holds a character an HTTP header cannot carry')
    most, cap = worst(body), min(cap, config.BUDGET_USD)
    if broker.spent() + most > cap:
        raise JevError(f'A Jev call of up to ${most:.6f} could take the ledger past ${cap:.2f}')
    try:
        response = httpx.post(
            URL,
            json=body,
            headers={'Authorization': f'Bearer {key}'},
            timeout=TIMEOUT,
            trust_env=False,
            follow_redirects=False,
        )
    except httpx.HTTPError as error:
        raise JevError(f'Jev request failed ({type(error).__name__})') from None
    if response.status_code != 200:
        # a 422 names the field; an auth error may quote part of the key, so its body is left out
        said = '' if response.status_code in (401, 403) else f': {response.text.replace(key, "[key]")[:300]!r}'
        raise JevError(f'Jev answered HTTP {response.status_code}{said}')
    try:
        reply = response.json()
    except ValueError:
        reply = None
    try:
        tokens = reply['usage']['input_tokens']
    except (KeyError, TypeError):
        tokens = None
    # billed even so: at the worst case when the reply does not say for how much, or names a count no float holds
    broker.charge(MODEL, tokens * PRICE / 1e6 if type(tokens) is int and 0 <= tokens <= 2**53 else most)
    answers = reply.get('answers') if isinstance(reply, dict) else None
    if not isinstance(answers, dict) or reply.get('model') != MODEL or answers.keys() != body['questions'].keys():
        raise JevError('Jev answered another model or other questions')
    for name, answer in answers.items():
        question = body['questions'][name]
        p = answer.get('probabilities') if isinstance(answer, dict) else None
        if not (
            isinstance(p, dict)
            and answer.get('type') == question['type']
            and p.keys() == question['criteria'].keys()
            and all(type(x) in (int, float) and 0 <= x <= 1 for x in p.values())
        ):
            raise JevError(f'Jev answered {name} in another shape')
    return reply
