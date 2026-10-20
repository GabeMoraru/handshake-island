"""The broker's spans, read from an in-memory exporter. No test calls a model or sends a span over the network."""

import pytest
import test_openai
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
from test_broker import ACT, COST, GAME, MODEL, THINK, TIMED_OUT, answering, broker_for, error, message, opening

from handshake import broker, telemetry
from handshake.game import Stop
from handshake.island import IDS


@pytest.fixture(autouse=True)
def host(tmp_path, monkeypatch):
    """The host's key in the environment, the ledger in tmp_path, and retries that do not wait."""
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')
    monkeypatch.setattr(broker, 'LEDGER', tmp_path / 'spend.jsonl')
    monkeypatch.setattr(broker.time, 'sleep', lambda seconds: None)


@pytest.fixture
def spans(monkeypatch):
    """The spans the broker ends, kept in memory."""
    exporter, provider = InMemorySpanExporter(), TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, 'tracer_provider', lambda: provider)
    return exporter


def test_a_call_is_one_client_span_naming_its_caller_usage_and_cost(spans):
    broker_for(answering(message(THINK, ACT)))(opening())
    [span] = spans.get_finished_spans()
    assert (span.name, span.kind, span.status.status_code) == (f'chat {MODEL}', SpanKind.CLIENT, StatusCode.UNSET)
    # Every attribute, so no prompt or output text rides along.
    assert dict(span.attributes) == {
        'gen_ai.operation.name': 'chat',
        'gen_ai.provider.name': 'anthropic',
        'gen_ai.request.model': MODEL,
        'gen_ai.agent.name': 'mara',
        'gen_ai.conversation.id': f'{GAME}/mara',
        'handshake_island.game_id': GAME,
        'handshake_island.round': 1,
        'handshake_island.attempt': 1,
        'gen_ai.response.model': MODEL,
        'gen_ai.response.id': 'msg_01',
        'gen_ai.response.finish_reasons': ('tool_use',),
        'gen_ai.usage.input_tokens': 4020,  # 20 uncached, 3,000 read from the cache and 1,000 written to it
        'gen_ai.usage.cache_read.input_tokens': 3000,
        'gen_ai.usage.cache_write.input_tokens': 1000,
        'gen_ai.usage.output_tokens': 800,
        'gen_ai.usage.reasoning.output_tokens': 500,
        'handshake_island.cost_usd': COST,
        'handshake_island.retries': 0,
        'handshake_island.thinking': True,
    }
    assert span.events == ()


@pytest.mark.parametrize('content', [[THINK | {'thinking': ''}, ACT], [ACT]], ids=['empty thinking', 'no thinking'])
def test_thinking_is_marked_only_when_a_thinking_block_carried_text(spans, content):
    broker_for(answering(message(*content)))(opening())
    [span] = spans.get_finished_spans()
    assert span.attributes['handshake_island.thinking'] is False


def test_an_openai_call_names_its_provider_and_counts_a_reasoning_summary_as_thinking(spans, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    api = answering(test_openai.response(test_openai.REASONING, test_openai.CALL))
    broker_for(api, seats=dict.fromkeys(IDS, test_openai.LUNA))(opening(model=test_openai.LUNA))
    [span] = spans.get_finished_spans()
    assert (span.attributes['gen_ai.provider.name'], span.attributes['handshake_island.thinking']) == ('openai', True)


@pytest.mark.parametrize('nameless', [{}, {'model': None, 'id': None}])
def test_a_reply_without_its_model_or_id_leaves_them_off_the_span(spans, nameless):
    body = {k: v for k, v in message(THINK, ACT).items() if k not in ('model', 'id')} | nameless
    broker_for(answering(body))(opening())
    [span] = spans.get_finished_spans()
    assert 'gen_ai.response.model' not in span.attributes and 'gen_ai.response.id' not in span.attributes


def test_each_retry_is_an_event_on_the_calls_one_span(spans):
    broker_for(answering(error(429), TIMED_OUT, message(THINK, ACT)))(opening())
    [span] = spans.get_finished_spans()
    assert [(e.name, dict(e.attributes)) for e in span.events] == [
        ('handshake_island.retry', {'error.type': 'RateLimitError'}),
        ('handshake_island.retry', {'error.type': 'APITimeoutError'}),
    ]
    assert span.attributes['handshake_island.retries'] == 2


def test_a_stop_ends_the_span_in_error_named_by_its_closing_reason(spans):
    with pytest.raises(Stop):
        broker_for(answering(error(529), error(500), error(503)))(opening())
    [span] = spans.get_finished_spans()
    assert (span.status.status_code, span.status.description) == (StatusCode.ERROR, 'provider_error')
    assert span.attributes['error.type'] == 'provider_error'
    # no exception event: the journal keeps the error itself
    assert [e.name for e in span.events] == ['handshake_island.retry', 'handshake_island.retry']


def test_any_other_error_is_named_by_its_type_alone(spans):
    with pytest.raises(ValueError), telemetry.span(GAME, opening()):
        raise ValueError('text a model wrote')
    [span] = spans.get_finished_spans()
    assert (span.status.description, span.attributes['error.type'], span.events) == ('ValueError', 'ValueError', ())


def test_a_reply_that_stops_the_game_still_shows_what_it_cost(spans):
    dropped = {'type': 'thinking_dropped', 'path': 'messages.2.content.0', 'reason': 'prefix_binding_mismatch'}
    with pytest.raises(Stop):
        broker_for(answering(message(THINK, ACT, transformations=[dropped])))(opening())
    [span] = spans.get_finished_spans()
    assert span.attributes['error.type'] == 'serializer_error' and span.attributes['handshake_island.cost_usd'] == COST


def test_without_an_otlp_endpoint_every_span_is_a_no_op():
    assert isinstance(telemetry.tracer_provider(), trace.NoOpTracerProvider)
    with telemetry.span(GAME, opening()) as span:
        assert not span.is_recording()


@pytest.mark.parametrize(
    ('environment', 'service'),
    [
        ({}, 'handshake-island'),
        ({'OTEL_SERVICE_NAME': 'island-a'}, 'island-a'),
        ({'OTEL_RESOURCE_ATTRIBUTES': 'service.name=island-b'}, 'island-b'),
    ],
    ids=['default', 'service name', 'resource attributes'],
)
def test_an_otlp_endpoint_exports_the_spans_under_the_service_name(monkeypatch, environment, service):
    exporter = InMemorySpanExporter()
    monkeypatch.setattr(telemetry, 'OTLPSpanExporter', lambda: exporter)
    monkeypatch.setenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://127.0.0.1:4318')
    for name in ('OTEL_SERVICE_NAME', 'OTEL_RESOURCE_ATTRIBUTES'):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    provider = telemetry.tracer_provider()
    broker_for(answering(message(THINK, ACT)))(opening())
    provider.force_flush()
    [span] = exporter.get_finished_spans()
    assert span.name == f'chat {MODEL}' and span.resource.attributes['service.name'] == service
    provider.shutdown()
