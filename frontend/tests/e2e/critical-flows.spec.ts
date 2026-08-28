import { expect, test, type APIRequestContext } from "@playwright/test";
import path from "node:path";

const apiRoot = process.env.PLAYWRIGHT_API_URL ?? "http://localhost:8000/api/v1";

async function json<T>(request: APIRequestContext, path: string): Promise<T> {
  const response = await request.get(`${apiRoot}${path}`);
  expect(response.ok(), `${path}: ${response.status()} ${await response.text()}`).toBeTruthy();
  return response.json() as Promise<T>;
}

async function fixture(request: APIRequestContext) {
  const corpora = await json<Array<{ id: string; name: string; versions: Array<{ id: string; status: string }> }>>(request, "/corpora");
  const corpus = corpora.find((value) => value.name === "RAGScope deterministic fixture corpus");
  expect(corpus).toBeTruthy();
  const pipelines = await json<Array<{ id: string; name: string; frozen_at: string | null }>>(request, "/pipeline-configurations");
  const experiments = await json<{ items: Array<{ id: string; name: string }> }>(request, "/experiments?limit=100");
  const experiment = experiments.items.find((value) => value.name === "RAGScope deterministic fixture experiment");
  expect(experiment).toBeTruthy();
  const versions = corpus!.versions;
  const version = versions.find((value) => value.status === "ready");
  expect(version).toBeTruthy();
  const documents = await json<Array<{ id: string; title: string }>>(request, `/corpus-versions/${version!.id}/documents?limit=50`);
  return { corpus: corpus!, version: version!, pipelines, experiment: experiment!, documents };
}

test("fixture corpus, source focus, and frozen benchmark are inspectable", async ({ page, request }) => {
  const data = await fixture(request);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Scientific corpora" })).toBeVisible();
  await expect(page.getByText(data.corpus.name)).toBeVisible();

  const elements = await json<Array<{ id: string; text: string }>>(request, `/documents/${data.documents[0].id}/elements?limit=50`);
  const selected = elements.find((value) => value.text.includes("10,000")) ?? elements[0];
  await page.goto(`/documents/${data.documents[0].id}?element=${selected.id}`);
  await expect(page.locator(`#element-${selected.id}`)).toHaveAttribute("aria-current", "true");
  await expect(page.locator(".element-detail mark")).toContainText("10,000");

  const benchmarks = await json<Array<{ id: string; name: string }>>(request, "/benchmarks");
  const benchmark = benchmarks.find((value) => value.name === "RAGScope deterministic fixture benchmark");
  const versions = await json<Array<{ id: string; status: string }>>(request, `/benchmarks/${benchmark!.id}/versions`);
  const frozen = versions.find((value) => value.status === "frozen");
  await page.goto(`/benchmarks/versions/${frozen!.id}`);
  await expect(page.getByText("Read-only benchmark snapshot")).toBeVisible();
  await expect(page.getByRole("heading", { name: "5 annotations" })).toBeVisible();
});

test("pipeline contracts create, freeze, run, compare, and trace without hidden state", async ({ page, request }) => {
  const data = await fixture(request);
  const templates = await json<Array<{ template_id: string; configuration: Record<string, unknown> }>>(request, "/pipeline-configuration-templates");
  const template = templates.find((value) => value.template_id === "P3")!;
  const create = await request.post(`${apiRoot}/pipeline-configurations`, {
    data: { ...template.configuration, name: `Playwright P3 ${Date.now()}` },
  });
  expect(create.status()).toBe(201);
  const pipeline = await create.json() as { id: string };
  expect((await request.post(`${apiRoot}/pipeline-configurations/${pipeline.id}/freeze`)).ok()).toBeTruthy();

  const runResponse = await request.post(`${apiRoot}/query-runs`, {
    data: {
      corpus_version_id: data.version.id,
      pipeline_configuration_id: pipeline.id,
      query_text: "How many images are in the Atlas test split?",
    },
  });
  expect(runResponse.ok()).toBeTruthy();
  const run = await runResponse.json() as { id: string };
  await page.goto(`/laboratory/${run.id}`);
  await expect(page.getByRole("heading", { name: "Stages in actual execution order" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Retrieval → fusion → reranking → context" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Generator evidence package" })).toBeVisible();
  await page.getByRole("button", { name: "Reveal exact context" }).click();
  await expect(page.locator(".exact-context pre")).toContainText("BEGIN_UNTRUSTED_SOURCE");

  const fixed = data.pipelines.filter((value) => value.frozen_at && /P[1234] Fixture/.test(value.name)).slice(0, 2);
  const comparison = await request.post(`${apiRoot}/query-comparisons`, {
    data: {
      corpus_version_id: data.version.id,
      question: "Compare the licenses of Atlas and RiverSound.",
      pipeline_configuration_ids: fixed.map((value) => value.id),
    },
  });
  expect(comparison.ok()).toBeTruthy();
  const comparisonBody = await comparison.json() as { id: string };
  await page.goto(`/comparisons?comparison=${comparisonBody.id}`);
  await expect(page.getByText("Pipeline Comparison").first()).toBeVisible();
  await expect(page.getByText("Meaningful configuration differences")).toBeVisible();
});

test("dataset extraction and review surface remains evidence-first", async ({ page, request }) => {
  const data = await fixture(request);
  const response = await request.post(`${apiRoot}/documents/${data.documents[0].id}/extract-datasets?wait=true`, {
    data: { strategy: "baseline", provider: "fake" },
  });
  expect(response.ok()).toBeTruthy();
  const records = await json<Array<{ id: string }>>(request, `/dataset-records?corpus_version_id=${data.version.id}`);
  expect(records.length).toBeGreaterThan(0);
  await page.goto(`/datasets/${records[0].id}`);
  await expect(page.getByText("Original model output").first()).toBeVisible();
  await expect(page.getByText("Current reviewed value").first()).toBeVisible();
  await expect(page.getByText(/Correction history/).first()).toBeVisible();
});

test("dashboard uses complete server-side populations and drills into runs", async ({ page, request }) => {
  const data = await fixture(request);
  await page.goto(`/results/${data.experiment.id}`);
  await expect(page.getByRole("heading", { name: "RAGScope deterministic fixture experiment" })).toBeVisible();
  for (const title of [
    "Retrieval Recall@k by pipeline",
    "Answer correctness by pipeline",
    "Citation support rate by pipeline",
    "Cost versus answer correctness",
    "Latency distribution",
    "Failure-stage distribution",
    "Performance by question type",
    "Required-evidence survival",
  ]) await expect(page.getByRole("heading", { name: title })).toBeVisible();
  await expect(page.getByText(/calculated server-side from all 30 filtered runs/i)).toBeVisible();
  await expect(page.getByRole("link", { name: /→/ }).first()).toHaveAttribute("href", /laboratory/);
});

test("human review queue preserves labels separately and supports run navigation", async ({ page, request }) => {
  const data = await fixture(request);
  await page.goto(`/experiments/${data.experiment.id}/review`);
  await expect(page.getByRole("heading", { name: "Runs missing required human labels" })).toBeVisible();
  const reviewLink = page.getByRole("link", { name: "Inspect evidence and label →" }).first();
  await expect(reviewLink).toBeVisible();
  await reviewLink.click();
  await expect(page.getByRole("heading", { name: "Human evaluation review" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Human review navigation" })).toContainText("Review");
  for (const label of [
    "Answer correctness",
    "Answer completeness",
    "Appropriate abstention",
    "False-premise recognition",
    "Claim support rate",
    "Citation precision",
  ]) await page.getByLabel(label).fill("1");
  await page.getByLabel("Reviewer label").fill("playwright-reviewer");
  await page.getByLabel("Human evaluation reviewer note").fill("Reviewed against the frozen benchmark evidence.");
  await page.getByRole("button", { name: "Preserve human labels" }).click();
  await expect(page.getByText("6 human labels preserved separately from automatic judgments.")).toBeVisible();
  await expect(page.getByText("human-review.v1").first()).toBeVisible();
});

test("corpus authoring uploads, parses, chunks, indexes, and freezes a source snapshot", async ({ page }) => {
  const nonce = Date.now();
  const corpusName = `Playwright corpus ${nonce}`;
  const versionLabel = `e2e-${nonce}`;
  const sourceTitle = `Playwright Atlas ${nonce}`;

  await page.goto("/");
  await page.getByLabel("Name").fill(corpusName);
  await page.getByLabel("Domain").fill("Synthetic browser verification");
  await page.getByLabel("Description").fill("Disposable local corpus used to verify the full authoring contract.");
  await page.getByRole("button", { name: "Create corpus" }).click();
  await page.getByRole("link", { name: new RegExp(corpusName) }).click();

  await page.getByLabel("Version label").fill(versionLabel);
  await page.locator('select[name="parser_id"]').selectOption("markdown-parser");
  await page.locator('select[name="chunk_strategy"]').selectOption("fixed");
  await page.getByLabel("Preprocessing version").fill("hashed-bow-v1");
  await page.getByRole("button", { name: "Create draft version" }).click();
  await page.getByRole("link", { name: new RegExp(versionLabel) }).click();

  await page.getByLabel("Scientific file").setInputFiles(
    path.resolve(process.cwd(), "../benchmark/fixtures/synthetic/atlas.md"),
  );
  await page.getByLabel("Title override").fill(sourceTitle);
  await page.getByRole("button", { name: "Preserve & upload" }).click();
  await page.getByRole("link", { name: new RegExp(sourceTitle) }).click();

  await page.getByRole("button", { name: "Parse document" }).click();
  await expect(page.getByRole("navigation", { name: "Parsed elements" }).getByRole("button").first()).toBeVisible();
  await page.getByRole("link", { name: "← Corpus version" }).click();

  await page.getByRole("button", { name: "Fixed chunks" }).click();
  await expect(page.locator("article.chunk").first()).toBeVisible();
  await page.getByRole("button", { name: "Structure chunks" }).click();
  await expect(page.getByText(/both strategies retained/i)).toBeVisible();
  await page.getByRole("button", { name: "Build indexes" }).click();
  await expect(page.getByRole("button", { name: "Freeze version" })).toBeEnabled();
  await page.getByRole("button", { name: "Freeze version" }).click();
  await expect(page.getByText("This snapshot is immutable.")).toBeVisible();
});

test("human-authored unanswerable benchmark freezes without fabricated evidence", async ({ page, request }) => {
  const data = await fixture(request);
  const nonce = Date.now();
  const benchmarkName = `Playwright unanswerable benchmark ${nonce}`;
  const questionText = `Which fictional moon base was reported by Atlas in test ${nonce}?`;

  await page.goto("/benchmarks");
  await page.getByRole("heading", { name: "New benchmark" }).locator("..").getByLabel("Name").fill(benchmarkName);
  await page.getByRole("heading", { name: "New benchmark" }).locator("..").getByLabel("Description").fill(
    "Disposable human-ground-truth workflow verification.",
  );
  await page.getByRole("button", { name: "Create benchmark" }).click();
  await page.getByLabel("Benchmark").selectOption({ label: benchmarkName });
  await page.getByLabel("Corpus version").selectOption(data.version.id);
  await page.getByLabel("Notes").fill("Synthetic Playwright annotation only.");
  await page.getByRole("button", { name: "Create draft version" }).click();

  await page.getByLabel("Question").fill(questionText);
  await page.getByLabel("Type").selectOption("false_premise");
  await page.getByLabel("Difficulty").selectOption("medium");
  await page.getByLabel("Answerable from this corpus").uncheck();
  await page.getByLabel("Why unanswerable?").fill("The frozen fixture corpus contains no moon base claim; the premise is false.");
  await page.getByLabel("Annotation notes").fill("Human-authored synthetic negative case.");
  await page.getByRole("button", { name: "Create and select evidence" }).click();

  await page.getByLabel("Human review state").selectOption("reviewed");
  await page.getByRole("button", { name: "Save human annotation" }).click();
  await expect(page.getByText("No evidence is required for an unanswerable question.")).toBeVisible();
  await page.getByRole("link", { name: /← Benchmark version/ }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Freeze version" }).click();
  await expect(page.getByText("Read-only benchmark snapshot")).toBeVisible();
  await expect(page.getByText(questionText)).toBeVisible();
});
