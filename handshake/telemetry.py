"""OpenTelemetry spans for the broker's model calls, under the GenAI semantic conventions.

The broker opens one client span per call. It names the game, the resident and the model, why the reply stopped, its
tokens and list-price cost, and each retry, never prompt or output text: the journal keeps that. Names the
conventions do not define use handshake_island.*. The conventions are in development; the names follow
open-telemetry/semantic-conventions-genai as read on 2026-10-18.

Spans leave the process only when the standard OTLP environment variables name an endpoint; otherwise every span is a
no-op. Only the host imports this module: the broker is the only egress, so it is the only place a span starts.
"""

import contextlib
import functools
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, StatusCode

from handshake import providers
from handshake.game import Stop


@functools.cache
def tracer_provider():
    """The tracer provider: one exporting over OTLP/HTTP when the environment names an endpoint, otherwise a no-op.
    The exporter reads the other OTEL_EXPORTER_OTLP_* variables itself, and the SDK flushes the batch when the process
    exits."""
    if not (os.environ.get('OTEL_EXPORTER_OTLP_ENDPOINT') or os.environ.get('OTEL_EXPORTER_OTLP_TRACES_ENDPOINT')):
        return trace.NoOpTracerProvider()
    resource = Resource.create()
    # The SDK's fallback name, when neither OTEL_SERVICE_NAME nor OTEL_RESOURCE_ATTRIBUTES names the service.
    if resource.attributes['service.name'].startswith('unknown_service'):
        resource = resource.merge(Resource({'service.name': 'handshake-island'}))
    exporting = TracerProvider(resource=resource)
    exporting.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    return exporting


@contextlib.contextmanager
def span(game_id, payload):
    """The span of one model call: "chat <model>", for one resident's conversation in one game."""
    resident, model = payload['resident'], payload['model']
    attributes = {
        'gen_ai.operation.name': 'chat',
        'gen_ai.provider.name': providers.ADAPTERS[model].provider,
        'gen_ai.request.model': model,
        'gen_ai.agent.name': resident,
        'gen_ai.conversation.id': f'{game_id}/{resident}',
        'handshake_island.game_id': game_id,
        'handshake_island.round': payload['round'],
        'handshake_island.attempt': payload['attempt'],
    }
    tracer = tracer_provider().get_tracer(__name__)
    # The journal records each error in full; the span names it, since an error's message may quote the model.
    with tracer.start_as_current_span(
        f'chat {model}',
        kind=SpanKind.CLIENT,
        attributes=attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as opened:
        try:
            yield opened
        except Exception as error:
            kind = str(error) if isinstance(error, Stop) else type(error).__name__
            opened.set_attribute('error.type', kind)
            opened.set_status(StatusCode.ERROR, kind)
            raise


def record(model, body, reply):
    """Set the reply's attributes on the call's span."""
    usage = reply['usage']
    # The conventions count cached tokens as input; the replies' input_tokens leave them out.
    cached = usage['cache_read_tokens'] + usage['cache_write_tokens']
    # A local server may leave the model or the id out or null, and a span attribute cannot be None.
    named = {f'gen_ai.response.{k}': body[k] for k in ('model', 'id') if body.get(k) is not None}
    trace.get_current_span().set_attributes(
        named
        | {
            'gen_ai.response.finish_reasons': [reply['stop']],
            'gen_ai.usage.input_tokens': usage['input_tokens'] + cached,
            'gen_ai.usage.cache_read.input_tokens': usage['cache_read_tokens'],
            'gen_ai.usage.cache_write.input_tokens': usage['cache_write_tokens'],
            'gen_ai.usage.output_tokens': usage['output_tokens'],
            'gen_ai.usage.reasoning.output_tokens': usage['thinking_tokens'],
            'handshake_island.cost_usd': reply['cost_usd'],
            'handshake_island.retries': reply['retries'],
            'handshake_island.thinking': bool(providers.ADAPTERS[model].reasoning(reply['native'])),
        }
    )


def retry(error):
    """A retry on the open span, named by the error's type."""
    trace.get_current_span().add_event('handshake_island.retry', {'error.type': type(error).__name__})
