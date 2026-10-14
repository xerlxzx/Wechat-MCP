# Security review: public-release audit

Date: 14 October 2026. Reviewer: independent read-only review. The review
modified no source, test or runtime state, and it touched no setup, WeChat
process, key material or live archive.

This report cites `file:line` and describes classes of data. It reproduces no
real identifier, key or message content.

## 1. Scope and method

Layers present: Python source (`src/wechat_bridge/`), setup/LLDB helper, native
SQLCipher bindings (ctypes), stdio MCP server, CLI, tests/benchmarks, docs,
`pyproject.toml`/`uv.lock`, one build script. Layers absent: network listener,
web frontend, HTTP API, containers, CI workflows (`.github/` holds community
files only).

Method:

- Read in full: `core/config.py`, `core/errors.py`, `core/validate.py`,
  `core/limits.py`, `source/{native,reader,discovery,keys}.py`,
  `search/{cursor,repository,tokenize,index}.py`, `mcp/{server,service}.py`,
  `archive/{store,importer,reconcile,watcher}.py`,
  `attachments/{cache,resolver,images,mapping}.py`,
  `setup/{lldb_capture,capture,system,prepare,recovery,lock}.py`,
  `adapters/{identity,wire,replies}.py`, `cli.py`, `scripts/build_sqlcipher.py`,
  parts of `adapters/{messages,wechat_4_1,schema}.py` and `proof.py`.
- Grepped all of `src/` for `eval/exec/pickle/yaml/shell=True/os.system/urlopen/socket`,
  f-string SQL, `print(`, and imports from `tests`.
- Release leakage: pattern scans of the tracked tree and its history for home
  paths, account names, emails, long hex strings, `wxid_` identifiers, key-like
  assignments and PEM/token formats. Release history contains no secrets, and the
  public repository starts from a single commit.
- Not run: the test suite (a static review does not need it). Dependency CVE lookup was not possible offline, so dependency findings
  are marked unverified.

Severity scale: Critical / High / Medium / Low / Info. Confidence is HIGH when the
review read the exact code or text.

## 2. Summary

No Critical or High findings. The review found no secrets, keys, message
content, real account identifiers or private hashes in the tracked tree.

Status values: **Resolved** means the shipping tree addresses the finding.
**Open** means a documentation or data change remains. **Known limitation,
tracked** means the fix needs maintainer approval because it touches `setup/`,
the private-state layout or a public contract, and the docs describe the
limitation meanwhile.

| # | Severity | Class | Title | Status |
|---|----------|-------|-------|--------|
| P-1 | Medium | Release | No LICENSE, SECURITY.md or CODE_OF_CONDUCT.md; dangling links | Resolved |
| P-3 | Low | Leakage | Aggregates and per-database byte sizes from the maintainer's real archive in a research manifest | Resolved |
| H-1 | Medium | Data handling | Plaintext keys, archive and media at rest; extra key copies never removed; backup exposure | Known limitation, tracked |
| H-2 | Low | Data handling | Decoded-image cache is write-only, unbounded and plaintext | Known limitation, tracked |
| H-3 | Low | Secret handling | Setup helper inherits the full environment and exposes the account path in argv | Known limitation, tracked |
| H-4 | Low | Supply chain | Native library path is trusted without pinning; dependency CVE status unverified | Known limitation, tracked |
| H-5 | Low | Injection | Retrieved text carries no per-item trust marker (indirect prompt injection) | Known limitation, tracked |
| H-6 | Low | Data exposure | CLI prints raw tracebacks for non-`BridgeError` failures | Known limitation, tracked |
| H-7 | Info | Authz | `source_acceptance` trusts any `proofs/*.json` the user owns | Known limitation, tracked |
| H-8 | Info | Configuration | State-in-repo guard is index-based and only meaningful from a checkout | Known limitation, tracked |

The identifier P-2 is unused. It covered identity strings that appeared only in
files that do not ship.

Counts: Critical 0, High 0, Medium 2, Low 6, Info 2 (10 findings).

## 3. Release findings

---
**FINDING P-1**

**Title:** The repository had no license or security policy, and docs linked to files that did not exist.

**Status:** Resolved. The release ships `LICENSE` (MIT), `SECURITY.md` and
`CODE_OF_CONDUCT.md`, and the documentation links to all three.

**Severity:** Medium

**Category:** Configuration (release hygiene)

**Evidence:** At review time the tree held no `LICENSE`, `SECURITY.md` or
`.github` file. `pyproject.toml:1-10` has no `license` field.
`CONTRIBUTING.md` linked to `CODE_OF_CONDUCT.md` and `SECURITY.md`, which did
not exist. `third_party/licenses/acmerfight-toolkit.txt:3` reads
`Copyright (c) 2026 Contributors`.

**Exploitation path:** None. A public repository with no license grants no reuse
rights, and a vulnerability reporter without a private channel is likely to file
a public issue. This tool handles private messages, so that matters.

**Impact:** Legal ambiguity, unmanaged disclosure of future vulnerabilities and
broken links in the first-impression docs.

**Remaining follow-ups:**

- Add a `license` field to `pyproject.toml` that matches `LICENSE`.
- Check that the copyright line in the third-party license text matches the
  upstream gist revision recorded in `THIRD_PARTY_NOTICES.md`. The file says
  "Contributors", and MIT requires the upstream notice verbatim.
- Enable GitHub private vulnerability reporting on the repository.
  `SECURITY.md` directs reporters to it.

**Confidence:** HIGH

---
**FINDING P-3**

**Title:** A research manifest publishes aggregates about the maintainer's real archive.

**Status:** Resolved. The research manifests are not part of the public
repository, and `docs/research/` is gitignored so they cannot be committed.

**Severity:** Low

**Category:** Data Exposure

**Evidence:**

- `docs/research/source-discovery.json` records per-database byte sizes and
  WAL presence for one live account, with an `observed_date`.
- `docs/research/research-sources.json` records the account and database counts
  that the research observed (`local_observations`).

**Exploitation path:** None direct. The files hold aggregates and no values, but
together they describe one real account's approximate size.

**Impact:** Minor privacy disclosure. `SECURITY.md` and `CONTRIBUTING.md` require
diagnostics to be redacted aggregates, which these files mostly satisfy. The
data comes from a real account.

**Affected surfaces:** `docs/research/source-discovery.json`,
`docs/research/research-sources.json`.

**Mitigation:** Exclude the research folder from the repository and ignore it.
`docs/compatibility.md` states rounded figures only.

**Confidence:** HIGH

## 4. Findings: hardening (not release-blocking)

---
**FINDING H-1**

**Title:** Raw database keys, the identity secret, the full plaintext archive and proof evidence sit unencrypted in the state directory; capture keys are duplicated and never deleted.

**Severity:** Medium

**Category:** Secret Leakage / Data Exposure

**Evidence:**

- `source/keys.py:42`: `private_dict()` writes `"raw_key_hex": self.raw_key.hex()`;
  `keys.py:~148`: `write_private_json(path, KeyManifest(...).private_dict())`.
- `adapters/identity.py:31`: `write_private_json(path, {"version": 1, "secret_hex": os.urandom(32).hex()})`
  (the key for every stable ID and cursor MAC).
- `archive/store.py:~56-62` (`_create`): all message text stored in `messages.text`/`data` in
  an ordinary SQLite file (`archive.sqlite3`, 0600).
- `proof.py:264`: proof reports persist `selection.private_dict()`,
  `observations`, `schemas` and `evidence` (sample text and usernames).
- `setup/capture.py:64-65` (candidates) and `setup/system.py:82-87` (capture output path): `plan.directory/captured.json`
  (raw keys, written by `lldb_capture.py:27-31,141`) and `candidates.json` are
  written under `state/setup/<plan>/`. `setup/recovery.py` and
  `SystemBackend.disable_copy` (`system.py:129-132`) only rename the signed
  app copy to `WeChat.disabled`; nothing unlinks `captured.json`, `candidates.json`
  or the copy after verification.

**Exploitation path:** Any process running as the same user (a compromised
dependency, another MCP client's child process, a malicious package script) can
read `keys.json` and the archive with no further privilege. Time Machine and
iCloud-synced backups of `~/Library/Application Support` also carry the
plaintext archive and key copies unless excluded. Requires a same-user foothold
or backup access; file modes (0600/0700, symlink and owner checks in
`core/config.py:28-62`) are correct and the review found no way around them.

**Impact:** Full disclosure of message history and the ability to decrypt the
source databases offline.

**Status:** Partially resolved. `setup run` and `setup recover` now delete
`captured.json` and `candidates.json` once keys are verified and recovery
completes, and report `key_copies_removed`. The `WeChat.disabled` app copy
remains as a recovery artifact, and older setups may still hold the key copies.
`README.md` and `SECURITY.md` document the backup exposure and tell users to
exclude the state directory from backups. Tracked in
[issue #1](https://github.com/xerlxzx/Wechat-MCP/issues/1).

**Affected surfaces:** `keys.json`, `identity.json`, `archive.sqlite3` (+WAL),
`proofs/*.json`, `setup/<plan>/{captured,candidates}.json`, `decoded/`.

**Mitigation:** (a) delete `captured.json` and `candidates.json` once the plan is
verified and recovered, keeping only `keys.json` (done; the disabled app copy
remains); (b) set the
backup-exclusion attribute on the state directory when it is created (`NSURL`
`isExcludedFromBackup`, equivalent `xattr com.apple.metadata:com_apple_backup_excludeItem`);
(c) document that FileVault is required (done in `README.md` and
`SECURITY.md`); (d) consider the macOS Keychain for `keys.json` and
`identity.json`. Item (a) touches `setup/` and (d) changes the private-state
layout, so both need maintainer approval.

**Confidence:** HIGH for the code paths. The review read the code and did not
inspect a live state directory.

---
**FINDING H-2**

**Title:** Decoded-image cache is write-only, unbounded and plaintext.

**Status:** Known limitation, tracked in [issue #2](https://github.com/xerlxzx/Wechat-MCP/issues/2).

**Severity:** Low

**Category:** Data Exposure / DoS

**Evidence:** `mcp/service.py:88` calls `cache_decoded(result.decoded, ...)` on
every decoded `get_attachment`; `attachments/cache.py:10-24` writes
`state/decoded/<sha256>.<fmt>`. `grep -rn cache_decoded src` shows no reader, so
the cache is never consulted. Nothing evicts it. An `OSError` here (disk full)
propagates and `mcp/server.py:75` (`except Exception`) turns the whole delivery into `internal_error`.

**Exploitation path:** A client (or an injected prompt driving it) iterates
`list_attachments` then `get_attachment` over every image; each call hashes up to
50 MiB, decodes up to 40 MP, and leaves a plaintext copy behind. Growth is
bounded by the number of attachments times the image cap, but it is unbounded in
practice and never cleaned.

**Impact:** Extra plaintext copies of private media; disk exhaustion; delivery
failure coupled to an unused cache.

**Affected surfaces:** `mcp/service.py`, `attachments/cache.py`, `decoded/`.

**Mitigation:** Remove the write (nothing reads it) or make it best-effort
(`except OSError: pass`) with a size cap and eviction. Removing it also aligns
with the "queries read indexed data only" rule. `docs/architecture.md:93`
documents `decoded/` and must change with it.

**Confidence:** HIGH

---
**FINDING H-3**

**Title:** The LLDB helper runs with the caller's full environment and the account path in argv.

**Status:** Known limitation, tracked in [issue #4](https://github.com/xerlxzx/Wechat-MCP/issues/4).

**Severity:** Low

**Category:** Secret Leakage / Configuration

**Evidence:** `setup/system.py:82`: `env = dict(os.environ, PYTHONPATH=self.lldb_python)` (also `:49`)
then `subprocess.Popen(["/usr/bin/python3", str(helper), "--exe", ..., "--db-root",
<account>/db_storage, "--out", ..., "--owned", ...], env=env)`. `command()`
(`system.py:14`) passes `env=None`, so every helper inherits the environment too.
Python is started without `-I`, so `PYTHON*` variables set by the caller apply
(only `PYTHONPATH` is replaced). `--db-root` exposes the account folder name to
other local users through `ps`.

**Exploitation path:** A hostile environment variable in the invoking shell
alters the interpreter that holds the LLDB handle on a process containing the
database keys; a local user reads the account folder name from the process list.
Both need a pre-existing local foothold or an already tainted shell.

**Impact:** Low. The helper is invoked only during an explicit, user-authorised
setup.

**Mitigation:** Build the child environment from an allowlist (`PATH`, `HOME`,
`LANG`, `PYTHONPATH`) and pass the account root through a private file or
inherited fd rather than argv. This touches `setup/`, so it needs maintainer
approval.

**Confidence:** HIGH

---
**FINDING H-4**

**Title:** The SQLCipher shared library path is trusted without pinning, and dependency CVE status is unverified.

**Status:** Known limitation, tracked.

**Severity:** Low

**Category:** Dependency / Configuration

**Evidence:** `core/config.py:14-17` falls back from `/opt/homebrew/opt/sqlcipher/...`
to `~/.local/share/wechat-bridge/sqlcipher-4.19.0/lib/libsqlcipher.dylib`;
`core/config.py:~150` takes `sqlcipher_library` from `config.json`;
`source/native.py:9` runs `ctypes.CDLL(str(path))` on it. `reader.py:73-74`
checks only that `cipher_version` starts with `4.`. `scripts/build_sqlcipher.py:12`
pins the source archive SHA-256 (good) but nothing binds the built library.
`pyproject.toml:9-12` uses lower bounds (`cryptography>=50.0.2`, `pillow>=12.3.0`)
with `uv.lock` pinning cryptography 50.0.2, Pillow 12.3.0, mcp 2.3.0, pydantic
2.13.5, starlette 1.7.0, uvicorn 0.54.0. The lock includes `httpx2`, `httpcore2`
and `truststore`; the review could not independently confirm their provenance (they are
declared by `mcp==2.3.0` and hash-pinned from PyPI).

**Exploitation path:** Native code replaced at the library path (needs write
access to the user's home or admin-group write access to the Homebrew prefix)
runs in the process that holds database keys. Same-user compromise already
exposes `keys.json` (H-1), so this adds little.

**Impact:** Low incremental. Pillow parses images sent by any contact (decoder
CVEs are the realistic remote vector; bounded by size and 40 MP caps in
`attachments/images.py:20-21`).

**Mitigation:** Record and compare the library's expected file hash or
code-signature identity in setup; run a one-off audit (`uv export | osv-scanner`
or `pip-audit`) before tagging a release and record the result in
`THIRD_PARTY_NOTICES.md`. Adding audit tooling to CI needs maintainer approval.

**Confidence:** MEDIUM (no CVE data available offline; dependency findings are
unverified).

---
**FINDING H-5**

**Title:** Message text, quoted text, filenames and display names are returned verbatim with no per-item trust marker.

**Status:** Known limitation, tracked in [issue #3](https://github.com/xerlxzx/Wechat-MCP/issues/3). `SECURITY.md` names the risk, and the
server's `instructions` string labels retrieved content as untrusted.

**Severity:** Low

**Category:** Injection (indirect prompt injection)

**Evidence:** `search/repository.py:130` copies source text into
`excerpt`; `adapters/replies.py:23` returns `quoted_text`; `mcp/service.py:76`
returns `original_filename`; `get_attachment` can return raw file bytes as an MCP
resource (`mcp/server.py:66-70`). The only mitigation is the server
`instructions` string (`server.py:125`) and docs (the README privacy section, `docs/mcp.md` "Errors and trust").

**Exploitation path:** Any WeChat contact or group member sends text such as
"ignore previous instructions and ..."; the user's agent retrieves it through
`search_messages`/`get_messages`. If that agent has shell, file or network tools,
the sender can try to steer them. The bridge itself is read-only and cannot
act, which limits impact to the client side.

**Impact:** Depends on the MCP client's other tools; not exploitable inside the
bridge.

**Mitigation:** Already documented, which is the right place to start. Optional:
add `"content_trust": "untrusted_external"` to each item (an MCP result-shape
change, which needs maintainer approval), and name prompt injection from message
senders in the user-facing threat model (`SECURITY.md` now does), with the advice
to run clients without write or network tools.

**Confidence:** HIGH that no marker exists; the severity is a judgement.

---
**FINDING H-6**

**Title:** The CLI catches only `BridgeError`; other exceptions print a raw traceback.

**Status:** Known limitation, tracked in [issue #5](https://github.com/xerlxzx/Wechat-MCP/issues/5).

**Severity:** Low

**Category:** Data Exposure

**Evidence:** `cli.py:139-144` handles `BridgeError` and `KeyboardInterrupt`
only. Examples that can raise `OSError` raw: `archive/store.py:28`
(`os.open(self.path, O_RDWR|O_CREAT|O_NOFOLLOW, 0o600)` is outside any `try`).
`mcp/server.py` already maps arbitrary exceptions to `internal_error` for tool
calls, so this affects CLI commands only. The review did not execute a failing case, so
the traceback content is **unverified**; reasoning says it includes the state
path, which the project treats as private.

**Exploitation path:** A user pastes a traceback into an issue or log.

**Impact:** Disclosure of local paths (including the account folder under
`state_dir`).

**Mitigation:** Add a final `except Exception` in `cli.main` that prints
`{"error": "internal_error"}`, which matches how the MCP server already treats
unexpected exceptions.
Test: monkeypatch `Archive` to raise `PermissionError` and assert stdout.

**Confidence:** MEDIUM

---
**FINDING H-7**

**Title:** `status.source_acceptance` reports the newest `proofs/*.json` whose `proof_scope` is `live`, without checking provenance.

**Status:** Known limitation, tracked.

**Severity:** Info

**Category:** Client Trust

**Evidence:** `mcp/service.py:26-40` reads any `proofs/*.json` the user owns and
returns its `status` and `expectation_basis`.

**Exploitation path:** A same-user process drops a file saying `"status": "passed"`;
the MCP `status` tool then reports acceptance passed, contradicting the
documented "M1 acceptance is blocked" state. Needs same-user write access.

**Impact:** Misleading status only.

**Mitigation:** Store a MAC (identity-key HMAC) in the proof file and verify it
in `_acceptance`.

**Confidence:** HIGH

---
**FINDING H-8**

**Title:** The "state inside repository" guard computes the repo root by parent index.

**Status:** Known limitation, tracked.

**Severity:** Info

**Category:** Configuration

**Evidence:** `core/config.py:127`: `repo_root = repo_root or Path(__file__).resolve().parents[3]`.
After the move to `core/` this is correct for a checkout (`parents[3]` is the
repository root), but for an installed wheel it resolves to a directory inside
`lib/python3.14`, so the check is vacuous there. The guard only runs in
`create_config`, not `load_config`.

**Impact:** None in the supported checkout workflow. The `core/` move kept the guard
correct.

**Mitigation:** Document that the guard applies to source checkouts, or search
upward for `pyproject.toml`.

**Confidence:** HIGH

## 5. Public-release checklist

### History

Release history contains no secrets, and the public repository starts from a
single commit.

### Checklist

| # | Item | Status |
|---|------|--------|
| 1 | P-1: `LICENSE` (MIT), `SECURITY.md`, `CODE_OF_CONDUCT.md` | Resolved (add the `pyproject.toml` `license` field and confirm the third-party copyright line) |
| 2 | Identity strings (home paths, account names) in tracked files | Resolved: swept from the shipping tree |
| 3 | P-3: aggregates and per-database sizes in research manifests | Resolved: research folder removed and gitignored |
| 4 | Full test suite after the doc and license changes | Resolved: 549 passed, 0 skips |
| 5 | Repository name and description | Resolved: "Wechat-MCP". The review did not assess WeChat terms-of-service or local law on circumventing technical protections, and `README.md` carries a disclaimer |
| 6 | GitHub private vulnerability reporting and secret scanning | Open (GitHub repository settings, maintainer action) |

Recommended soon after release:

7. H-1(a)(b): delete residual capture key copies after verification, and
   exclude the state directory from backups. H-2: drop the write-only decoded
   cache.
8. H-4: run one dependency audit and record it. H-6: add the CLI catch-all.

## 6. Verified safe (positive findings)

- **Read-only source access.** `sqlite3_open_v2` flag `1` (read-only) at
  `source/reader.py:65`; `PRAGMA query_only=ON`; an authorizer
  (`reader.py:97-107`) denies everything except SELECT/READ/recursive, an
  allowlisted function set and read pragmas, and ATTACH/DDL/DML fall through to
  `SQLITE_DENY`. Extensions disabled (`reader.py:90`). Statements must be single
  (`multiple_or_empty_statements`), parameter counts are checked, results are
  bounded (200 rows, 8 MiB, 5 s deadline via progress handler).
- **SQL identifiers.** The only interpolated identifiers come from
  `schema.quote_table`, which whitelists against observed tables and escapes
  (`adapters/schema.py:35-38`); `archive/store.py` and `search/repository.py`
  interpolate only fixed internal constants (`KINDS`, a fixed field tuple, a
  whitelisted filter key). All values are bound.
- **FTS query handling.** Query text is normalised and converted to hex-coded
  quoted tokens (`search/tokenize.py:59-65`), so user input cannot reach FTS5
  syntax; terms are bounded by `MAX_QUERY_CHARACTERS=256`; a 2-second progress
  deadline applies to archive queries.
- **Cursors.** HMAC-SHA256 with the private identity key, constant-time compare,
  domain-separated prefix, bound to method, filters, limit and generation
  (`search/cursor.py`); tampering or reuse across queries yields
  `invalid_cursor`.
- **Path traversal and symlinks.** Attachment paths are rebuilt from validated
  components (`attachments/mapping.py`: month regex, name with no separators),
  and opened with a per-component `O_NOFOLLOW` descriptor walk under
  `msg/file|attach`, regular files only, identity re-checked around every read
  (`attachments/resolver.py:21-110`). Source databases go through
  `reject_symlinks`/`..` checks (`discovery.py:35-41`).
- **Private-state I/O.** Owner-only dir/file checks on file descriptors,
  `O_NOFOLLOW`, temp file + `fsync` + `os.replace`, `flock` serialisation;
  state inside the repository is refused; 8 MiB state-read cap
  (`core/config.py`).
- **Error surface.** `BridgeError(code)` throughout; `from None` at boundaries;
  MCP maps unexpected exceptions to `internal_error`, and a middleware
  normalises SDK validation errors so caller values are never echoed
  (`mcp/server.py:83-108`). Dataclass fields holding paths, keys and usernames
  use `repr=False`.
- **MCP surface.** stdio only; no network listener; nine read-only tools with
  `additionalProperties: false`, strict pydantic types and regex-checked IDs;
  attachment responses bounded to 8 MiB source / 12 MiB wire; resource template
  re-validates IDs through `valid_id` in the service layer.
- **Parsers.** Protobuf parser bounded to 1 MiB/4096 fields with strict varints
  (`adapters/wire.py`); XML parse rejects DOCTYPE/ENTITY, caps depth 32 and 4096
  nodes, uses stdlib expat (no external entity fetch); zstd decompression capped
  at `MAX_PAYLOAD+1` with a 2^23 window; image decode enforces format, frame
  count, 40 MP and storage budgets with decompression-bomb warnings as errors.
- **Cryptographic hygiene.** Keys compared with `hmac.compare_digest`; key
  material never in logs or `repr`; capture helper prints counts only
  (`lldb_capture.py` emits fixed codes); stable IDs are domain-separated HMACs.
  MD5 usage is dictated by the WeChat format, not a trust boundary.
- **Subprocesses.** Every `subprocess` call uses an argument list (no
  `shell=True`), absolute system binaries, timeouts, and no user-controlled
  argv except validated UUID plan IDs. The LLDB helper launches only the signed
  copy, validates the PID/start-time/executable record before `SIGTERM`, and
  refuses to touch the original bundle (`setup/system.py`).
- **No dangerous primitives.** No `eval`, `exec`, `pickle`, `yaml.load`,
  `os.system`, network clients, or imports of `tests/` from `src/`.
  `scripts/build_sqlcipher.py` verifies a pinned SHA-256 before extracting with
  `filter="data"`.
- **Repository hygiene.** `.gitignore` excludes `.env*`, `keys*.json`, `*.db*`,
  images, `.private/` and `dist/`; no database, image or dump is tracked;
  fixtures use `fictional-*` names.

## 7. Residual risk

- Same-user compromise is out of the bridge's threat model: keys, the identity
  secret and the plaintext archive are readable by any process of that user (H-1).
- Untrusted content from contacts reaches the client's LLM context by design
  (H-5); safety rests on the client's own tool permissions.
- Pillow, libsqlcipher and the MCP SDK dependency tree parse or serve
  attacker-influenced data; versions are pinned but not audited here (H-4).
- WeChat format changes can break adapters; the bridge fails closed
  (`unsupported_build`), which is a safety property but a usability limit.
- The review did not inspect a live state directory or assess the legal
  exposure of key extraction.

## 8. Fixes that need maintainer approval

H-1(a) (touches `setup/`), H-1(d) (private-state layout), H-3 (touches
`setup/`), the optional H-5 `content_trust` field (MCP result shape), the H-4
audit in CI (tooling) and all GitHub settings changes (checklist item 6). The
other hardening items (H-2, H-6, H-7, H-8) stay within ordinary maintenance,
except where they alter documented behavior. H-2 changes `docs/architecture.md`.
