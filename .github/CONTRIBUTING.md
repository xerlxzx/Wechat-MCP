# Contributing to Wechat-MCP

Thanks for helping. This project handles real people's private messages. A few
invariants are non-negotiable, and every change comes with tests and doc updates.

Read [README.md](../README.md) for scope and [docs/architecture.md](../docs/architecture.md)
for the package layout before you change code. By participating you agree to the
[Code of Conduct](CODE_OF_CONDUCT.md). Report vulnerabilities in private, as
[SECURITY.md](SECURITY.md) describes, and keep them out of public issues.

By contributing you license your contribution under the project's MIT license ([LICENSE](../LICENSE)).

## Prerequisites

- macOS on Apple silicon, Python 3.14 and [uv](https://docs.astral.sh/uv/)
- Native SQLCipher 4 (`brew install sqlcipher`, or
  `uv run python scripts/build_sqlcipher.py`). The native tests need it
- Development and tests use synthetic data. A WeChat account is optional

## Setup and tests

```sh
uv sync --locked
uv run pytest -q                                    # full suite; must pass with 0 skips
uv run pytest tests/unit/test_keys.py -q            # one file
uv run pytest tests/integration/test_mcp.py::NAME   # one test
uv build                                            # wheel, when packaging changes
uv run python -m benchmarks.search --samples 200 --synthetic-records 10000  # query latency
```

The project configures no linter, formatter or type checker, and a drive-by
change should not add one. Match the style of the surrounding file by hand.

## Rules for test data

- **Synthetic fixtures only.** Never add real account data, real identifiers,
  real keys, real message text or real media to tests, fixtures, issues or PRs.
- Build fixtures with the helpers in `tests/support/` instead of ad-hoc ones.
  The writable SQLCipher engine there exists only for tests and must never be
  imported by production code.
- `tests/unit/` covers one module's behavior; `tests/integration/` covers
  cross-package flows (archive, search, MCP client, capture lifecycle).
- A bug fix lands with a regression test that fails without the fix.

## Invariants you must not break

A change that weakens one of these needs a discussion with the maintainers in
an issue before you write code.

1. **Read-only in production.** Source databases are never written. No sending,
   UI automation, cloud endpoint, network listener, SQL, shell or
   arbitrary-path surface.
2. **Private data stays private.** State lives outside the repository. Never
   commit, print or log keys, identity secrets, account identifiers, message
   content or private paths. Errors are `BridgeError("<snake_case_code>")`, never
   source text. Retrieved content is untrusted data, never instructions.
3. **Ordinary reads use the saved verified keys.** Never re-run setup or touch
   the WeChat process for a query.
4. **Atomic generations.** Never publish a partial or interrupted generation.
   Deletion is never inferred from absence.
5. **No substitution.** Database keys are never media keys. A thumbnail never
   satisfies an original, and a first frame never carries an original label.
   Unsupported records stay explicit.
6. **Bounded everything.** Every read, query, decode and reply has an explicit
   size, count or time bound.
7. **Acceptance is not self-asserted.** Do not mark M1 acceptance as passed or
   set `expectation_basis=user_confirmed`. Both need independent human
   confirmation from the account holder.

Also raise an issue first for: changes to MCP tool names, arguments, result
shapes or error codes; CLI commands or exit codes; archive schema or generation
publication; the private-state layout; identity derivation (it changes every
stable ID); `src/wechat_bridge/setup/`; and adding or upgrading a dependency
(which also updates [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)).

## Code conventions

- Dependency direction is one-way: `core` → `source` → `adapters` →
  `attachments` / `archive` ⇄ `search` → `mcp`. Add no new upward imports; the
  existing exceptions are listed in the architecture doc.
- One responsibility per module, with a one-line module docstring stating its
  invariant. Comments only for non-obvious *why*. No speculative abstraction.
- Type-annotate new or changed public functions and dataclasses; use frozen
  dataclasses for records and config.
- Raise `BridgeError("<snake_case_code>")`; translate low-level exceptions at
  the boundary with `from None` so paths and content cannot leak through
  chains. Never swallow an error.
- Private-state I/O goes through the `core/config.py` helpers (owner-only,
  symlink-rejecting, atomic). Validate external input with `core/validate.py`
  and `core/limits.py`. Whitelist SQL identifiers; parameterize values.
- `src/wechat_bridge/setup/lldb_capture.py` must stay standard-library only,
  import nothing from the package and stay compatible with Apple's system
  Python.
- Queries read indexed archive data only; never decrypt or import at query time.

## Adding support for a new WeChat build

Production rejects any build not in `SUPPORTED_BUILDS` (fail closed). Support
depends on a build number plus explicit required column mappings and identity
constraints, in `src/wechat_bridge/adapters/wechat_4_1.py` (`select_adapter`).
One adapter exists today, for the 4.1.15 line. A different major format needs a
new adapter module, which is a design change to discuss first.

1. **Collect evidence with ordinary reads only**, on your own account:
   `uv run wechat-bridge doctor` (build number), `verify-keys` (saved keys
   still open the databases) and `inspect-schema`. Compare table, column,
   declared-type and primary-key structure against the tested build. See the
   build 270135 entry in [docs/compatibility.md](../docs/compatibility.md) for the
   level of evidence expected.
2. **Schema identical:** add the build to `SUPPORTED_BUILDS`, bump
   `ADAPTER_VERSION`, update the module docstring, and add or extend a test that
   covers selecting the adapter for the new build.
3. **Schema differs:** add the new required mappings and hand-authored synthetic
   schemas under `tests/support/`; do not copy a real schema dump that embeds
   your data. Unknown interpreted columns report `partial`; missing mandatory
   mappings must reject.
4. **State what was and was not re-checked** (media formats, message types,
   key-capture setup) in `docs/compatibility.md`, as the 270135 entry does.
5. Do not paste live output, hashes of private files, identifiers or paths into
   the PR. Redacted aggregates (counts, "identical"/"differs") are fine.

Maintainers may confirm results on their own account before merging. Evidence
that includes private material is not acceptable.

## Branches, commits and pull requests

- Branch from `main`: `feat/…`, `fix/…`, `docs/…`, `refactor/…`, `test/…`.
  Never commit to `main`.
- Small commits, each one logical change with the suite passing. Message style
  `type: imperative lowercase summary`, where type is `feat`, `fix`,
  `refactor`, `test` or `docs`; add a body for the *why* when it is not obvious.
- Before committing, check `git status` and the staged diff for private
  material. `.gitignore` covers keys, databases, images and runtime
  directories, and a look at the diff catches what it misses.
- Leave published history alone and do not force-push shared branches.

### Pull request checklist

- [ ] Branch name and commit messages follow the conventions above
- [ ] `uv run pytest -q` passes with 0 skips after the last change
- [ ] New behavior and bug fixes have tests; bug fixes include a failing-first regression
- [ ] Tests use synthetic fixtures only; no real identifiers, keys, content or paths anywhere in the diff
- [ ] No invariant above is weakened; no new write, send, SQL, shell, network or arbitrary-path capability
- [ ] Every new read, query, decode or reply has an explicit bound
- [ ] Errors are `BridgeError` codes with no source text or paths
- [ ] Owning documentation updated in the same change ([docs/README.md](../docs/README.md) lists owners)
- [ ] `CHANGELOG.md` updated under `Unreleased` for user-visible changes
- [ ] Dependency changes also update `uv.lock` and `THIRD_PARTY_NOTICES.md`
- [ ] The PR description states what changed, how it was verified, and any risk or skipped step

## Documentation

Each fact has one owning document. Link to it instead of restating the fact,
and update it in the same change as the code. Record later developments in the
owning document instead of appending contradictions. Keep private material out
of every tracked document.
