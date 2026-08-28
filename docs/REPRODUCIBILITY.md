# Reproducibility and fixture workflow

This guide separates an engineering fixture run from a reportable research
experiment. A successful test suite demonstrates implementation behavior; it is
not a scientific result.

## 1. Record the environment

From the repository root:

```powershell
git rev-parse HEAD
git status --short
python --version
node --version
docker version
docker compose version
```

Keep the commit identifier in every Experiment. If the working tree is dirty,
archive the diff or commit the intended code before claiming reproducibility.

## 2. Configure local secrets and pricing

```powershell
Copy-Item .env.example .env
```

Edit only the local root `.env`. Do not commit it. Fake providers require no key.
Optional Gemini generation/extraction uses:

```dotenv
RAGSCOPE_GEMINI_API_KEY=<local secret>
RAGSCOPE_GEMINI_MODEL=gemini-2.5-flash
```

Do not paste the key into source, prompts, benchmark notes, experiment snapshots,
frontend forms, logs, or chat.

Pricing uses currency units per **one million tokens**:

```dotenv
RAGSCOPE_GENERATION_INPUT_PRICE_PER_MILLION=<decimal or blank>
RAGSCOPE_GENERATION_OUTPUT_PRICE_PER_MILLION=<decimal or blank>
RAGSCOPE_EMBEDDING_INPUT_PRICE_PER_MILLION=<decimal or blank>
RAGSCOPE_EXPERIMENT_COST_LIMIT=<optional decimal ceiling or blank>
```

Blank means unavailable, not zero. Record the pricing source, currency, and date
outside `.env` in the study notes.

## 3A. Start the Docker stack

```powershell
docker compose up --build
```

Verify:

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-WebRequest http://localhost:3000 -UseBasicParsing | Select-Object StatusCode
```

Expected health payload: `{"status":"ok"}`. PostgreSQL and artifacts persist in
named Docker volumes. Do not remove those volumes if they contain a study run that
has not been exported/backed up.

The stack includes a dedicated `worker` service. Confirm that it is healthy/running
before enqueueing ingestion, evaluation, experiment, export, or figure jobs:

```powershell
docker compose ps
docker compose logs worker --tail 50
docker compose exec db psql -U ragscope -d ragscope -c "select status, count(*) from jobs group by status order by status;"
```

No host `psql` installation is required.

## 3C. One-command deterministic PostgreSQL fixture

After migrations, the idempotent fixture command exercises the full stored
research lifecycle without a provider key or network access:

```powershell
$env:RAGSCOPE_DATABASE_URL = "postgresql+psycopg://ragscope:ragscope@localhost:5433/ragscope"
$env:RAGSCOPE_ARTIFACT_ROOT = (New-Item -ItemType Directory -Force ".\var\fixture-artifacts").FullName
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m backend.app.fixture_workflow
```

Expected result: one frozen synthetic corpus, P0–P5 fake configurations, one
frozen five-question benchmark, 30 completed matrix cells, versioned raw/derived
exports, and eight SVG/JSON figures plus a manifest. Run the command twice and
confirm it prints the same experiment identifier. The Markdown inputs under
`benchmark/fixtures/synthetic/` intentionally include a table, missing fact,
contradiction, distractor, and prompt-injection text. The tracked
`northstar-field-report.pdf` adds a one-page table/provenance parser fixture and
can be regenerated with `scripts/fixtures/generate_synthetic_pdf.py` using the
documented reportlab environment. Representative real PDFs remain a separate
parser-validation set.

## 3B. Start a local SQLite development stack

Use this path for deterministic fixture development, not PostgreSQL-specific
performance/ranking claims.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,pdf]"
Copy-Item .env.example .env
$env:RAGSCOPE_DATABASE_URL = "sqlite:///./ragscope.db"
$env:RAGSCOPE_ARTIFACT_ROOT = "./var/artifacts"
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\uvicorn.exe backend.app.main:app --reload
```

Second terminal:

```powershell
Set-Location frontend
npm install
npm run dev
```

## 4. Prepare the development document set

Place openly usable representative PDFs in the Git-ignored directory:

```text
benchmark/fixtures/representative/
```

The current Git-ignored directory contains candidate development PDFs. Treat
multilingual content, image annotations, activity sensors, reading comprehension,
web-scale image/text data, restricted clinical data, tables, and incomplete
metadata as selection goals—not facts inferred from filenames. Confirm actual
coverage in Document Inspector. Do not copy the PDFs into Git or call them a
frozen corpus until they have been uploaded, parsed, indexed, reviewed, and frozen
inside RAGScope.

## 5. Build a frozen corpus through the UI

1. Open `http://localhost:3000` and create a corpus and draft version.
2. Upload a small subset of representative PDFs.
3. In each Document Inspector, parse and inspect warnings, elements, pages, tables,
   and provenance.
4. From the corpus/version screen, generate structure-aware chunks. Generate the
   fixed-token alternative too if the study compares chunkers.
5. Build lexical/dense indexes and inspect indexed/failure/integrity counts.
6. Perform a known-term lexical and dense fixture search via the API docs if needed.
7. Freeze only the ready version and record its ID/content hash.
8. Confirm attempted post-freeze document/chunk/index mutation is rejected.

For command-line upload after creating a draft version in the UI:

```powershell
$api = "http://localhost:8000/api/v1"
$versionId = "<draft corpus-version UUID>"
$pdf = (Resolve-Path "benchmark/fixtures/representative/<paper>.pdf").Path
curl.exe -sS -X POST "$api/corpus-versions/$versionId/documents" -F "file=@$pdf;type=application/pdf"
```

Use the returned document UUID to parse:

```powershell
$documentId = "<document UUID>"
Invoke-RestMethod -Method Post "$api/documents/$documentId/parse"
```

Chunk and index the version:

```powershell
$chunkBody = @{ strategy="structure-aware"; target_tokens=384; overlap_tokens=32; include_section_titles=$true; preserve_tables=$true } | ConvertTo-Json
Invoke-RestMethod -Method Post -ContentType "application/json" -Body $chunkBody "$api/corpus-versions/$versionId/chunk"
Invoke-RestMethod -Method Post "$api/corpus-versions/$versionId/index"
Invoke-RestMethod "$api/corpus-versions/$versionId/index-status"
```

Do not freeze until the version is ready and required index integrity checks pass.
These POSTs normally return an operation receipt. Poll its `status_url`, or append
`?wait=true&timeout_seconds=60` for a bounded local compatibility wait.

## 6. Create frozen pipelines

Open `http://localhost:3000/runtime`. Create, inspect, and freeze at least:

- lexical;
- dense;
- hybrid;
- hybrid plus reranking; and
- optionally no-RAG and adaptive conditions.

Hold prompt/generation settings constant unless they are the intended independent
variable. Fake generation/embedding/reranking are suitable for deterministic
engineering checks; label them accurately and do not use fake semantic quality as
a research conclusion.

## 7. Create a reviewed development benchmark

Open `http://localhost:3000/benchmarks`:

1. create a benchmark and draft version linked to the frozen corpus;
2. author at least five pilot questions across more than one question type;
3. select exact element/chunk passages in Document Inspector;
4. create distinct acceptable alternative evidence sets where warranted;
5. include answerable, partially answerable, and unanswerable/false-premise cases;
6. review leakage warnings and human annotations; and
7. freeze the version only when every required question is reviewed and valid.

Human evidence must resolve to the same corpus version. Model suggestions do not
count as human ground truth.

## 8. Audit single runs before batch execution

Use `http://localhost:3000/laboratory` for one question per condition. Inspect:

- query classification/rewrite and fixed/adaptive route reason;
- stage order and status;
- lexical/dense/fused/reranked rank history;
- evidence loss and context exclusions;
- exact context artifact;
- structured answer, claims, and source-resolved citations;
- metric method/version/inputs and missingness; and
- automatic failure attribution versus any human correction.

Use Pipeline Comparison for the same original question and corpus; never compare
columns that silently differ on either input.

## 9. Run and resume a pilot experiment

Open `http://localhost:3000/experiments`:

1. select one frozen corpus, one frozen benchmark, and at least two frozen pipelines;
2. enter the exact Git commit, repetitions, research question, and stop policy;
3. create the draft;
4. calculate the maximum cost estimate and document missing pricing;
5. freeze the experiment;
6. start the matrix; and
7. if interrupted, use Resume and confirm completed valid cells are unchanged.

The minimum specification pilot is five questions × three pipelines. This pilot is
for runner/metric/cost validation and must not be presented as the main study.

## 10. Analyze and export

Open `/results/<experiment UUID>`. Confirm every chart exposes sample size,
denominator/missing/excluded counts, filters, and contributing QueryRun drill-down.
Export tidy CSV and versioned JSON from the Experiment Manager/Results Dashboard.

The equivalent API smoke checks are:

```powershell
$api = "http://localhost:8000/api/v1"
$experimentId = "<frozen experiment UUID>"
$exportRoot = (New-Item -ItemType Directory -Force "var/exports/$experimentId").FullName
Invoke-RestMethod "$api/experiments/$experimentId"
Invoke-RestMethod "$api/experiments/$experimentId/results?offset=0&limit=100"
Invoke-WebRequest "$api/experiments/$experimentId/export?format=csv&kind=raw" -OutFile "$exportRoot/experiment-$experimentId-runs.csv"
Invoke-WebRequest "$api/experiments/$experimentId/export?format=csv&kind=aggregate" -OutFile "$exportRoot/experiment-$experimentId-aggregates.csv"
Invoke-WebRequest "$api/experiments/$experimentId/export?format=json" -OutFile "$exportRoot/experiment-$experimentId.json"
Get-ChildItem $exportRoot | Select-Object Name,Length
```

If any route is absent from `http://localhost:8000/openapi.json`, stop and record
the integration failure. A visible frontend download link alone is not proof that
an export was produced.

After download, verify the files are non-empty and retain them with the commit,
experiment ID/configuration hash, and study notes. Never fill report tables from a
screenshot when a deterministic export is available.

## 11. Generate reproducible figures

The research-figure generator accepts only the versioned analysis JSON export and
an explicit exact-metric selector configuration. It creates all eight required
SVG figures, machine-readable JSON specifications, and a hash-bearing manifest
beneath the Git-ignored generated root. It emits visibly labelled placeholders
when required results are absent rather than inventing values.

```powershell
Set-Location D:\RAGScope
$experimentId = "<frozen experiment UUID>"
.\.venv\Scripts\python.exe scripts/research/generate_figures.py `
  --input "var/exports/$experimentId/experiment-$experimentId.json" `
  --config scripts/research/figure_config.example.json `
  --output artifacts/research/figures
```

Before a main report, copy the example configuration to a study-specific JSON
file and pin the exact metric identities and denominator policies actually
preregistered. Do not edit generated SVG values by hand. Preserve the source
export, configuration, figure JSON/SVG files, and `manifest.json` together.

## 12. Engineering verification

Backend:

```powershell
Set-Location D:\RAGScope
.\.venv\Scripts\python.exe -m pytest backend/tests -q -p no:cacheprovider
.\.venv\Scripts\ruff.exe check backend
.\.venv\Scripts\mypy.exe backend/app
.\.venv\Scripts\alembic.exe upgrade head
```

Frontend:

```powershell
Set-Location frontend
npm run typecheck
npm run lint
npm run build
npm run e2e
```

Focused deterministic experiment/analysis checks:

```powershell
Set-Location D:\RAGScope
.\.venv\Scripts\python.exe -m pytest backend/tests/test_experiments.py backend/tests/test_analysis_aggregation_exports.py -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pytest backend/tests/test_research_figures.py -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pytest backend/tests/test_evaluation_retrieval.py backend/tests/test_evaluation_context_operational.py backend/tests/test_evaluation_generation_citation.py backend/tests/test_failure_attribution.py backend/tests/test_adaptive_router.py -q -p no:cacheprovider
```

## 13. Reproduction acceptance checklist

- [ ] Clean migration reaches `head`.
- [ ] Fake-provider test suite runs without network/API credentials.
- [ ] Frozen corpus/benchmark/pipeline/experiment IDs and hashes are recorded.
- [ ] Run matrix count equals questions × pipelines × repetitions.
- [ ] Resume creates no duplicate completed valid cells.
- [ ] Traces/context/citations resolve to stored artifacts and sources.
- [ ] Every aggregate accounts for denominator, missing, and infrastructure-excluded runs.
- [ ] CSV/JSON exports link back to contributing QueryRun IDs.
- [ ] Figure manifest hashes match the source export and selector configuration.
- [ ] Report values are copied/recomputed from stored exports.
- [ ] Main-results placeholders remain until a completed main experiment exists.

## 14. Post-experiment human review

Open `/experiments/<experiment-id>/review` after a deterministic or real experiment
reaches a terminal state. The paginated queue lists runs still missing the six primary
human judgments. Each row opens Query Laboratory with previous/next review navigation,
the frozen benchmark reference, exact trace/evidence, automatic metrics, and a separate
human evaluation form.

Scores are normalized to 0–1 and persisted as `human-review.v1` observations. Saving
never overwrites an automatic metric. Recalculation runs through the PostgreSQL worker,
appends input-hashed derived metrics, and leaves completed raw QueryRun, retrieval,
context, trace, claim, citation, and provider artifacts immutable. Regenerate experiment
exports/figures only after the human queue is complete, and retain the new derived
artifact versions alongside the earlier ones.
