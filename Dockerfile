FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
COPY app ./app
RUN pip install --no-cache-dir uv==0.12.3 && uv sync --frozen --no-dev --no-editable
COPY alembic.ini ./
COPY alembic ./alembic
RUN useradd --create-home --uid 10001 api
USER api
EXPOSE 8000
CMD ["sh", "-c", ".venv/bin/alembic upgrade head && exec .venv/bin/uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --no-access-log --no-proxy-headers"]
