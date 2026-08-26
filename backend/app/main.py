from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.adaptive.api import router as adaptive_router
from backend.app.api.corpora import router as corpora_router
from backend.app.api.indexing import router as indexing_router
from backend.app.api.jobs import router as jobs_router
from backend.app.api.pipelines import router as pipelines_router
from backend.app.api.query_runs import router as query_runs_router
from backend.app.api.routes.documents import router as documents_router
from backend.app.benchmarks.api import router as benchmarks_router
from backend.app.comparisons.api import router as comparisons_router
from backend.app.core.config import get_settings
from backend.app.core.errors import install_error_handlers
from backend.app.datasets.api import router as datasets_router
from backend.app.evaluation.api import router as evaluation_router


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="RAGScope API", version="0.1.0")
    install_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(corpora_router, prefix=settings.api_prefix)
    app.include_router(documents_router, prefix=settings.api_prefix)
    app.include_router(indexing_router, prefix=settings.api_prefix)
    app.include_router(jobs_router, prefix=settings.api_prefix)
    app.include_router(pipelines_router, prefix=settings.api_prefix)
    app.include_router(query_runs_router, prefix=settings.api_prefix)
    app.include_router(comparisons_router, prefix=settings.api_prefix)
    app.include_router(datasets_router, prefix=settings.api_prefix)
    app.include_router(benchmarks_router, prefix=settings.api_prefix)
    app.include_router(adaptive_router, prefix=settings.api_prefix)
    app.include_router(evaluation_router, prefix=settings.api_prefix)

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
