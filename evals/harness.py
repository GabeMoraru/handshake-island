"""The decision evals' trials, run through the broker outside evaluation mode (design §4.10).

The plan runs Haiku on every arm that applies to each task, then Sonnet's control arm on the Sonnet tasks, k trials of
each in a row so their identical requests find the cache warm. Each trial is one first attempt, never retried by the
game loop, and appends {task, model, arm, index, reply} to <out>/trials.jsonl; the broker journals its retries and
stops to <out>/host.jsonl and charges the spend ledger. A Stop ends the run with no row for its call. A rerun resumes,
once every row already written carries the hash of the request its task, arm and model render now.
"""

import json

from evals import arms, tasks
from handshake import broker, config
from handshake.island import IDS

HAIKU, SONNET = tasks.HAIKU, 'claude-sonnet-5-5'
MODELS = (HAIKU, SONNET)
K = 3


class Broker(broker.Broker):
    """The game's broker for frozen payloads: the same render, worst-case hold, retries, journal, ledger and span. A
    payload is built on the host from the committed task file, carries replies an E1 broker returned with their
    thinking removed and, in persona_off, another persona, so check pins only the model and keeps the user turns
    text-only."""

    def check(self, payload):
        me = payload['resident']
        if payload['model'] != self.models[me]:
            raise ValueError(f'{me} is not seated with {payload["model"]}')
        if not all(broker.text_only(t) for t in payload['history'] if not broker.is_reply(t)):
            raise ValueError(f'The user turns of {me} may hold only text')


def plan(chosen, models, arm_names, k):
    """The trials, in the order they run: (task, model, arm, index)."""
    return [
        (task, model, arm, index)
        for model in models
        for task in chosen
        if model == HAIKU or task['sonnet']
        for arm in arm_names
        if (model == HAIKU or arm == 'control') and arms.payload(task, arm, model) is not None
        for index in range(k)
    ]


def run(everything, trials, out, cap, transport=None):
    """Run every trial not yet in out; everything is every task, for the resume check, and cap is the most the spend
    ledger may reach."""
    out.mkdir(parents=True, exist_ok=True)
    path = out / 'trials.jsonl'
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    by_id = {t['id']: t for t in everything}
    for row in rows:
        task = by_id.get(row['task'])
        sent = task and row['arm'] in arms.ARMS and arms.payload(task, row['arm'], row['model'])
        if not sent or tasks.sha256(sent, task['id']) != row['reply']['request_sha256']:
            raise ValueError(f'{path}: a {row["arm"]} trial of {row["task"]} is not what that arm sends now')
    done = {(row['task'], row['model'], row['arm'], row['index']) for row in rows}
    the_broker, seated = None, None
    with (out / 'host.jsonl').open('a') as journal, path.open('a') as written:
        for task, model, arm, index in trials:
            if (task['id'], model, arm, index) in done:
                continue
            if seated != (task['id'], model):  # one broker per task and model
                cfg = {'version': config.CONFIG_VERSION, 'seed': task['seed'], 'arm': task['arm']}
                the_broker = Broker(task['id'], cfg | {'seats': dict.fromkeys(IDS, model)}, journal, transport, cap)
                seated = task['id'], model
            reply = the_broker(arms.payload(task, arm, model))
            row = {'task': task['id'], 'model': model, 'arm': arm, 'index': index, 'reply': reply}
            written.write(json.dumps(row) + '\n')
            written.flush()
