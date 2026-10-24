# Architecture

`wechat-bridge` is a local, read-only, one-directional pipeline from WeChat's
encrypted local databases to a private archive and a stdio MCP server. This page
describes the code as implemented; update it in the same change as any
structural code change.

```mermaid
flowchart LR
    S[setup/<br/>one-time key capture] -.keys.json.-> K
    D[(WeChat SQLCipher DBs + WAL)] --> K[source/<br/>discovery · keys · reader]
    K --> A[adapters/<br/>builds 270102 · 270135 mappings]
    A --> R[archive/<br/>generations · reconcile · watcher]
    R --> I[search/<br/>index · repository · cursor]
    F[(WeChat media files)] --> T[attachments/<br/>mapping · resolver · images]
    A --> T
    I --> M[mcp/<br/>service · server]
    T --> M
    M --> C[MCP client over stdio]
    A --> P[proof.py<br/>acceptance gate]
    T --> P
```

## Packages

All under `src/wechat_bridge/`. Each module's one-line docstring states its
invariant; read it before editing the module.

| Package | Responsibility | Key modules |
| --- | --- | --- |
| `source/` | Lowest layer above `core/`. Inventory DB identities without decrypting; hold exact per-database keys; run read-only, query-only, authorizer-restricted, deadline-bounded SQLCipher transactions. Never merges WAL pages manually. | `discovery`, `keys`, `native` (ctypes bindings), `reader` |
| `adapters/` | Interpret source rows for the supported builds only (`SUPPORTED_BUILDS` in `adapters/wechat_4_1.py`: 270102 and 270135 of WeChat 4.1.15). Bounded schema observation and identifier whitelisting; decode messages, replies, protobuf wire data; derive stable opaque IDs from a private persisted identity secret. Unmapped/malformed records keep their source identity and are reported, never dropped. | `wechat_4_1`, `schema`, `messages`, `replies`, `identity`, `wire`, `models` |
| `archive/` | Private owner-only SQLite archive (separate from the source). Full bounded reconciliation into a *generation*; publish only a complete, stable-source generation via one atomic pointer. Failed/interrupted imports keep the prior generation; current + previous are retained. | `store`, `importer`, `reconcile`, `watcher` |
| `search/` | FTS index built inside the import transaction; NFKC/casefold literal substring matching over Unicode unigrams/bigrams; small indexed queries with no decryption or import at query time; authenticated opaque cursors bound to method, filters, limit and a pinned generation. | `index`, `tokenize`, `repository`, `cursor` |
| `attachments/` | Exact resource/hardlink joins on tested layouts; no-follow descriptor reads pinned beneath the account's media roots with size/digest/streaming SHA-256 checks; static image decoding; Media key material is separate from database keys. | `mapping`, `resolver`, `images`, `models` |
| `mcp/` | `service` produces verified bounded bytes and safe status independently of protocol; `server` exposes nine typed read-only tools over stdio (stdout is protocol only). See [mcp.md](mcp.md). | `service`, `server` |
| `setup/` | One-time key capture, never called by retrieval. Expiring plan → ad-hoc-signed *copy* of WeChat under Apple LLDB → page-authenticated, natively verified keys → idempotent recovery. See [setup.md](setup.md). | `prepare`, `capture`, `lldb_capture`, `recovery`, `lock`, `system` |
| `proof.py` | Source-acceptance gate: private selection of samples with explicit expected values; redacted public report. | none |
| `cli.py` | `wechat-bridge` entry point; lazy-imports each subsystem per command. | none |

Shared foundation modules live in the `core/` package and import nothing above
it. The top-level modules `cli.py` and `proof.py` sit above the layers they
orchestrate:

| Module | Purpose |
| --- | --- |
| `core/config.py` | Frozen `Config`; owner-only, symlink-rejecting, `O_NOFOLLOW`, size-bounded, atomic (temp + rename + fsync under `flock`) private-state I/O. Rejects state inside the repository (`state_inside_repository`); the guard runs in `create_config` and only means something for a source checkout. |
| `core/errors.py` | `BridgeError(code)`. Public output is a code, never source material. |
| `core/limits.py` | Public query-contract limits (values in [mcp.md](mcp.md#limits)). Other bounds live next to the code that uses them. |
| `core/validate.py` | Shared strict input validators raising a caller-chosen `BridgeError` code. |
| `core/serialize.py` | The single compact JSON encoding for archive storage and MCP text. |
| `core/clock.py` | UTC ISO-8601 timestamps. |

## Dependency direction

The intended layering (module-level imports follow it):

```
foundation = core/ (config, errors, clock, validate, serialize, limits)
  ← source ← adapters ← archive ⇄ search ← mcp
                      ← attachments (also imported by search, mcp, proof)
  source ← setup
```

Known exceptions, all function-local imports:

| Import | Reason |
| --- | --- |
| `archive/importer.py`, `archive/store.py` → `search.index` | The search index is built inside the import transaction, so `archive` and `search` are coupled by design (`search` imports `archive.store` at module level). |
| `adapters/wechat_4_1.py` → `attachments.mapping` | Attachment references are derived while adapting messages; `attachments.mapping` itself imports `adapters.identity` and `adapters.wire`. |
| `mcp/service.py` → `cli.doctor` | `status()` reuses the CLI's environment report. |

Add no new upward imports. Move shared code down a layer.

`setup/lldb_capture.py` is a special case: it runs under Apple's system
`/usr/bin/python3` inside LLDB, not the project interpreter. It must stay
standard-library only, import nothing from the package, and remain compatible
with that older Python.

## Runtime state

Private state lives outside the repository, by default in
`~/Library/Application Support/WechatBridge` (override with `--state-dir` or
`WECHAT_BRIDGE_STATE_DIR`). Directories are 0700, files 0600.

| Path | Contents |
| --- | --- |
| `config.json`, `inventory.json` | Selected account root and native library path; discovered DB identities |
| `keys.json` | Verified per-database keys. Never print, copy or commit. |
| `identity.json` | Identity secret. Deleting it changes every stable ID. |
| `image-material.json` | Optional explicit image-key candidates; when absent, material is derived from known app-data locations |
| `adapter-schemas.json` | Schema observation written each time an adapter opens |
| `archive.sqlite3` (+ WAL/SHM) | Archive generations and search index |
| `setup/<plan-id>/`, `setup/active.json` | Setup plans, journals, capture diagnostics; the plan that owns setup now |
| `proofs/<uuid>.json` | Private proof evidence |
| `.lock`, `.archive-import.lock`, `.archive-watcher.lock`, `.setup.lock` | `flock` files, described under [Locks](#locks) |

The native SQLCipher library resolves to Homebrew's
`/opt/homebrew/opt/sqlcipher/lib/libsqlcipher.dylib` if present, otherwise
`~/.local/share/wechat-bridge/sqlcipher-4.19.0/lib/libsqlcipher.dylib` (built by
`scripts/build_sqlcipher.py`, which checks the source archive's SHA-256). A saved
`config.json` keeps its explicit path.

### Locks

All four are advisory `flock` files in the state directory (mode 0600,
`O_NOFOLLOW`). The kernel releases a lock when its process exits, so a crashed
holder leaves no stale lock.

| File | Taken by | Behaviour on contention |
| --- | --- | --- |
| `.lock` | `core.config.state_lock`: registry, key-manifest and identity-secret read-modify-write (`discover`, `publish_verified_manifest`, identity creation) | Blocks until free |
| `.archive-import.lock` | `archive.importer.import_lock`: `archive-refresh`, watcher refreshes, `search-index` | Fails at once with `archive_refresh_in_progress` |
| `.archive-watcher.lock` | `archive.watcher.run_exclusive`: one watcher per state directory across `serve` sessions | Waits and retries every 30 s; takes over when the holder exits |
| `.setup.lock` | `setup.lock.setup_lock`: `setup run` and `setup recover` | Fails at once with `setup_busy`. A separate `setup/active.json` check returns `setup_recovery_required` while an earlier plan is unrecovered |

## Key contracts

- **Source reads:** read-only flags, `query_only`, restrictive authorizer, 2 s
  busy timeout, 5 s query deadline, 8 MiB result cap (`result_too_large`).
  SQLite may coordinate through the `-shm` file (shared-memory coordination,
  not a write to database content).
  Per-database observation times; no atomic account-wide snapshot is claimed.
- **Key verification** (`verify-keys`) authenticates a schema read per database
  using an exact account/path/salt/incarnation match. It does not prove
  full-page integrity. Only verified candidates whose ID the registry accepts
  are merged atomically into the manifest; failures preserve existing keys.
  A missing `keys.json` returns `setup_required`, and incomplete coverage of
  the required databases returns `key_coverage_incomplete`; neither starts a
  debugger.
- **Required databases:** `contact/contact.db`, `session/session.db`,
  `message/message_resource.db`, `hardlink/hardlink.db`, plus every
  `message/message_<n>.db` shard (`REQUIRED` and `MESSAGE_SHARD` in
  `source/discovery.py`). The required count therefore grows with the number of
  message shards. Other databases are discovered and keyed but not required.
  Discovery flags any `.kvdb` path as `auxiliary_storage_unhandled`.
- **Database incarnations:** a database ID belongs to a relative path + salt.
  A new salt is a new incarnation with a new ID (and new message IDs). A
  same-salt rewrite (new inode) keeps its ID and is flagged for reverification;
  the saved key must still open it; the flag (`replaced_count`) clears at the
  next `verify-keys`. If the registry holds a different ID for the same path +
  salt on a row flagged replaced, `verify-keys` restores the verified key's ID
  once that key opens the file, unless another row owns that ID. That run still
  reports `replaced` (exit 2); the next run reports `verified`.
- **Generations:** a refresh either publishes a complete generation or leaves the
  previous one active and marks status stale. A missing established message
  shard (`source_shard_missing`) or an unmapped message table
  (`message_table_mapping_incomplete`) blocks
  publication; deletion is never inferred from absence. Abandoned `building`
  generations are reclaimed at the next `begin_generation`, which also keeps
  only the latest failed generation row (failed rows hold no records). After a crash,
  reconciliation restarts the full bounded scan and does not resume batches.
  This choice favors correctness over an unmeasured large-archive optimization.
- **Watcher:** polls DB/WAL metadata (1 s debounce) and runs a full reconcile every
  60 s while the source is changing. A reconcile that finds the source untouched
  doubles the next interval, up to one hour, and a source change restores 60 s,
  since every refresh rewrites the whole archive. Every attempt is followed by
  idle time of four times its own duration (`MAX_DUTY_CYCLE`, 20%), so a busy
  source or a repeating failure cannot hold a core. It runs independently of
  queries; queries never trigger import. Concurrent
  `serve` sessions coordinate on `.archive-watcher.lock` so only one watcher
  polls per state directory at a time; the others retry the lock every 30 s and
  take over if the holder exits. After a `setup_required`,
  `key_coverage_incomplete`, `unsupported_build` or `archive_account_mismatch`
  failure the watcher retries only when `keys.json` changes or after an hour,
  since source writes cannot clear those states and each attempt records a
  failed generation. A refresh that fails with another code is retried on the
  next reconcile (60 s, never sooner than the duty-cycle rest). A refresh
  blocked by `.archive-import.lock` returns `deferred` without recording a
  generation.
- **Attachments:** files ≤ 200 MiB; images ≤ 50 MiB encoded/decoded and ≤ 40 M
  pixels. Static JPEG/PNG/WebP, legacy XOR and verified V1/V2 DAT decode;
  animated, WXGF and HEVC are unsupported. A thumbnail never stands in for an
  original, and a first frame is never labeled an original. Media key material
  is derived only from documented app-data locations or an owner-only
  `image-material.json` in private state; no debugger runs during reads, and
  database keys are never used as media keys.
- **Acceptance proof** (`prove`): a private selection lists `samples` for
  `message`, `contact`, `group`, `reply`, `original_image` and `ordinary_file`,
  each with explicit expected values; message samples pin conversation and
  database/table/local-row identity. Only `expectation_basis: user_confirmed`
  (set after independent confirmation) can pass; missing checks and
  automatically selected expectations cannot. Production JSON cannot assert
  `synthetic_fixture` ground truth or override the detected build. Detailed
  evidence goes to private `proofs/`; the public report omits sample content,
  identifiers, hashes and paths.
- **Errors:** every failure surfaces as a `BridgeError` code; CLI exit codes
  are listed in the [README](../README.md#commands). MCP codes are in
  [mcp.md](mcp.md#errors-and-trust).

## Tests

`tests/unit/` and `tests/integration/` use synthetic data only. `tests/support/`
provides the writable SQLCipher fixture engine (`encrypted.py`, never imported
by production), hand-authored schemas, sample builders, image generators, a
complete synthetic proof and a controlled setup backend. Native tests need the
SQLCipher library above; the suite has no skipped tests.
