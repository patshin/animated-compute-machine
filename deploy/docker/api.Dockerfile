FROM python:3.12.14-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH=/opt/agent-trace-venv/bin:$PATH \
    PYTHONPATH=/app/apps/api

RUN groupadd --gid 10001 agenttrace \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/agenttrace \
       --shell /usr/sbin/nologin agenttrace \
    && python -m venv /opt/agent-trace-venv

WORKDIR /app
COPY requirements.production.lock ./requirements.production.lock
RUN python -m pip install --requirement requirements.production.lock

COPY apps/api ./apps/api
COPY config ./config
COPY contracts ./contracts
COPY evals ./evals
COPY scenarios ./scenarios
COPY sample-scenarios ./sample-scenarios
RUN mkdir /app/.local && chown agenttrace:agenttrace /app/.local

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=4s --start-period=20s --retries=4 \
  CMD ["python", "-c", "import json,urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/ready', timeout=3))['status'] == 'ready'"]

CMD ["python", "-m", "uvicorn", "agent_trace_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-keep-alive", "5", "--timeout-graceful-shutdown", "30", "--no-server-header"]
