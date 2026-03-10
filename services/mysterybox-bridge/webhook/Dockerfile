FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home /app app

ARG MYSTERYBOX_BRIDGE_WEBHOOK_VERSION=0.0.0.dev0

COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --upgrade pip \
    && SETUPTOOLS_SCM_PRETEND_VERSION_FOR_MYSTERYBOX_BRIDGE_WEBHOOK="${MYSTERYBOX_BRIDGE_WEBHOOK_VERSION}" \
       python -m pip install .

USER app

EXPOSE 8080

CMD ["gunicorn", "--workers=2", "--bind=0.0.0.0:8080", "mysterybox_bridge.app:create_app", "--log-level=info"]
