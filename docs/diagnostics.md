# Diagnose verification failures and model timeouts

The app writes one JSON event per line to `logs/crosscheck-<process-id>.jsonl`. Restarts and automatic reloads create a new process log. Logging cannot recover model responses from requests made before it was enabled.

## Find a verification in the logs

A report ID matches the `request_id` in log events. Hover over the report ID in the workspace to see the full value. Failed requests also show a diagnostic ID; the `X-Request-ID` HTTP response header provides the same value.

Find the most recently modified log files:

```bash
ls -lt logs/
```

Search by a full ID or the first eight characters shown in the report:

```bash
read -r diagnostic_id
rg -i -- "$diagnostic_id" logs/
```

Find model failures and rule fallbacks:

```bash
rg '"event": "(llm_request_failed|llm_attempt_failed|llm_rules_fallback|evidence_model_attempt_failed|evidence_model_rules_fallback)"' logs/
```

| Event | What it records |
| --- | --- |
| `http_request_started` / `http_request_finished` | HTTP status, total request duration, and whether the response completed. An SSE status of 200 does not mean verification succeeded. |
| `verification_started` / `verification_completed` / `verification_failed` | Total verification duration and final claim and evidence analysis methods. |
| `verification_progress` / `stage_finished` | Current stage and elapsed time between progress updates. |
| `llm_request_started` | Model name and role, endpoint host, effective timeout, input length, and whether proxy environment variables exist. |
| `llm_http_response` / `llm_response_shape` | HTTP status, response time and size, upstream request ID, finish reason, content type and length, and available token usage. |
| `llm_request_failed` | Timeouts, HTTP errors, and JSON errors; format failures distinguish empty, plain-text, and JSON-like content. |
| `llm_attempt_succeeded` / `llm_attempt_failed` | Whether an individual model completed analysis and whether a backup was used. Field validation can fail even after JSON parsing succeeds. |
| `llm_rules_fallback` | No model was configured or every model failed. |
| `evidence_model_attempt_started` / `evidence_model_attempt_succeeded` / `evidence_model_attempt_failed` | Outcome and duration of the local classifier or another configured evidence analyzer. |
| `evidence_model_rules_fallback` | Evidence analysis fell back to rules. |
| `provider_http_started` / `provider_http_response` | Effective timeout, HTTP status, and time spent waiting for provider response headers. |
| `provider_succeeded` / `provider_failed` / `content_selected` | Search result count, body length, failure type, and selected content provider. |
| `report_saved` / `report_save_failed` | Whether the report was saved and how long it took. |

Each model HTTP call also has its own `attempt_id`. Parallel fetches are distinguished by `provider` and `page_id`. Timestamps use UTC and durations use milliseconds. A `ReadTimeout` alone does not identify whether the delay came from a model queue, the network, or a proxy. The presence of proxy environment variables does not prove that a request used a proxy.

If the primary model fails and the backup succeeds, the logs retain the failed attempt while the workspace shows the final success. A fallback warning appears in the workspace only when all models fail.

## Inspect a model response that is not valid JSON

By default, logs do not record user claims, page bodies, prompts, raw model responses, request headers, or upstream error bodies. They record response length, content fingerprint, finish reason, and exception location without storing arbitrary raw exception text.

If those fields are insufficient, set the following value in the project's `.env`:

```dotenv
LOG_LLM_RESPONSE_PREVIEW=true
```

Restart the service and retry the verification. When JSON extraction fails, `llm_request_failed.response_preview` saves up to 2,000 characters of model output, and `preview_truncated` indicates whether it was shortened. Configured API keys and common credential formats are replaced with `[REDACTED]`. The preview may still contain business content repeated by the model, so inspect logs before sharing them. Set the option back to `false` and restart after troubleshooting.

This option does not save complete responses or model reasoning content, and it does not change model calls, retries, timeouts, or primary/backup order.

## Log files and retention

- Each process has one active file. At about 5 MB, it rotates through at most three backups (`.1`, `.2`, `.3`).
- Logs from old processes remain after automatic reload. The roughly 20 MB per-process limit is not a limit on the entire directory; remove old process logs periodically if needed.
- Only the current user can read or write the files. The default `logs/` directory is in `.gitignore`.
- Set `LOG_DIRECTORY` in `.env` to use another writable directory, then restart the service.

Implementation: [diagnostics.py](../src/crosscheck/diagnostics.py). Tests use simulated models and a temporary database to cover concurrent request correlation, HTTP/SSE report IDs, primary/backup failover, timeouts, redaction, and file rotation.
