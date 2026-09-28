# CrossCheck: multi-source fact verification

CrossCheck verifies Chinese-language claims against web evidence. It combines Exa and Firecrawl for source discovery and page retrieval, then uses rules, a locally trained classifier, and an optional OpenAI-compatible model to assess each source. The app runs with mock data out of the box.

## Quick start

Requires Python 3.11 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
uvicorn crosscheck.main:app --reload
```

Open <http://127.0.0.1:8000/> for the Chinese-language verification workspace or <http://127.0.0.1:8000/docs> for the API reference. FastAPI serves the interface directly; no separate frontend server is needed.

Submit a claim in the workspace to see live progress for claim parsing, parallel search, page retrieval, and evidence assessment. Scripts can use `POST /api/v1/verifications`; the workspace uses `POST /api/v1/verifications/stream` for progress events.

## Connect search and content providers

The default mock providers work without credentials. To use live sources, set the following values in `.env`:

```dotenv
SEARCH_PROVIDERS=exa,firecrawl
CONTENT_PROVIDER=firecrawl
EXA_API_KEY=your-exa-api-key
FIRECRAWL_API_KEY=your-firecrawl-api-key
```

Search providers discover candidate URLs. Content providers retrieve page bodies for selected URLs; search snippets are not used as the final evidence. When both Exa and Firecrawl have credentials, the app retrieves content from both in parallel. It prefers the content provider selected in the workspace and falls back to the other if needed. Successful providers and failures are recorded in the evidence metadata.

You can also open **Source management** (`来源管理`) in the workspace to enable providers, set API URLs and keys, choose the content provider, and save the configuration. Changes apply to the next verification. Settings are stored in `config.local.json`, which is excluded from Git; the page displays masked API keys.

## Choose claim and evidence analyzers

Source management offers independent settings for claim parsing and evidence assessment. The default selections are an OpenAI-compatible model for claim parsing and **local model + LLM** for evidence assessment. Add primary and optional backup model credentials in the workspace. If the primary model times out, fails, or returns an unusable response, the backup is tried before rules are used. Reports record the method used and any fallback warnings.

For each retrieved page, rules first compare the claim with the content, including its subject, year, scope, and key quantities. In hybrid mode, the local classifier and the LLM independently classify the evidence as `supports`, `refutes`, or `insufficient`. Agreement is kept; disagreement becomes `insufficient` with both reasons shown for review. If one model is unavailable, the other is used; if both are unavailable, the rule result is used. Hybrid mode makes LLM requests during evidence assessment. You can select local-only, LLM-only, or rules-only assessment in the workspace.

The local classifier is a three-class linear model implemented in this project. Its features include Chinese character 2/3-grams, claim–evidence overlap, subject/time/object/action matches, number differences, and negation terms. It learns its weights through online gradient updates without pretrained embeddings or a third-party machine-learning runtime. At inference time, rule checks and a confidence threshold prevent some unsafe changes to the initial decision.

## Train the local classifier

The configured default model is the compressed CFEVER-trained file `models/evidence_relation_cfever.json.gz`. The synthetic starter dataset lives at `data/relation_training.jsonl` when generated locally; `data/` is excluded from Git. To create that starter dataset and train a baseline model:

```bash
python -m crosscheck.ml.train --create-starter-data
```

For your own labeled data, provide one JSON object per line with at least these fields:

```json
{"claim":"上海市2026年禁止所有电动自行车上路","evidence":"上海市2026年政策仅限制部分道路的电动自行车通行，并未全面禁行。","label":"refutes","group":"source-001"}
```

`label` must be `supports`, `refutes`, or `insufficient`. Give claims from the same source or event the same `group`; the training script uses groups to split training and holdout samples. Run `python -m crosscheck.ml.train --help` to see the data, model, and report path options.

Synthetic starter results do not measure accuracy on live web pages. The CFEVER model was trained on Chinese claims and Wikipedia evidence sentences; some `insufficient` examples use constructed negative evidence. See [CFEVER data and training](docs/cfever-data.md) for the import workflow, evaluation, and limitations. Use independently labeled claim–evidence pairs to assess performance in your target setting.

## Troubleshoot verification

Diagnostic events are written to `logs/crosscheck-<process-id>.jsonl`. They include request IDs, stage durations, provider and model failures, and fallback decisions. Report IDs map to log request IDs. By default, logs do not store claims, page bodies, or raw model responses.

See [diagnostics](docs/diagnostics.md) to find a verification, investigate timeouts, and temporarily enable a redacted model-response preview.

## Project layout

```text
src/crosscheck/
  api/          HTTP routes
  domain/       Data models and provider protocols
  ml/           Local classifier and training scripts
  providers/    Exa, Firecrawl, and mock adapters
  services/     Verification workflow and evidence assessment
  static/       Workspace interface
  storage/      Saved verification reports
```

See [UI design notes](DESIGN.md) for the workspace's visual system.
