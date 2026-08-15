FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    SENTINEL_HOST=0.0.0.0 \
    SENTINEL_PORT=8080 \
    SENTINEL_DB_PATH=/app/data/thermal-sentinel.db

WORKDIR /app

RUN useradd --create-home --uid 10001 sentinel

COPY --chown=sentinel:sentinel src ./src
COPY --chown=sentinel:sentinel frontend ./frontend
COPY --chown=sentinel:sentinel data ./data
COPY --chown=sentinel:sentinel run.py ./run.py

USER sentinel
EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=2s --start-period=3s --retries=3 \
  CMD python -c "import json,urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=1))['status'] == 'ok'"

CMD ["python", "run.py"]
