"""A stand-in for Docker in the tests: python fake_engine.py MODE ARGS...

`version` and `image inspect` answer as Docker would for one image, IMAGE; any other command but `run` (build, rm)
does nothing and succeeds. `run` plays the real worker on the directory mounted at /data, with an isolation probe
that passes (mode `worker`) or fails (`unsafe`), or says nothing at all (`silent`), or asks for one model call and
never reads the reply (`deaf`). No container is involved.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from handshake import worker  # noqa: E402

IMAGE = 'sha256:' + 'a' * 64


def main(mode, command, *args):
    if command == 'version':
        print('28.5.1')
    elif command == 'image':
        print(json.dumps([{'Id': IMAGE}]))
    elif command == 'run' and mode == 'silent':
        time.sleep(60)
    elif command == 'run' and mode == 'deaf':
        worker.emit({'kind': 'ready', 'probe': {'verified': True}})
        worker.emit({'kind': 'model_request', 'id': '1', 'payload': {'resident': 'mara', 'round': 1}})
        time.sleep(60)
    elif command == 'run':
        mount = args[args.index('--mount') + 1]
        worker.DATA = Path(mount.split(',')[1].removeprefix('source='))
        worker.isolation_probe = lambda: {'verified': mode == 'worker', 'checks': {'fake_engine': mode == 'worker'}}
        worker.main()


if __name__ == '__main__':
    main(*sys.argv[1:])
