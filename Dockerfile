# Fallback execution path for the organizers.
# Build:  docker build -t <user>/gridwise-llm:v1 .
# Run:    docker run --rm -p 8000:8000 -e LLM_API_KEY=... <user>/gridwise-llm:v1
#
# No secrets are baked in: every credential arrives at runtime via -e / --env-file.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY tests/data ./tests/data

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://127.0.0.1:${PORT}/health || exit 1

# 0.0.0.0 bind is required: the judge calls this from outside the container.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 2"]
