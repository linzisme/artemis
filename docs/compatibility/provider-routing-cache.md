# Provider routing, tool-loop, and cache compatibility

Ticket #6, parent specification #1. Integration baseline: `6344605` (tickets #2–#5 merged). This report records deterministic checks and bounded, opt-in synthetic live probes. **Compatibility is partial:** Terra and Codex passed their required live tool behavior; Sol failed required sequential continuation. Codex cache reuse was not observed. The required full test target still has 68 baseline failures.

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
| Codex subscription, `gpt-6.1-sol`, medium, requested fast | Passed text, vision, two sequential declared calls, final answer without calls, Checker structured output, public Explorer loop | Passed `click` and `press_key` together | **Unsupported this run: 0 / 0 / 0 cached tokens**, including both later turns | Live schema-rejection injection and live multimodal tool receipts; these are deterministic checks below |
| CA, WorkBuddy, generic OpenAI, Anthropic, OpenRouter, direct Google | Unexecuted live; deterministic routing coverage below | Unexecuted | Unexecuted | All live protocol checks |

Committed synthetic observation snapshots: [Terra](provider-routing-cache/terra.json), [Sol](provider-routing-cache/sol.json), [Codex](provider-routing-cache/codex.json). These contain usage, observed tool calls, and safe request metadata, never authorization headers or credential values. The Sol second-turn response was text `{"name":"press_key","arguments":{"key":"BACK"}}`, with an empty `tool_calls` list. It was not converted into a tool call or credited as successful execution. The run stopped and recorded subsequent checks as unexecuted.

Codex full-run input token counts were **17,959 / 17,997 / 18,035**, with **0 / 0 / 0** cached input. A separate initial cache-only run also reported zero cached input, with input counts **17,961 / 17,999 / 18,037**. Neither run demonstrates real cache reuse. Terra's first cache call reported 3,968 cached / 7,353 input tokens; the acceptance evidence is the later appended-history count, not that initial hit.

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

## Required repository checks

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

Of these 68 failures, 63 construct Google clients without a Google API key, four supply unresolved `Mock` providers, and one fails planner Google-key validation. No real credentials were added to make deterministic fixtures pass. These exact failures already occur at integration `6344605`, before ticket #6 edits. Consequently the specification's all-regression-suites-pass criterion is **not met** in this environment; follow-up fixture repair is needed. Logs: `baseline-test.log`, `final-test.log`, `baseline-lint.log`, `final-lint.log`, `baseline-typecheck.log`, `final-typecheck.log`, `focused-final.log`, all under `/tmp/artemis-spec-1/ticket-6-evidence`.

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

## Remaining acceptance gaps

- Sol's required sequential tool serialization failed in the observed run. Deterministic transport success does not override that provider/gateway failure.
- Codex produced no genuine cache hits despite stable related-call execution context. Reported unsupported for these runs; no claim of cache improvement.
- Full deterministic regression acceptance is blocked by the 68 pre-existing Flash fixture/configuration failures.
- Live CA, WorkBuddy, Anthropic, OpenRouter, generic OpenAI and direct Google behavior is unexecuted. Their routing coverage is deterministic only.
- Multimodal helper-result continuation and invalid-schema rejection are covered deterministically; the live Explorer check only submitted a synthetic answer. No device or model-quality validation is claimed.
