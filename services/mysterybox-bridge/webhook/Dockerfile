FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home /app app

COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --upgrade pip \
    && python -m pip install .

USER app

EXPOSE 8080

CMD ["gunicorn", "--workers=2", "--bind=0.0.0.0:8080", "mysterybox_bridge.app:create_app", "--log-level=info"]
