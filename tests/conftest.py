"""The fake container engine the sandbox tests pin in place of Docker, and spans kept in the process."""

import json
import shlex
import sys
from pathlib import Path

import pytest
from fake_engine import IMAGE

from handshake import sandbox, telemetry

FAKE_ENGINE = Path(__file__).resolve().parent / 'fake_engine.py'


@pytest.fixture(autouse=True)
def no_export(monkeypatch):
    """No test sends a span over the network, whatever the environment names."""
    monkeypatch.delenv('OTEL_EXPORTER_OTLP_ENDPOINT', raising=False)
    monkeypatch.delenv('OTEL_EXPORTER_OTLP_TRACES_ENDPOINT', raising=False)
    telemetry.tracer_provider.cache_clear()


@pytest.fixture
def engine(tmp_path, monkeypatch):
    """Point the sandbox at a runtime directory in tmp_path and return a function that pins fake_engine.py, in the
    mode it names, as a Docker executable there."""
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    monkeypatch.setattr(sandbox, 'RUNTIME', runtime)

    def pin(mode='worker', **changes):
        executable = runtime / 'docker'
        executable.write_text(
            f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(FAKE_ENGINE))} {mode} "$@"\n'
        )
        executable.chmod(0o755)
        pinned = {
            'backend': 'docker',
            'executable': str(executable),
            'image': IMAGE,
            'digest': IMAGE,
            'source_hash': sandbox.source_hash(),
            **changes,
        }
        (runtime / 'image.json').write_text(json.dumps(pinned))
        return executable

    return pin
