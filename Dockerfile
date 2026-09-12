FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY call_agent/ call_agent/

RUN pip install --no-cache-dir . \
    && mkdir -p /app/artifacts

ENV CALL_AGENT_ARTIFACTS_DIR=/app/artifacts

EXPOSE 8000

CMD ["uvicorn", "call_agent.server:app", "--host", "0.0.0.0", "--port", "8000"]
