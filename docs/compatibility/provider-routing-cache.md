# Provider routing, tool-loop, and cache compatibility

Ticket #6, parent specification #1. Integration baseline: `6344605` (tickets #2–#5 merged). This report records deterministic checks and bounded, opt-in synthetic live probes. **Compatibility is partial:** Terra and Codex passed their required live tool behavior; Sol failed required sequential continuation. Codex appended-history cache reuse was not observed. The final review repair results and disposition are recorded below; the original ticket #6 validation is retained as historical evidence.

## Status definitions

- **Passed:** the named behavior was observed and validated at the stated boundary.
- **Failed required:** a required check returned an invalid result or failed to complete successfully. Later checks are not credited.
- **Unsupported optional:** a valid provider response did not provide the optional capability in this run. This does not establish that the provider never supports it.
- **Unexecuted:** no observation was made. Deterministic transport doubles do not establish live compatibility or real prompt-cache hits.

The probes preserve `all_checks_passed` as a strict aggregate and report `required_checks_passed` separately. An optional same-response multi-call limitation cannot fail otherwise valid sequential continuation. A zero-cache Responses follow-up fails its required cache check; zero-cache Codex related calls report unsupported for that run, as required by the specification. Impossible cached-input counts fail validation. Client reuse is never counted as cached input.

## Live results

| Profile and requested model | Required behavior | Optional same-response two calls | Appended-history cache | Unexecuted checks |
| --- | --- | --- | --- | --- |
| Magpie Terra, `codex/gpt-6.1-terra`, Responses, medium | Passed text, vision, two sequential declared calls, final answer without calls, Checker/Hopper structured output, public Explorer loop | Unsupported: only `click` returned | **Passed: 7,168 cached / 7,374 input tokens** on the follow-up | None in this probe |
| Magpie Sol, `codex/gpt-6.1-sol`, Responses, medium, requested priority | **Failed required:** second sequential call returned JSON-shaped text and no tool call | Unsupported: only `click` returned | Unexecuted | Final tool continuation, Checker, Hopper, Explorer, cache first/follow-up |
| Codex subscription, `gpt-6.1-sol`, medium, requested fast | Passed text, vision, two sequential declared calls, final answer without calls, Checker structured output, public Explorer loop | Passed `click` and `press_key` together | **Unsupported this run: 8,320 / 0 / 0 cached tokens**; neither later turn hit cache | Live schema-rejection injection and live multimodal tool receipts; these are deterministic checks below |
| CA, WorkBuddy, generic OpenAI, Anthropic, OpenRouter, direct Google | Unexecuted live; deterministic routing coverage below | Unexecuted | Unexecuted | All live protocol checks |

Committed synthetic observation snapshots: [Terra](provider-routing-cache/terra.json), [Sol](provider-routing-cache/sol.json), [Codex](provider-routing-cache/codex.json). These contain usage, observed tool calls, and safe request metadata, never authorization headers or credential values. The Sol second-turn response was text `{"name":"press_key","arguments":{"key":"BACK"}}`, with an empty `tool_calls` list. It was not converted into a tool call or credited as successful execution. The run stopped and recorded subsequent checks as unexecuted.

Codex full-run input token counts were **17,959 / 17,997 / 18,035**, with **8,320 / 0 / 0** cached input. A separate initial cache-only run also reported zero cached input, with input counts **17,961 / 17,999 / 18,037**. The full run's initial call did hit cache, but neither run demonstrates appended-history cache reuse. Terra's first cache call reported 3,968 cached / 7,353 input tokens; the acceptance evidence is the later appended-history count, not that initial hit.

Service tiers are requested settings. No conclusion is drawn that the provider applied them; reported served metadata remains in the snapshots. Codex does not report a served tier or an upstream HTTP endpoint through this adapter.

### Live request and safety audit

The Responses probe asserted the exact configured loopback URL `http://127.0.0.1:3425/v1/responses`, configured model, reasoning effort, requested service tier, `store=false`, and absence of `previous_response_id` before sending each POST. The snapshots retain method-independent safe request fields: path, model, reasoning, service tier, stream flag, and input item count. The Terra cache request grew from two native input items to four. Both Responses profiles used the configured OpenAI adapter; no Google request was observed. Deterministic transport tests separately reject unexpected destinations and preserve native history, encrypted reasoning inclusion, streaming, and disabled response chaining.

Codex's audit boundary is the official `codex exec` process with ChatGPT authentication, configured model/reasoning/tier, a stable empty execution directory, read-only sandbox, disabled built-ins, and temporary per-call files. Existing executable-boundary tests verify those properties and cleanup on success, error, timeout, and cancellation. This is not an independent observation of the CLI's upstream HTTP endpoint.

Authentication availability was checked using the supported `codex login status` command and a two-second connection check to the already configured Magpie loopback port. No credential files were inspected, no authentication was changed, and no device actions were performed. The probes used generated images and synthetic prompts. Declared `click` and `press_key` calls received explicitly labeled dry-run receipts without dispatching device operations. Explorer used its public `run` entry point with perception helpers denylisted and only answer submission exposed. Each measured call has a 180-second bound; CLI probe runs have a 1,200-second overall bound.

## Deterministic coverage

The combined focused suite passed **804 tests**. It covers provider profiles, factory identity, Codex process lifecycle, Google usage normalization, memory, Explorer, native-engine eligibility, grounding/tool binding, structured output, helper dispatch, and tool-result history. Transport/process doubles are synthetic and establish no real cache hits.

| Required seam | Evidence |
| --- | --- |
| Primary/fallback roles, memory, visual summaries, judges, initialization errors | `tests/unit/test_role_routing.py`: public calls and outgoing SDK transports across generic OpenAI, Anthropic, OpenRouter, Codex, Responses, CA, WorkBuddy, Google |
| Effective endpoint, credential digest identity, runtime changes, native Responses history | `tests/unit/test_model_factory.py`: public factory calls and outgoing requests; no stale endpoint/client after effective settings change |
| Native Google eligibility | `tests/unit/agents/test_native_engine_selection.py`: provider/model/key/shared-client matrix; Google-shaped non-Google models remain universal |
| Declared tools, schema rejection, structured output, final responses, no built-in execution | `tests/unit/test_codex_provider.py`: real adapter with executable double, including `test_invalid_action_arguments_rejected_before_execution`, unknown-tool rejection and `test_structured_output_validates_domain_schema` |
| Actual ARTEMIS-side helper dispatch, sequential text and multimodal receipts | New `tests/unit/test_provider_compatibility.py`: public `Explorer.run`, real OpenAI/OpenRouter adapters, two real local image crops, tool-call IDs/text receipts and returned image pixels observed at the HTTP transport; socket access forbidden; an unrelated Google key cannot redirect requests |
| Optional multi-call versus required sequential behavior | Updated `tests/unit/test_magpie_probe.py`; new `tests/unit/test_codex_probe.py` drives the entire Codex probe through an executable double returning only one optional call, then verifies both sequential calls, structured output and public Explorer completion |
| Cache reporting honesty | Probe tests reject zero/out-of-range Responses cache counts and impossible Codex counts, retain actual usage on failure, exclude first-turn-only Codex cache hits, preserve unsupported optional outcomes, and refuse to call incomplete probes fully passed |
| Provider-specific image result carriers, tool wrappers, usage | `tests/unit/tools/history`, `tests/unit/mcp/test_action_executor_helper_tools.py`, `tests/unit/test_google_usage.py`, memory and grounding suites |

TDD evidence in `/tmp/artemis-spec-1/ticket-6-evidence`: `red-reporting.log` (missing required-check summary API), `red-codex-parallel.log` (optional single call aborted the full probe), `red-codex-cache.log` (impossible cached-input count incorrectly passed), and `red-unexecuted.log` (missing optional observation incorrectly counted as all passed). Each retained behavior now passes. Cache measurement remains independent of response wording, preserving ticket #5's public related-call contract. New Explorer coverage exercises existing behavior; no production agent or provider code changed in this ticket.

## Ticket #6 repository checks (before final review repair)

The existing shared venv was reused without modification. Imports were checked to resolve this worktree (`/private/tmp/artemis-spec-1/ticket-6/artemis/__init__.py`). Commands ran from the ticket worktree with:

```sh
export PYTHONPATH="$PWD"
export UV_PROJECT_ENVIRONMENT=/Users/lin/macdata/linz/Projects/artemis-codex/.venv
export UV_NO_SYNC=1
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
make test
make lint
make typecheck
```

| Check | Integration baseline | Ticket #6 result |
| --- | --- | --- |
| `make test` | 68 failed, 2,894 passed, 7 skipped, 8 deselected | **68 failed, 2,902 passed, 7 skipped, 8 deselected**; identical failing test node IDs, zero added failures |
| `make lint` | Failed formatting of `scripts/probe_magpie_responses.py` | **Passed** formatting, Ruff, quality ratchet; owned-file formatting repaired |
| `make typecheck` | Passed, zero errors | **Passed**, zero errors/warnings/informations from core analysis |
| Focused regression command below | — | **804 passed** |

Pyright prints that this isolated checkout has no local `.venv` directory, while its executable runs from the existing shared venv. Its configured core analysis finishes successfully. No dependencies were installed or changed. The full test suite emits 87 existing warnings; the focused suite emits 60 warnings (mainly Pillow deprecations and existing enum-fixture warnings).

The unchanged full-suite failures are baseline fixture/configuration problems, not clean regression acceptance:

| Baseline failure file | Count |
| --- | ---: |
| `tests/unit/agents/flash/test_turn_index_snapshot.py` | 4 |
| `tests/unit/agents/test_flash_runner.py` | 16 |
| `tests/unit/agents/test_flash_runner_ledger.py` | 16 |
| `tests/unit/agents/test_flash_scrub_edge.py` | 14 |
| `tests/unit/agents/test_flash_step_summarizer.py` | 18 |

Of these 68 failures, 63 construct Google clients without a Google API key, four supply unresolved `Mock` providers, and one fails planner Google-key validation. No real credentials were added to make deterministic fixtures pass. These exact failures already occur at integration `6344605`, before ticket #6 edits. At ticket #6 completion the specification's all-regression-suites-pass criterion was **not met**. The final repair below resolves these fixtures. Logs: `baseline-test.log`, `final-test.log`, `baseline-lint.log`, `final-lint.log`, `baseline-typecheck.log`, `final-typecheck.log`, `focused-final.log`, all under `/tmp/artemis-spec-1/ticket-6-evidence`.

Focused command (same environment):

```sh
python -m pytest tests/unit/test_codex_provider.py tests/unit/test_codex_probe.py \
  tests/unit/test_magpie_responses.py tests/unit/test_magpie_probe.py \
  tests/unit/test_model_factory.py tests/unit/test_role_routing.py \
  tests/unit/test_provider_compatibility.py tests/unit/test_google_provider.py \
  tests/unit/test_google_usage.py tests/unit/test_memory_config.py tests/unit/memory \
  tests/unit/agents/test_native_engine_selection.py tests/unit/agents/test_explorer*.py \
  tests/unit/test_llm_grounding.py tests/unit/test_llm_structured.py \
  tests/unit/mcp/test_action_executor_helper_tools.py tests/unit/tools/history -q
```

Live commands used the same existing Python, explicit `--live`, and no fake-LLM environment:

```sh
python scripts/probe_magpie_responses.py --live --profile magpie-terra.jsonc \
  --output-dir /tmp/artemis-spec-1/ticket-6-evidence/live-terra
python scripts/probe_magpie_responses.py --live --profile magpie-sol.jsonc \
  --output-dir /tmp/artemis-spec-1/ticket-6-evidence/live-sol
python scripts/probe_codex_subscription.py --live --cache-only
python scripts/probe_codex_subscription.py --live
```

For the full Codex run, the existing `configure_local_runtime()` launcher initialized an isolated `/tmp/artemis-spec-1/ticket-6-evidence/live-codex-runtime` state directory before executing the script. The cache-only run was additionally bounded by an outer 580-second process timeout. No live checks were retried to replace a failed outcome.

## Final review disposition (#1, #5, #6)

Both supplied reviews were verified against integration tip `6111f6c`. All four findings were current and in scope; none was rejected as invalid or stale. No live probe was rerun during repair, and the committed observation snapshots are unchanged.

| Finding | Disposition and durable evidence |
| --- | --- |
| Spec P1: Codex execution identity belonged to cached model instances | Fixed. `ArtemisContext` owns a Codex scope for its async task lifecycle, which the SDK already enters per task. A context variable carries that scope through inference and inherited background tasks. CLI invocations lazily allocate one empty directory per session, independently of factory model identity. Shutdown drains/cancels background work before cleaning the directory. Standalone adapter calls retain their model-local context. `test_artemis_session_owns_codex_context_across_cached_model_configurations` observes the executable working directory across changed timeout/reasoning, two sessions sharing a cached client, nested scope restoration and session cleanup. Existing per-call cleanup and isolation checks remain green. |
| Spec P1: failed Codex probes discarded their report | Fixed. Finalization persists JSON via `--output` (default `codex-probe.json`), retains completed observations and partial cache-call usage, marks remaining checks `unexecuted`, and computes both aggregates as false on interruption. The CLI prints the saved report and exits nonzero on failure. Executable-boundary tests cover a failed second sequential tool, process failure, outer timeout, cancellation and the actual CLI failure exit/report. |
| Spec P2: published Codex cache counts contradicted the snapshot | Fixed above: full-run counts are **8,320 / 0 / 0**, matching `codex.json`. Only the first call hit cache; later-turn acceptance remains unsupported. |
| Standards P2 smell: duplicated sequential protocol conversation | Accepted and fixed. `scripts/probe_tools.py` owns the synthetic click → dry-run receipt → BACK → dry-run receipt → final-answer conversation. Both callers retain their result names and text normalization. Optional parallel-tool checks remain independent. The standards review found zero hard violations. |
| Final contract: 68 Flash fixture failures blocked regression acceptance | Fixed only in tests. Five fixture modules opt into explicit Google primary/fallback configuration, inert Google keys and socket rejection through `tests/unit/agents/conftest.py`. Existing assertions and production routing remain intact. All **82 tests** across those files pass, compared with **68 failed / 14 passed** immediately before repair. |

Red/green evidence is retained in `/tmp/artemis-spec-1/review-fixes-evidence`: `red-session.log`, `green-session.log`, `red-probe-report.log`, `green-probe-report.log`, `green-shared-probe.log`, `red-flash-fixtures.log`, and `green-flash-fixtures.log`. The session test first failed on different observed working directories. The report tests first failed because failure persistence/CLI output did not exist. These are process-boundary observations, not evidence of real cache hits.

Final required checks use the shared environment without installing dependencies; imports resolve `/private/tmp/artemis-spec-1/ticket-review-fixes/artemis/__init__.py`. The command environment is the same as above, additionally removing `GOOGLE_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPEN_ROUTER_API_KEY` and `CODEX_API_KEY` for `make test`. No real credentials are required. Required results after repair:

| Command | Result | Evidence under `/tmp/artemis-spec-1/review-fixes-evidence` |
| --- | --- | --- |
| `make test` | **2,976 passed, 7 skipped, 8 deselected**, 87 existing warnings, 95.04 seconds | `final-test.log` |
| `make lint` | **Passed**: 657 files formatted, Ruff clean, quality ratchet passed | `final-lint.log` |
| `make typecheck` | **Passed**: zero errors/warnings/informations in core analysis | `final-typecheck.log` |

The full deterministic target now meets regression acceptance. Live limitations below remain independent of that result.

## Remaining acceptance gaps

- Sol's required sequential tool serialization failed in the observed run. Deterministic transport success does not override that provider/gateway failure.
- Codex reported 8,320 cached tokens on the initial full-probe call, but zero on both appended-history calls. Later-turn cache reuse remains unsupported for these observations; the session-scope repair has not been rerun live and establishes no cache improvement.
- Live CA, WorkBuddy, Anthropic, OpenRouter, generic OpenAI and direct Google behavior is unexecuted. Their routing coverage is deterministic only.
- Multimodal helper-result continuation and invalid-schema rejection are covered deterministically; the live Explorer check only submitted a synthetic answer. No device or model-quality validation is claimed.
