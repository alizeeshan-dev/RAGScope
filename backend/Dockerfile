FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY backend/app/__init__.py ./backend/app/__init__.py
ARG RAGSCOPE_INSTALL_RERANKER=false
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu 'torch>=2.2,<3'
RUN if [ "$RAGSCOPE_INSTALL_RERANKER" = "true" ]; then \
      pip install --no-cache-dir '.[pdf,reranker]'; \
    else \
      pip install --no-cache-dir '.[pdf]'; \
    fi
COPY backend ./backend
COPY benchmark/fixtures/synthetic ./benchmark/fixtures/synthetic
CMD ["sh", "-c", "alembic upgrade head && uvicorn backend.app.main:app --host 0.0.0.0 --port 8000"]
