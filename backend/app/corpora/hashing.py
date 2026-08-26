from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

HASH_SCHEMA_VERSION = 1


def canonical_json(value: Mapping[str, Any]) -> bytes:
    """Serialize JSON deterministically for research reproducibility hashes."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def corpus_content_hash(
    *,
    document_hashes: Sequence[str],
    parser_configuration: Mapping[str, Any],
    chunker_configuration: Mapping[str, Any],
    embedding_configuration: Mapping[str, Any],
) -> str:
    """Hash content and material processing settings, never IDs or timestamps."""

    payload = {
        "schema_version": HASH_SCHEMA_VERSION,
        "documents": sorted(document_hashes),
        "parser_configuration": dict(parser_configuration),
        "chunker_configuration": dict(chunker_configuration),
        "embedding_configuration": dict(embedding_configuration),
    }
    return hashlib.sha256(canonical_json(payload)).hexdigest()
