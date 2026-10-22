# Second Act: FastAPI backend + static frontend, one container.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SECOND_ACT_DATA_DIR=/app/data \
    SECOND_ACT_WEB_DIR=/app/web \
    SECOND_ACT_LOG_DIR=/tmp/second-act/logs \
    SECOND_ACT_CACHE_DIR=/app/precomputed

WORKDIR /app

# Dependencies first for layer caching.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install .

# Match data, frontend and precomputed (already verified) online outputs.
COPY data ./data
COPY web ./web
COPY precomputed ./precomputed

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=4)"

CMD ["uvicorn", "second_act.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
