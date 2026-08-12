FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY libs/common /tmp/common
RUN pip install --no-cache-dir /tmp/common && rm -rf /tmp/common

COPY services/query-simulator/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/query-simulator/simulator ./simulator

RUN useradd --create-home --uid 10001 simulator
USER simulator

# Overridden by `make simulate`; the default is a gentle baseline so that
# `docker compose run query-simulator` does something sensible.
ENTRYPOINT ["python", "-m", "simulator"]
CMD ["--qps", "50", "--scenario", "baseline", "--duration", "60"]
