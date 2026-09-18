FROM python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    LLM_PROVIDER=gemini \
    LLM_MODEL=gemini-3.1-flash-lite \
    LLM_FALLBACK_PROVIDER=openai_compatible \
    LLM_FALLBACK_MODEL=openrouter/free \
    LLM_FALLBACK_BASE_URL=https://openrouter.ai/api/v1

WORKDIR /app

COPY requirements.txt .
RUN --mount=type=bind,source=deploy/wheels,target=/wheels \
    if ls /wheels/*.whl >/dev/null 2>&1; then \
      pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt; \
    else \
      pip install --no-cache-dir --timeout=120 --retries=5 -r requirements.txt; \
    fi

RUN groupadd --gid 10001 gridwise \
 && useradd --uid 10001 --gid gridwise --no-create-home --shell /usr/sbin/nologin gridwise
COPY --chown=10001:10001 app ./app
USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import json,os,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health',timeout=3); assert r.status == 200 and json.load(r).get('status') == 'ok'"

# 0.0.0.0 bind is required: the judge calls this from outside the container.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"${PORT}\" --workers 1"]
