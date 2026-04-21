# syntax=docker/dockerfile:1.7
# Whoop MCP server — stdio transport, ephemeral per-session container.

ARG PYTHON_IMAGE=docker.io/library/python:3.12-slim@sha256:e31013b9573989b2dc2f0cb688044c9e650c2721dd52c54d0fd3c669d3548bb6

FROM ${PYTHON_IMAGE} AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
COPY pyproject.toml requirements.lock ./

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --require-hashes --no-deps -r requirements.lock


FROM ${PYTHON_IMAGE}

RUN groupadd --system --gid 1000 whoop \
 && useradd --system --uid 1000 --gid whoop --home /app --shell /sbin/nologin whoop

COPY --from=build /opt/venv /opt/venv
COPY --chown=whoop:whoop src /app/src

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
USER whoop

ENTRYPOINT ["python", "src/whoop_server.py"]
