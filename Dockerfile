FROM python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534

# Only measured configuration is baked in, and never a credential.
#
# Primary  OpenRouter nex-agi/nex-n2.5-mini:free - 6/6 trap notes correct at a
#          4.89 s mean through our own prompt and guardrails.
# Fallback Google gemini-3.5-flash              - 6/6 correct at 7.06 s.
#
# They are deliberately different VENDORS. Earlier configurations used two
# Gemini models on one key, which is not insurance: one spent quota, one
# revocation or one billing stop took both paths down together.
#
# Rejected by measurement, not by taste: gemini-2.5-flash returns 404 "no
# longer available to new users" on new keys; the -flash-lite variants return
# [13, 14, 15] for a 1 PM-3 PM window and fail the end-exclusive rule;
# deepseek-v4-flash:free is accurate but averages 16.5 s, past our per-call
# timeout.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    LLM_PROVIDER=openai_compatible \
    LLM_MODEL=nex-agi/nex-n2.5-mini:free \
    LLM_BASE_URL=https://openrouter.ai/api/v1 \
    LLM_FALLBACK_PROVIDER=gemini \
    LLM_FALLBACK_MODEL=gemini-3.5-flash \
    LLM_TIMEOUT_S=10 \
    LLM_MAX_RETRIES=0 \
    REQUEST_BUDGET_S=25

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
