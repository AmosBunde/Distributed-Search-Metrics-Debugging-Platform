FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# The shared library first: it changes far less often than service code, so the
# layer stays cached across most rebuilds.
COPY libs/common /tmp/common
RUN pip install --no-cache-dir /tmp/common && rm -rf /tmp/common

COPY services/metrics-engine/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/metrics-engine/engine ./engine

RUN useradd --create-home --uid 10001 engine
USER engine

EXPOSE 8002
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8002/health').read()"

CMD ["uvicorn", "engine.main:app", "--host", "0.0.0.0", "--port", "8002"]
