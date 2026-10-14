# Test coverage and regression-risk review

Reviewed after the `core/` regrouping. The baseline suite had 399 passing tests
and 0 skips. This review added tests and exposed one real bug (see
[Bugs found](#bugs-found)). The maintainers fixed that bug afterwards, and the
suite now passes with 549 tests and 0 skips.

## Methodology and limits

- Line and branch coverage measured with the ephemeral
  `uv run --with coverage coverage run --branch --source=src/wechat_bridge -m pytest -q`
  (the command is not recorded in `pyproject.toml` or `uv.lock`, and the review
added no dependency).
- Coverage says a line executed, not that an assertion would catch a
  regression. Each new test below was written to assert an observable
  contract; three were mutation-checked by hand (see "Mutation checks").
- `setup/lldb_capture.py` runs under Apple's system Python in a subprocess, so
  in-process coverage under-reports it (10%); the lifecycle tests exercise it
  out of process. `setup/system.py` (41%) wraps live process control that
  tests must not touch.
- The review used synthetic fixtures. It touched no WeChat process, real key or live archive.

## Coverage by package (line+branch, before -> after)

| Package | Before | After | Notes |
| --- | --- | --- | --- |
| `core/` | 83-100% (`config` 83%) | `config` 98%, others 100% | Remaining: one `read_private_json` partial branch |
| `source/` | 85-100% | unchanged | `reader.py` 85%: WAL/short-read error branches |
| `adapters/` | 84-94% | unchanged | `wechat_4_1.py` 89%: malformed-row branches |
| `attachments/` | 82-100% | `resolver` 91% -> 94% | `images.py` 83%, `mapping.py` 82% are the weakest |
| `archive/` | 86-99% | unchanged | `watcher.py` 86%: OSError paths in `key_state` and lock |
| `search/` | 88-100% | `cursor`, `tokenize` 100%, `repository` 92% -> 97% | |
| `mcp/` | 75-89% | unchanged in-process | `service.py` 75%: `status()` partial branches |
| `setup/` | 10-100% | unchanged | see limits above |
| `cli.py`, `proof.py` | 79%, 85% | unchanged | `cli.py` serve/verify-keys branches |
| Total | 83% | 84% | |

## Risk register

| Risk | Likelihood | Impact | Current protection | Gap |
| --- | --- | --- | --- | --- |
| Repo-root depth wrong after `core/` move (`parents[3]`), letting private state live in the checkout | Medium (the move was recent; `parents[2]` before) | High (private data in repo) | Was none: existing test passed `repo_root=` explicitly | Closed by `test_default_repo_root_is_the_checkout_after_core_move` |
| Upward import creeping in, or a foundation module moving back out of `core/` | Medium | Medium | Prose in `docs/architecture.md` only | Closed by `tests/unit/test_layering.py` |
| Build 270135 acceptance (a build bump that is "schema-identical") | Medium on each WeChat update | High (wrong columns -> wrong history) | `test_schema.py` build allowlist, exact-string match, `test_proof` installed-build cases | None found; allowlist is exact-match and tested for neighbours 270134/270136 |
| Serialized watchers / backoff | Medium | Medium (CPU churn or silent stall) | `test_watcher.py`: lock admits one holder, takeover, hourly ceiling, transient not backed off | `key_state` OSError and lock `check_private_fd` failure branches untested (fail-closed by design) |
| Cursor tampering / cross-query reuse | Low | High (cross-generation or cross-filter reads) | `test_pagination.py` binding tests | Closed at unit level by `test_cursor.py` (signature, key, binding, payload shape, size) |
| Query tokenization drift (NFKC/casefold/offset) | Medium | Medium (silent search misses) | `test_search_index.py` parametrized round-trips | Closed for `normalize`, `terms`, `match_expression`, `source_offset` by `test_tokenize.py` |
| Public limits drift (`core/limits`) | Low | Medium (contract change) | Indirect via MCP tests | Closed by `test_core_contracts.py` pinning values and `status()` echo |
| Config malformed / permissive state accepted | Low | High | Partial (4 tests) | Closed by additions to `test_config.py`; one real bug found and fixed (below) |
| Unmapped/stale media delivered as original | Low | High (no-substitution invariant) | `test_attachment_io.py`, mapping tests | Provenance gating, FIFO/dir, digest case, pinned identity and size-limit validation now covered by `test_resolver_gates.py` |
| MCP error code or argument bound drift | Medium | High (public contract) | `test_mcp.py` samples | Wire-level code table and bounds pinned by `test_mcp_contract.py` |
| Repository arg validation drift (time ranges, enums, generation) | Medium | Medium | Partial | Closed by `test_query_validation.py` |
| Image decode/XOR/V2 material paths | Medium | Medium | `test_images.py`, `test_image_material.py` | `images.py` 83%: several malformed-header branches uncovered (left open) |

## Tests added

| File | Tests | Invariant locked |
| --- | --- | --- |
| `tests/unit/test_layering.py` | 5 | Module-level import direction matches the documented layering; function-local upward imports are only the 4 documented exceptions; `core/` imports nothing above it; no foundation module lives at package top level; `lldb_capture.py` imports nothing from the package |
| `tests/unit/test_config.py` (+16) | 22 total | Default repo root is the checkout; malformed `config.json` (version, relative/non-str paths, missing keys, symlinked account) rejected; permissive dir/fd rejected; non-object/oversize/garbage/symlink private JSON rejected; atomic write leaves prior value and no temp on failure, error carries no cause; state lock is exclusive; account-selection conflicts |
| `tests/unit/test_core_contracts.py` | 17 | Limits equal 50/200/100/256 and are what `status()` reports; `require_int_range` rejects bool/float/str/None and honours inclusive bounds; `compact_json` shape; `BridgeError` public dict is code-only; clock is tz-aware |
| `tests/unit/test_cursor.py` | 20 | Round trip; binding sensitivity; tamper/truncate/non-str/oversize rejected as `invalid_cursor` with no chained cause; wrong key/binding; validly signed but ill-shaped payloads (version, generation 0/bool/str, `last` length) rejected |
| `tests/unit/test_tokenize.py` | 23 | Width/case/whitespace folding, idempotence, unigram/bigram terms incl. astral code points, single-char fallback, query text cannot inject FTS syntax, `source_offset` mapping across collapsed whitespace and combining marks |
| `tests/integration/test_mcp_contract.py` | 16 | Over-bound/ill-typed arguments return exactly `{"error":"invalid_arguments"}` with no echo; missing records and bad cursors/representations use stable codes; unexpected exceptions map to `internal_error` without leaking the message; decoded request for a file is explicit `unsupported_representation` |
| `tests/integration/test_resolver_gates.py` | 16 | Unknown/conflicting provenance never opens a file; not-local stays explicit; directory and FIFO finals refused; digest compare is case-insensitive but mismatch refused; pinned `file_identity` enforced; source byte limit validated and enforced |
| `tests/integration/test_query_validation.py` | 37 | Limits, ISO times (naive, reversed, oversized), type/kind/representation/availability enums, id shapes, context bounds (inclusive), generation bounds, expired generation, direct conversation is not a group |

## Mutation checks (performed by hand, then reverted)

- `core/errors.py` gained `from ..mcp import server` and `source/native.py` a
  function-local `from ..search import index`: `test_layering.py` failed 3 tests.
- `core/config.py` `parents[3]` -> `parents[2]`: `test_default_repo_root_is_the_checkout_after_core_move` failed.
- Reverting restored the original bytes; a stale `.pyc` (same size, same mtime
  second) kept the mutant alive for a while. Clear `__pycache__` when mutating by hand.

## Known gaps left open

- `setup/system.py`, `lldb_capture.py`: live process/debugger control; needs a
  fake process layer, and `CONTRIBUTING.md` asks for an issue first for `setup/`
  changes.
- `attachments/images.py` (83%) and `mapping.py` (82%): malformed-header
  branches; worth a fuzz-style table test, but requires understanding the V1/V2
  container format beyond the current fixtures (`tests/support/images.py`).
- `cli.py` serve/verify-keys/watch branches (79%): need subprocess fixtures
  already used in `test_capture_*`; low regression risk.
- Concurrency: no test races two live processes on `config.write_private_json`.
- Performance bounds (`benchmarks/search.py`) are exercised by
  `test_benchmark.py` only for correctness, not latency.
- Query read budget (8 MiB) overflow path (`repository.py` 107/115) needs an
  oversized synthetic record; not added.

## Bugs found

1. **Fixed.** `core/config.py` `load_config` compared `data["version"] != 1`,
   which accepts JSON `true` because `True == 1`. The project validates external
   input with strict types. Severity was low, because the state file is
   owner-only and self-written. The review locked the behavior with
   `tests/unit/test_config.py::test_load_config_rejects_boolean_version`, first
   marked `xfail(strict=True)`. The fix checks
   `type(data["version"]) is not int or ... != 1`, and the test now passes
   without the marker.

## Repository hygiene observed (not test gaps)

- A local `state-never-created/` directory could appear in the checkout if the
  repo-root guard regresses. The test removes it in `finally`.
