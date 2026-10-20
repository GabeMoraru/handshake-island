# python:3.12-slim, named in full so no engine guesses its registry, and pinned to its multi-platform index so
# every engine and architecture builds on the same base.
FROM docker.io/library/python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
# The worker needs only the standard library, so the image installs no packages.
COPY handshake /app/handshake
USER 1000:1000
ENTRYPOINT ["python", "-m", "handshake.worker"]
