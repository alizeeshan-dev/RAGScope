from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def canonical_configuration_hash(configuration: Mapping[str, Any]) -> str:
    """Hash a JSON-compatible index configuration without incidental values."""

    serialized = json.dumps(
        configuration,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def lexical_configuration() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "postgresql_method": "postgresql_fts_ts_rank_cd",
        "portable_method": "tfidf_cosine_v1",
        "tokenization": "unicode_word_casefold_v1",
    }


def dense_configuration(
    provider_id: str, model_id: str, dimension: int, preprocessing: str
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "provider": provider_id,
        "model": model_id,
        "dimension": dimension,
        "preprocessing_version": preprocessing,
        "similarity": "cosine",
    }
