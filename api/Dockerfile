FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependency layer first: application edits should not re-resolve the
# dependency tree on every rebuild.
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
      "fastapi>=0.115" "uvicorn[standard]>=0.32" "sqlalchemy>=2.0.36" \
      "psycopg[binary]>=3.2" "pgvector>=0.3.6" "alembic>=1.14" \
      "pydantic>=2.9" "pydantic-settings>=2.6" "httpx>=0.27" "typer>=0.15"

COPY . .
RUN pip install --no-cache-dir --no-deps -e .

CMD ["alembic", "upgrade", "head"]
