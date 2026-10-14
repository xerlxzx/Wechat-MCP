# Architecture review

Reviewed on 14 October 2026, after the six foundation modules moved into
`src/wechat_bridge/core/`. This is a point-in-time review. The living
description of record is [architecture.md](architecture.md). The review modified
and read no source, test or private data.

## 1. Scope and method

- **Read:** `docs/architecture.md`, `CONTRIBUTING.md`, and the modules
  that carry boundaries: `core/config.py`, `archive/{importer,store,watcher,reconcile}.py`,
  `search/{index,repository}.py`, `mcp/{server,service}.py`, `cli.py`,
  `attachments/cache.py`, `source/discovery.py`, `adapters/wechat_4_1.py` (open and
  select paths), and `proof.py` (adapter access only).
- **Import graph:** derived with a throwaway `ast` script (run with `python3 -I`,
  kept outside the repo) that lists every `import`/`from` of `wechat_bridge`
  including function-local ones, resolving relative imports. 152 edges; local
  (function-body) imports are marked in the table below.
- **Pattern greps:** raw file/lock/SQLite/subprocess calls outside `core/config.py`;
  every `except` clause; network/socket use (none found); cross-module use of
  underscore names.
- **Not done:** no tests were run, no runtime behavior was measured, and the
  `tests/` tree was read by name and import frequency only. LLDB capture
  internals and the image decoders were not reviewed in depth (the security
  review covers those).

Confidence labels: **[observed]** read in code; **[inferred]** conclusion from
adjacent evidence.

## 2. Current architecture

```
                     cli.py (lazy-imports each subsystem per command)
                       │
          proof.py ────┼──────────────────────────────┐
             │         ▼                              │
             │      mcp/server ─▶ mcp/service ──▶ cli.doctor   (local, upward)
             │         │              │
             │         ▼              ▼
             │   archive/watcher   search/repository ─▶ search/cursor, tokenize
             │         │              │       │
             │         ▼              ▼       └─▶ archive/importer.archive_status
             │   archive/importer ◀──┴── search/index ◀─ archive/store (local)
             │      │   │  └▶ archive/store ──(local)──▶ search/index
             │      │   └───▶ archive/reconcile
             ▼      ▼
         adapters/wechat_4_1 ──(local)──▶ attachments/mapping ─▶ attachments/resolver ─▶ images
             │   ▲  identity, schema, messages ⇄ replies, wire, models        │
             ▼   └──────── (attachments.mapping imports adapters.identity/wire)
          source/{discovery, keys, reader, native}
             │
             ▼
          core/{config, errors, limits, validate, serialize, clock}
                 ▲
          setup/{prepare, capture, recovery, lock, system}  (imports core + source only)
```

Layer rank used for the checks below: core(0) → source(1) → adapters(2) →
attachments(3) → archive ⇄ search(4) → mcp(5) → cli/proof(6); setup is a
sibling of adapters above source. **[observed]**

Data flow **[observed]**: encrypted DBs → `source.discovery` (inventory, registry
write under `state_lock`) → `source.keys.verify_keys` (saved keys, native
SQLCipher open) → `adapters.wechat_4_1.open_adapter` (schema inspection and
whitelist, `adapter-schemas.json` write) → `archive.importer.refresh_archive`
(drives adapter iterators, writes 200-record batches into a `building`
generation, indexes FTS rows in the same transaction as each batch, compares
source fingerprint before/after, then one `publish` transaction flips
`archive_state.active_generation`) → `search.repository` (generation-pinned,
2 s progress-handler deadline, HMAC-style cursors) → `mcp.service` / `mcp.server`
(strict argument middleware, bounded wire size, safe error codes).

## 3. Boundary and layering findings

Every import that is upward, lateral across tiers, function-local, or
otherwise not covered by a documented exception. "Doc" = listed in
`docs/architecture.md` "Known exceptions".

| # | Edge | Kind | Doc | Justified? |
| - | --- | --- | --- | --- |
| 1 | `archive/importer.py:58` → `search.index.ensure_index` | local, archive→search | yes | Yes: index is built inside the generation transaction. |
| 2 | `archive/store.py:125` → `search.index.index_records` | local, archive→search | yes | Yes, same reason. Runtime flag is `PRAGMA user_version == 2`. |
| 3 | `search/index.py:5` → `archive.store.KINDS` | module-level, search→archive | yes (⇄) | Yes. This plus 1 and 2 is the only real cycle (local imports break it at load time). |
| 4 | `adapters/wechat_4_1.py:354` → `attachments.mapping.attachment_refs` | local, **upward** | yes | Weak. `attachments.mapping` imports `adapters.identity`/`wire` back, so adapters ⇄ attachments is a true cycle. Only two callers need it (`importer.py:78`, `proof.py:196`), both above both packages and able to call `attachments.mapping` directly. See R3. |
| 5 | `mcp/service.py:43` → `cli.doctor` | local, **upward** to top-level | yes | Weak. `doctor` has no CLI dependency; it belongs below. See R2. |
| 6 | `search/repository.py:10`, `mcp/service.py:4` → `archive.importer.archive_status` | module-level | **no** | Layer-legal but a cohesion defect: the read-only status helper lives in the write-path module, so every query-path import pulls `importer` → `adapters.wechat_4_1` → `source.*` → `core`. See R1. |
| 7 | `adapters/messages.py:125` ⇄ `adapters/replies.py:5` (`parse_xml`) | local, intra-package cycle | **no** | Cosmetic but undocumented. `parse_xml` sits in `messages` only because `replies` needs it. |
| 8 | `setup/{capture,prepare,recovery}.py` → `setup.system.SystemBackend` | local, intra-package | no | Fine; defers subprocess machinery for injected backends. |
| 9 | `setup/capture.py:11` → `setup.recovery._recover_setup` | underscore name across modules | no | Smell; `recovery.py:15` already wraps it as `recover_setup`. |
| 10 | `archive/importer.py:77` → `adapter._message_tables`; `proof.py:90,97,98,117,235` → `adapter._open`, `._resolve_reply`, `._message_tables`, `.identities` | private-attribute access across packages | no | Real surface leak. The adapter's de-facto interface is wider than its public methods. See R4. |
| 11 | `mcp/server.py:13` → `archive.watcher` | module-level | n/a | Allowed by rank. The protocol layer owns the watcher lifecycle policy (thread, join timeout 8 s, swallow `BridgeError`). |
| 12 | `mcp/service.py:12` → `source.keys.load_manifest`; `search/repository.py:9` → `adapters.identity.load_identity_key` | skips tiers | no | Acceptable (read-only helper), but it means `search` depends on the adapter package for a key loader. |

Result: **the documented layering holds at module level; there are no
module-level cycles** **[observed]**. `core` imports only `core.errors`;
`source` imports only `core` and itself; `setup` imports only `core` and
`source`. The three documented exceptions are accurate and complete for the
cross-package function-local imports; findings 6, 7, 9, 10 are undocumented
and 6 and 10 are the ones that matter.

No stale references to the pre-move paths (`wechat_bridge.config` etc.) remain
in `src/`, `tests/`, `docs/` or `pyproject.toml` **[observed]**. `core/config.py`'s
`parents[3]` repo-root guard is correct for the new depth (core → wechat_bridge
→ src → repo) **[observed]**; in an installed wheel it points somewhere
arbitrary, so `state_inside_repository` is only meaningful in a source checkout
**[inferred]**.

## 4. Coupling and cohesion

| Package | Cohesion | Coupling notes |
| --- | --- | --- |
| `core/` | Good, but `config.py` mixes three things: private-state I/O primitives, `Config`/`create_config`, and WeChat-specific constants (`DEFAULT_ACCOUNTS`, `select_account`, `DEFAULT_LIBRARY` probed at import). | Leaf. Every package imports it; that is the intent. |
| `source/` | Good. Four modules, one-directional. `discover()` both inventories and persists the registry under `state_lock`. | Imports only `core`. |
| `adapters/` | `wechat_4_1.py` (355 lines) holds build gating (`select_adapter`), the open sequence (`open_adapter`: version check, discovery, key verification, schema inspection, schema-file write) and the adapter class. `open_adapter` is not a cheap open: it runs a full `verify_keys` and writes `adapter-schemas.json` every call. | Hard-wired into four call sites (`importer.py:7`, `watcher.py:7`, `cli.py:99`, `proof.py:12`) as the default of an `adapter_factory` parameter (a good test seam). |
| `attachments/` | Good: mapping, resolver, images, cache are separate. `cache.py` re-implements an atomic write rather than using a `core` helper. | Cycle with `adapters` (finding 4). |
| `archive/` | `importer.py` is three things: the lock, the refresh orchestration (~100-line `refresh_archive`), and the read-side `archive_status`/`_public_status`. `store.py` mixes DDL, generation lifecycle, record writes and `status()`. | Calls `adapter._message_tables`; coupled to `search.index` by design. |
| `search/` | Good. `cursor`, `tokenize`, `index`, `repository` are small and testable. `repository._result` calls `archive_status` (below). | Depends on `archive.importer` for status. |
| `mcp/` | `service.py` is thin; `server.py` is wire plumbing plus lifespan/watcher. | `status()` reaches `cli`, `source.keys`, and reads `proofs/` directly (`_acceptance`). |
| `setup/` | Self-contained; injectable backend. | Only `core` and `source`. |
| `proof.py` / `cli.py` | `proof.py` (268 lines) contains selection I/O, adapter-internal replay and reporting; it is the main consumer of adapter internals. `cli.py::main` is one long if/elif chain with lazy imports (deliberate; keeps `serve` start fast and native-free). | Top of the graph; acceptable. |

God-modules: none in the harmful sense (largest file is 355 lines). The two
files that most deserve a future split are `archive/importer.py` (R1) and
`adapters/wechat_4_1.py` (only when a second build family arrives).

## 5. Public vs internal surfaces

- **External contract (frozen by `CONTRIBUTING.md`):** nine MCP tools in
  `mcp/server.py` (names, argument sets enforced by the `strict_arguments`
  middleware, `additionalProperties: false` injected into schemas), one
  `wechat-attachment://` resource template, and the CLI subcommands in
  `cli.py`. Results carry opaque IDs and `BridgeError` codes.
- **Internal-but-load-bearing:** `ToolService` (protocol-independent), the
  `Archive` class, `QueryRepository`, the adapter object (duck-typed; no
  Protocol or ABC), and `refresh_archive`'s return dict
  (`status: complete|failed`, `error`).
- **Leaks:** (a) the adapter's private methods used by `importer` and `proof`
  (finding 10); (b) `archive_status` exported from the write-path module
  (finding 6); (c) `mcp.service.status()` re-derives environment facts via
  `cli.doctor`, which reads `/Applications/WeChat.app/Contents/Info.plist`
  with its own copy of the path that `adapters.wechat_4_1.INFO_PLIST` also
  holds, and checks `DEFAULT_LIBRARY` rather than the saved
  `config.sqlcipher_library` **[observed]**, so `status` may report
  `sqlcipher_available` for a library the bridge is not configured
  to use.
- `core/limits.py` is the single source of the query contract; `mcp/server.py`
  and `mcp/service.py` both import it. Good.

## 6. State and I/O boundaries

Private-state reads and writes go through `core.config` helpers
(`read_private_json`, `write_private_json`, `ensure_private_dir`,
`check_private_fd`, `reject_symlinks`, `state_lock`) for JSON state in `source`,
`adapters`, `setup`, `proof`, `mcp.service` **[observed]**. Raw I/O outside
`core/config.py`, all individually reasonable:

| Site | What | Assessment |
| --- | --- | --- |
| `archive/store.py:28,44` | `os.open(..., O_NOFOLLOW, 0o600)` + `check_private_fd`, then `sqlite3.connect(file: URI)` | Correct (fd-level check, then SQLite path open). The SQLite file cannot go through the JSON helpers. |
| `archive/importer.py:19`, `archive/watcher.py:40`, `setup/lock.py:12` | Three hand-rolled non-blocking `flock` helpers; `core.config.state_lock` is a fourth (blocking). | Duplication. Behavior differs subtly: importer/watcher check `check_private_fd`; `setup/lock.py` and `state_lock` do not, and none call `ensure_private_dir`/`reject_symlinks` on the parent (they rely on a prior `Archive()` or config load). |
| `attachments/cache.py:18-25` | own `mkstemp` + `fsync` + `os.replace` | Same pattern as `write_private_json` minus the directory fsync and `reject_symlinks` on the parent (it does `ensure_private_dir`). Equivalent safety for a cache; duplicate code. |
| `archive/reconcile.py:29,40`, `attachments/resolver.py:29-34`, `attachments/images.py:66` | no-follow, dir_fd-relative opens of source/media trees | Intentional and strong; these are source, not private state. |
| `setup/system.py:87,132` | log file `O_EXCL`; `rename` of the signed copy | Inside the authorized setup lifecycle. |
| `cli.py:21`, `adapters/wechat_4_1.py:99` | read WeChat `Info.plist` | Duplicated (see section 5). |

No `socket`, `urllib`, `http` or `shell=True` use in `src/` outside `subprocess`
list-form calls in `setup/system.py` **[observed]**.

## 7. Error handling

`BridgeError(code)` is the single failure type throughout; boundaries
translate with `from None` (`core/config.py`, `archive/reconcile.py`,
`source/discovery.py`, `search/repository.py:106`). Broad excepts all fall into the three accepted shapes (cleanup that re-raises,
protocol boundary to `internal_error`, recorded failure) **[observed]**:

- Cleanup that re-raises: `archive/store.py:56` (`BaseException`, rollback),
  `archive/watcher.py:44`, `source/reader.py:80,100`.
- Protocol boundary to `internal_error`: `mcp/server.py:54,75,198`.
- Recorded failure: `archive/importer.py:115` (`except Exception` → `archive.fail`).

Observations:

1. `refresh_archive` has **two error channels**: it returns a `status: failed`
   dict for source/adapter errors, but raises `BridgeError` for lock contention
   (`archive_refresh_in_progress`), `archive.begin_generation` failures and
   anything before the `try`. Callers (`watcher.poll`, `cli`) handle both; the
   watcher maps a raise to `deferred`. This is workable and documented by the
   code, but a third caller would need to know. Not a bug.
2. `archive.fail()` inside the `except Exception` can itself raise (e.g.
   `invalid_generation`), which would replace the original code. Low
   likelihood **[inferred]**; the next `begin_generation` reclaims the stage.
3. `cli.py` maps `KeyboardInterrupt` to exit 130 and `BridgeError` to exit 2;
   unexpected exceptions in non-`serve` commands produce a traceback, which can
   include paths. Worth confirming against the redaction rule (security review
   scope).

## 8. Concurrency model

- **Processes:** every MCP client spawns its own `serve`. `archive/watcher.py`
  coordinates them with `.archive-watcher.lock` (non-blocking `flock`; waiters
  poll every 30 s and take over). A crashed holder releases automatically.
- **Import exclusivity:** `.archive-import.lock` (non-blocking `flock`) wraps
  `refresh_archive`; a second concurrent refresh gets
  `archive_refresh_in_progress` instead of waiting.
- **State JSON:** `state_lock` (blocking `flock` on `.lock`) wraps
  `discover`, `verify_keys` publication and identity creation. It is
  blocking with no timeout; a stuck holder would hang `open_adapter`
  **[inferred]**, but holders do only short JSON work.
- **Threads:** one daemon watcher thread per `serve` process; join bounded to
  8 s on shutdown; the MCP tool handlers are synchronous and read from SQLite
  in read-only, `query_only` connections.
- **Publication atomicity [observed]:** every `Archive.connection(write=True)`
  is `BEGIN IMMEDIATE … COMMIT` in WAL mode; `publish` verifies state is
  `building`, writes metadata and flips the pointer in one transaction;
  `begin_generation` reclaims abandoned `building` stages; failed rows hold no
  records. Readers pin a generation per cursor and get `cursor_expired` when
  it is pruned (keep latest two plus the previous complete). The source
  fingerprint is taken before and after the import and a mismatch aborts
  with `source_changed`.
- **Cost:** each 200-record batch is its own transaction on a fresh connection
  (reopen, `PRAGMA journal_mode=WAL`, `BEGIN IMMEDIATE`). Correct and simple;
  its overhead is part of the live refresh time recorded in
  [compatibility.md](compatibility.md), and this review did not re-measure it.

## 9. Extensibility (other WeChat builds and adapters)

- **Same family, new build (the 270102 → 270135 case):** one-line change to
  `SUPPORTED_BUILDS`; the strict schema comparison in `select_adapter` is the
  gate. Cheap and well-tested by the existing pattern.
- **New schema or major version (4.2, Windows):** `open_adapter` hard-codes
  `"4.1.15"`, `/Applications/WeChat.app`, macOS key and path conventions, and
  four call sites bind to `adapters.wechat_4_1` by name. There is no adapter
  protocol or registry. Under the project rule of no abstraction without a present
  need, this is the right state today; the first-order cost of a second
  family is a copy of `wechat_4_1.py` plus edits at four sites.
- **Changes that cheapen a second adapter without speculation:** (a) give the
  adapter a public, narrow surface (R4), (b) move the importer's use of
  `attachment_refs` out of the adapter (R3), (c) keep `adapter_factory`
  injection. Those three are worth doing because they remove present smells,
  not because of a hypothetical second adapter.
- **Archive schema** is adapter-neutral (records are JSON blobs plus a few
  indexed columns), so a second adapter can reuse `archive/` and `search/`
  unchanged **[inferred]**.

## 10. Testability seams

Good seams **[observed]**: `adapter_factory` on `refresh_archive` and
`ArchiveWatcher`; `backend` injection in `setup/*`; `poll(now=..., cancel_event=...)`
for deterministic watcher tests; `ToolService` separate from the MCP wrapper;
`tests/support/` synthetic SQLCipher engine kept out of production. Test
imports concentrate on `archive.importer.refresh_archive` (29),
`adapters.wechat_4_1.open_adapter` (25) and `archive.store.Archive` (24),
so moves of those names need re-exports or a sweep.

Gaps: no test asserts the layering rules or the documented exception list, so
the doc and code can drift without notice (see R6). The adapter's de-facto interface
is only checked through whole-flow tests.

## 11. Design trade-offs

| Decision | Alternative | Assessment |
| --- | --- | --- |
| Full bounded reconcile into a generation, atomic pointer publish | Incremental/CDC import | Sound for a read-only, unmeasured, single-user archive; trivially correct under crash. The cost is refresh time, which [compatibility.md](compatibility.md) records. Keep. |
| Everything stored as JSON blob + a few typed columns | Normalized tables | Fits "unmapped records stay explicit"; schema evolution is cheap. Query flexibility limited; fine at current query shapes. |
| FTS index built in the import transaction; `user_version == 2` as feature flag | Separate index DB; rebuild at query time | Keeps queries cheap and generations self-consistent. The version number doubling as "index present" is a hidden state machine (0/1/2) in `store.py` and `index.py`; any change to it is an archive-schema change that needs prior discussion. Acceptable, and documented here. |
| `archive` and `search` coupled by design via local imports | Hook injected into `Archive` | The coupling is honest and documented. Injecting an `on_records` callable would remove the local imports; low value on its own. |
| Lazy imports in `cli.py` | Eager imports | Right: native SQLCipher and MCP SDK are only loaded for commands that need them. |
| Protocol-independent `ToolService` + thin MCP layer | Logic in handlers | Good; makes unit tests protocol-free and keeps the contract in one place. |
| Per-process watcher with flock leader election | Single daemon / launchd | Matches "each client runs its own serve"; no extra process surface, no network listener. Sound. |
| Duck-typed adapter, one concrete build module | Adapter ABC/registry | Correct under "no speculative abstraction"; the cost shows up only as private-attribute access (R4). |
| `refresh_archive` returns failure dicts | Raise everywhere | Lets the watcher record and back off without exceptions; dual channel is the price (section 7). |
| Per-query `archive_status` → source fingerprint walk (`search/repository.py:140`) | Cached freshness in the archive | Gives truthful per-response `freshness`, but every search/list response stats up to 4096 source entries and does an extra SQLite read. Not decryption, so within "no decrypt at query time", yet it contradicts the spirit of "queries read indexed archive data only". Measure before changing (R7). |

## 12. Risks

| Risk | Severity | Evidence |
| --- | --- | --- |
| Query-path import graph drags in the whole adapter/source stack (and the native ctypes loader import) through `archive.importer`; one new import in `importer` changes query start-up and failure modes. | Medium | `search/repository.py:10`, `mcp/service.py:4` → `archive/importer.py:7`. |
| `adapters ⇄ attachments` cycle masked by a local import; a module-level import added in either direction breaks at load. | Medium | `adapters/wechat_4_1.py:354` vs `attachments/mapping.py:8-9`. |
| Adapter private API used by `importer` and `proof`; a tidy-up rename breaks acceptance proof paths without warning. | Medium | `importer.py:77`, `proof.py:90-117`. |
| `discover()` iterates `root.rglob("*")` with no entry cap (while `source_fingerprint` caps at 4096) and rewrites `inventory.json` on every call. Violates "bounded everything" in the pathological case. | Medium | `source/discovery.py:~57`; compare `archive/reconcile.py:21`. |
| `doctor`/`status` reports library availability for the default path, not the configured one; path constants duplicated. | Low | `cli.py:17-28`, `adapters/wechat_4_1.py:28`. |
| Four hand-rolled lock helpers with differing hardening (fd check, parent symlink check). | Low | Section 6. |
| Documentation drift: no check ties `architecture.md` exception table to code. | Low | Section 10. |
| `state_inside_repository` guard assumes a source checkout. | Low | `core/config.py:~117`. |

## 13. Recommendations (value / effort order)

All are behavior-preserving refactors unless marked. None adds a capability,
dependency or tool; none changes MCP tool shapes, CLI codes, archive schema,
state layout or identity derivation. Update `docs/architecture.md` in the same
change.

| # | Change | Value | Effort | Maintainer decision first? |
| - | --- | --- | --- | --- |
| R1 | Move `archive_status` and `_public_status` from `archive/importer.py` to `archive/status.py` (or `archive/reconcile.py`); keep a re-export in `importer` while tests migrate. Search and mcp then no longer import the write path. | High | Low | No (internal move). |
| R2 | Move `doctor` and the `Info.plist` read to one module below `mcp` (for example `source/environment.py`), used by `cli`, `mcp.service` and `adapters.wechat_4_1.open_adapter`. Removes documented exception #3 and the duplicated plist path. Keep the current output values byte-identical; checking the configured library path instead of `DEFAULT_LIBRARY` changes a reported value and should be a separate, noted change. | High | Low | No for the move; the library-path fix is a visible `status`/`doctor` value change, so confirm. |
| R3 | Have `archive/importer.py` and `proof.py` call `attachments.mapping.attachment_refs(adapter, message)` directly and drop `Wechat41Adapter.attachment_refs` (and its local import). Removes documented exception #2. Requires importer/proof to depend on `attachments` (legal by rank). | High | Medium | No. |
| R4 | Rename or expose the adapter members that outside code uses (`_message_tables`, `_open`, `_resolve_reply`) as public names (or one small `Protocol` in `adapters/models.py` listing exactly what `importer` and `proof` need). Update `proof.py` and `importer.py`. | Medium | Low | No. |
| R5 | Bound `discover()`'s `rglob` with the same 4096-entry cap and error code used by `source_fingerprint` (`source_inventory_limit`). Add a regression test with a synthetic tree. | Medium | Low | Adds an error path on pathological trees only; state it in the change description. Not an invariant change. |
| R6 | Make `setup/capture.py` import the public `recover_setup` (or move the shared body to a private module both use); put `parse_xml` in `adapters/wire.py` or a tiny `adapters/xml.py` so `messages`/`replies` stop importing each other. | Low | Low | No. |
| R7 | Measure per-query cost of `archive_status` (stat walk plus second SQLite read) with the existing `benchmarks/search.py` before changing anything; record the result in `docs/compatibility.md`. If material, cache the fingerprint for a short TTL inside `QueryRepository` (freshness semantics unchanged within the TTL). | Medium | Medium | Possibly (changes freshness timing guarantees; discuss first). |
| R8 | One lock helper in `core.config` (`exclusive_lock(path, *, blocking)` doing `ensure_private_dir`, `O_NOFOLLOW`, `check_private_fd`) used by importer, watcher, setup and `state_lock`; and a `write_private_bytes` used by `attachments/cache.py`. | Medium | Medium | No: it centralizes existing private-state I/O rather than adding a capability, but it touches `setup/lock.py`, and `CONTRIBUTING.md` asks for an issue first for `setup/` changes. |
| R9 | Add an import-layering pytest that parses `src/` with `ast` and compares against a short allow-list mirroring `architecture.md`. | Medium | Low | **Yes, discuss first**: it is a test and no linter, but `CONTRIBUTING.md` asks that tooling arrive through discussion; confirm it counts as a test. |
| R10 | Split `core/config.py`: leave private-state I/O + `Config` in `core`, move `DEFAULT_ACCOUNTS`/`select_account`/`DEFAULT_LIBRARY` probing to `source/`. | Low | Medium | No, but many import sites; do after R1-R4. |

**Not recommended now**: an adapter registry or ABC (no second
adapter exists), a dependency-injection framework, per-batch connection reuse
in `Archive` (unmeasured; changes transaction boundaries on the
publish-atomicity path and would need independent review), and any change that
moves `user_version` semantics (an archive-schema change that needs prior discussion).

## 14. Verified sound

- Layering holds at module level; no module-level import cycles; `core`,
  `source`, `setup` import strictly downward.
- Foundation modules are small, single-purpose and dependency-free beyond
  `core.errors`; the package regrouping left no stale references.
- Generation publication is one transaction guarded by `building` state;
  failed and abandoned stages are reclaimed; a missing established shard or
  unmapped message table raises before publish
  (`importer.py:66-67,93-94`); source fingerprint is compared before and after.
- Read connections are `mode=ro` + `query_only` with a progress-handler deadline;
  cursors are bound to method, filters, limit and generation.
- Private JSON state is owner-only, symlink-rejecting, size-bounded and
  atomically written; state inside the repository is rejected.
- Error discipline: one exception type, `from None` at boundaries, broad
  excepts limited to the three permitted shapes, MCP errors reduce to a code
  and unknown arguments are normalized so SDK text never echoes input.
- No network surface, no UI automation, no write path to source databases in
  production code; subprocess use is confined to `setup/system.py` with list
  arguments.
- Concurrency across multiple `serve` processes is correct by construction
  (kernel-released `flock`, non-blocking import lock, watcher leader election).
