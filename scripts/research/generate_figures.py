"""Generate all required RAGScope research figures from a versioned JSON export."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from backend.app.research import (  # noqa: E402
    generate_required_figures,
    load_analysis_export,
    write_figure_bundle,
)
from backend.app.research.io import load_figure_configuration  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Versioned analysis JSON export")
    parser.add_argument("--config", type=Path, required=True, help="Exact figure selector JSON")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/research/figures"),
        help="Generated directory under artifacts/research",
    )
    arguments = parser.parse_args()
    workspace = WORKSPACE_ROOT
    generated_root = workspace / "artifacts" / "research"
    output = arguments.output
    if not output.is_absolute():
        output = workspace / output
    dataset = load_analysis_export(arguments.input)
    configuration = load_figure_configuration(arguments.config)
    artifacts = generate_required_figures(dataset.runs, configuration)
    written = write_figure_bundle(
        artifacts,
        output,
        generated_root=generated_root,
        source_export_sha256=hashlib.sha256(arguments.input.read_bytes()).hexdigest(),
        configuration_sha256=hashlib.sha256(arguments.config.read_bytes()).hexdigest(),
    )
    print(f"Generated {len(artifacts)} figures ({len(written)} files) in {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
