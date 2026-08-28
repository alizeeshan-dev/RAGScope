import importlib.util
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.db.models import PipelineConfiguration
from backend.app.db.session import get_db
from backend.app.pipelines.schemas import (
    PipelineConfigurationCreate,
    PipelineConfigurationRead,
)
from backend.app.pipelines.service import (
    create_pipeline_configuration,
    freeze_pipeline_configuration,
    get_pipeline_or_error,
    list_pipeline_configurations,
)

router = APIRouter(tags=["pipelines"])


class ProviderCapabilityRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    capability: str
    available: bool
    reason: str


class PipelineTemplateRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    template_id: str
    label: str
    review_required: bool = True
    configuration: dict[str, Any]


@router.get("/pipeline-configuration-templates", response_model=list[PipelineTemplateRead])
def pipeline_templates(
    router_configuration_id: UUID | None = None,
) -> list[PipelineTemplateRead]:
    base: dict[str, Any] = {
        "version": 1,
        "execution_mode": "fixed",
        "router_configuration_id": None,
        "adaptive_configuration": {},
        "lexical_configuration": {"top_k": 8, "candidate_count": 20},
        "dense_configuration": {
            "top_k": 8,
            "candidate_count": 20,
            "similarity_method": "cosine",
        },
        "fusion_configuration": {
            "method": "rrf",
            "rrf_k": 60,
            "lexical_weight": 1,
            "dense_weight": 1,
            "final_count": 8,
        },
        "reranker_configuration": {
            "enabled": False,
            "provider": "fake",
            "model": "fake-token-overlap-reranker-v1",
            "input_candidate_count": 20,
            "final_count": 8,
        },
        "query_processing_configuration": {
            "classification_enabled": True,
            "rewriting_enabled": False,
            "rewriting_strategy": "normalize-only",
        },
        "context_configuration": {
            "token_budget": 2048,
            "deduplicate": True,
            "overlap_threshold": 0.85,
        },
        "generation_configuration": {
            "provider": "fake",
            "model": "fake-generation-v1",
            "temperature": 0,
            "max_output_tokens": 512,
        },
        "citation_configuration": {
            "verification_method": "citation-existence-v1",
            "require_factual_claim_citations": True,
        },
        "prompt_versions": {"grounded_generation": {"prompt_id": "grounded-answer", "version": 1}},
    }
    definitions = (
        ("P0", "No RAG", "none", False),
        ("P1", "Lexical RAG", "lexical", False),
        ("P2", "Dense RAG", "dense", False),
        ("P3", "Hybrid RAG", "hybrid", False),
        ("P4", "Hybrid + reranking", "hybrid", True),
    )
    templates: list[PipelineTemplateRead] = []
    for template_id, label, mode, rerank in definitions:
        configuration = {**base, "name": f"{template_id} — {label}", "retrieval_mode": mode}
        configuration["reranker_configuration"] = {
            **base["reranker_configuration"], "enabled": rerank
        }
        templates.append(
            PipelineTemplateRead(
                template_id=template_id, label=label, configuration=configuration
            )
        )
    adaptive = {
        **base,
        "name": "P5 — Adaptive RAG",
        "execution_mode": "adaptive",
        "retrieval_mode": "hybrid",
        "router_configuration_id": (
            str(router_configuration_id) if router_configuration_id else None
        ),
        "adaptive_configuration": {
            "allowed_retrieval_modes": ["none", "lexical", "dense", "hybrid"],
            "allow_rewriting": True,
            "allow_reranking": True,
            "maximum_candidate_count": 200,
            "maximum_context_budget": 100000,
        },
    }
    templates.append(
        PipelineTemplateRead(
            template_id="P5", label="Adaptive RAG", configuration=adaptive
        )
    )
    return templates


@router.get("/provider-capabilities", response_model=list[ProviderCapabilityRead])
def provider_capabilities(
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[ProviderCapabilityRead]:
    """Report usable provider families without returning credentials."""

    local_reranker = importlib.util.find_spec("sentence_transformers") is not None
    return [
        ProviderCapabilityRead(
            provider="fake",
            capability="generation,embedding,reranking",
            available=True,
            reason="Deterministic local test providers are bundled.",
        ),
        ProviderCapabilityRead(
            provider="gemini",
            capability="generation,embedding",
            available=settings.gemini_api_key is not None,
            reason=(
                "Environment credential is configured."
                if settings.gemini_api_key is not None
                else "RAGSCOPE_GEMINI_API_KEY is not configured."
            ),
        ),
        ProviderCapabilityRead(
            provider="sentence_transformers_cross_encoder",
            capability="reranking",
            available=local_reranker,
            reason=(
                "Local sentence-transformers runtime is installed."
                if local_reranker
                else "Optional sentence-transformers runtime is not installed."
            ),
        ),
    ]


@router.post(
    "/pipeline-configurations",
    response_model=PipelineConfigurationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_pipeline(
    payload: PipelineConfigurationCreate,
    session: Annotated[Session, Depends(get_db)],
) -> PipelineConfiguration:
    configuration = create_pipeline_configuration(session, payload)
    session.commit()
    session.refresh(configuration)
    return configuration


@router.get("/pipeline-configurations", response_model=list[PipelineConfigurationRead])
def list_pipelines(
    session: Annotated[Session, Depends(get_db)],
) -> list[PipelineConfiguration]:
    return list_pipeline_configurations(session)


@router.get(
    "/pipeline-configurations/{configuration_id}",
    response_model=PipelineConfigurationRead,
)
def get_pipeline(
    configuration_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> PipelineConfiguration:
    return get_pipeline_or_error(session, configuration_id)


@router.post(
    "/pipeline-configurations/{configuration_id}/freeze",
    response_model=PipelineConfigurationRead,
)
def freeze_pipeline(
    configuration_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> PipelineConfiguration:
    configuration = get_pipeline_or_error(session, configuration_id)
    freeze_pipeline_configuration(session, configuration)
    session.commit()
    session.refresh(configuration)
    return configuration
