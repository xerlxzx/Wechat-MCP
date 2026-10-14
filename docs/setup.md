# First-time key setup

Setup captures WeChat's per-database SQLCipher keys once and saves them in the
private state directory. Every ordinary read afterwards uses the saved keys.

Supported environment: macOS on Apple silicon (arm64), WeChat 4.1.15 builds
270102 and 270135 (`SUPPORTED_BUILDS` in `src/wechat_bridge/adapters/wechat_4_1.py`).
The number of required databases is four fixed databases plus one per message
shard, so it differs between accounts (see [compatibility.md](compatibility.md)).

## Rules for an AI agent running this procedure

1. Run `setup run` only after the person you are helping confirms, in their own
   words, that they understand it launches a temporary signed copy of WeChat
   and that they have quit WeChat.
2. Never quit, kill or relaunch WeChat yourself. Ask the person to quit it.
   Do not use `kill`, `pkill`, `killall` or `sudo`. Setup never needs any of them.
3. If the person cannot or will not quit WeChat, stop and report. Setup has no
   other path.
4. Do not run setup again while `verify-keys` passes (exit 0, `"ready": true`).
5. Never print, copy or commit `keys.json`, `identity.json` or capture logs.
6. Never delete or overwrite `keys.json` to retry setup.

## When to run setup

| Situation | Action |
| --- | --- |
| `wechat-bridge verify-keys` exits 0 with `"ready": true` | Do not run setup. Go to [After setup](#after-setup). |
| `setup_required` (no `keys.json`) | Run setup. |
| `key_coverage_incomplete` or `verify-keys` exits 2 | Run `verify-keys` once more. If a required database still has no verified key (WeChat created a new message shard, for example), run setup. |
| `unsupported_build` | Do not run setup. The installed build is not supported yet. Setup cannot fix this. |

## Prerequisites

| Requirement | Check |
| --- | --- |
| Apple silicon Mac, macOS, not running as root | `uname -m` prints `arm64` |
| WeChat 4.1.15 at `/Applications/WeChat.app`, signed in on this Mac | `uv run wechat-bridge doctor` shows `wechat_version` and `wechat_build` |
| Apple Command Line Tools (provide `/usr/bin/lldb` and a `/usr/bin/python3` that imports `lldb`) | `xcode-select -p` prints a path; install with `xcode-select --install` |
| SQLCipher native library (Homebrew `sqlcipher`, or built with `scripts/build_sqlcipher.py`) | `doctor` shows `"sqlcipher_available": true` |
| Project environment | `uv sync --locked` (installs Python 3.14 if missing; `requires-python` is `>=3.14,<3.15`) |
| A phone nearby | The temporary WeChat window may ask for login confirmation on the phone |

## Procedure

Run every command from the repository root.

1. Install and check the environment.

   ```sh
   uv sync --locked
   uv run wechat-bridge doctor
   ```

   `doctor` prints one JSON object with `python_version`, `architecture`,
   `sqlcipher_available`, `configured`, `wechat_version` and `wechat_build`.
   Continue only when `sqlcipher_available` is `true`, `wechat_version` is
   `4.1.15` and `wechat_build` is `270102` or `270135`.

2. Discover the account and create the configuration.

   ```sh
   uv run wechat-bridge discover
   ```

   This is the command that writes `config.json` (and `inventory.json`) in the
   state directory. Every later command fails with `invalid_private_state`
   until it has run. It prints counts only: `database_count`, `required_count`,
   `wal_count`, `replaced_count` and `issues`. If `issues` contains
   `required_source_missing`, WeChat has not created its databases for this
   account yet. Stop and report.
   `account_selection_required` means more than one account directory exists,
   and `source_not_found` means none does. Stop and report both.

3. Check whether setup is needed.

   ```sh
   uv run wechat-bridge verify-keys
   ```

   With no `keys.json` it prints `{"error": "setup_required"}` and exits 2.
   If it prints `"ready": true` and exits 0, stop here and go to
   [After setup](#after-setup).

4. Prepare a plan. This changes nothing on the Mac apart from writing the
   plan under `<state-dir>/setup/<plan-id>/`.

   ```sh
   uv run wechat-bridge setup prepare
   ```

   It prints one JSON object. Save its `plan_id`; the plan expires after one
   hour (`expires_at` is a Unix timestamp):

   ```json
   {"plan_id": "<uuid>", "status": "prepared", "expires_at": 1760000000.0,
    "requires_normal_client_exit": true, "may_require_phone_confirmation": true,
    "modifies_installed_application": false, "capture_timeout_seconds": 150,
    "restore_original": true}
   ```

   `restore_original` is `true` when WeChat was running during `prepare`. Setup
   then reopens the original app when it finishes.

5. Stop and get confirmation. Tell the person what happens next (see
   [What `setup run` does](#what-setup-run-does)), and ask them to quit WeChat
   (Cmd+Q, or WeChat menu, Quit). Do not continue until they confirm.

6. Confirm WeChat is closed.

   ```sh
   pgrep -x WeChat; echo "exit=$?"
   ```

   `exit=1` with no PID printed means closed. If a PID prints, WeChat is
   still running (check the menu bar and the Dock for a hidden window). Ask the
   person to quit it again. If they cannot, stop and report. Do not kill the
   process. `setup run` repeats this check and refuses to start with
   `client_exit_required` while any `WeChat` process exists.

7. Run the capture.

   ```sh
   uv run wechat-bridge setup run --plan-id <plan_id>
   ```

   It takes up to about three minutes. A WeChat window opens; if it asks for
   confirmation, tell the person to confirm on their phone or press Enter
   Weixin. It prints `{"status": ..., "recovery_status": ..., "key_copies_removed": ...}` and exits 0 only
   for `verified` with `recovered`.

8. Verify the result.

   ```sh
   uv run wechat-bridge verify-keys
   ```

   It prints one JSON object and exits 0 only when `ready` is `true`:

   ```json
   {"ready": true, "discovered_count": 12, "required_count": 5,
    "verified_count": 12, "verification_scope": "schema_read",
    "full_page_integrity_checked": false,
    "databases": [{"database_id": "<id>", "required": true, "status": "verified"}],
    "issues": []}
   ```

   `ready` depends on required databases only. Each `status` is `verified`,
   `missing_key`, `replaced`, `key_or_format_mismatch`, `unsupported` or
   `unreadable`. The counts above are an example; yours differ.

## What `setup run` does

1. Verifies the plan is unexpired, the installed signature is unchanged, the
   database identities still match, and WeChat is not running.
2. Copies `/Applications/WeChat.app` into the plan directory as
   `WeChat-trial.app` and signs that copy ad hoc.
3. Launches only the copy, under Apple LLDB, for up to 150 seconds. The copy
   uses the same account data as the original; it is not an isolated sandbox.
4. Accepts captured material only when it authenticates a page of a selected
   database, then verifies each candidate with the native read-only SQLCipher
   reader. It publishes only verified keys and merges them into any existing
   `keys.json`.
5. Closes the copy it started, disables the temporary bundle, verifies the
   installed signature, and reopens the original app if `restore_original` was
   `true`.

Setup never re-signs the installed bundle, changes SIP, uses sudo, stores an
administrator password, sends messages or drives the UI. Candidate keys, the
journal, the process identity and capture logs stay under the private state
directory with mode 0600.

The plan's concrete commands are:

- `codesign --verify --deep --strict /Applications/WeChat.app`, then
  `codesign -dvvv` for the signature fingerprint.
- `ditto /Applications/WeChat.app <plan-directory>/WeChat-trial.app`.
- `codesign --force --deep --sign - <plan-directory>/WeChat-trial.app`, then
  signature verification of the copy.
- `/usr/bin/python3 <checkout>/src/wechat_bridge/setup/lldb_capture.py --exe <copy>/Contents/MacOS/WeChat --db-root <account>/db_storage --out <plan-directory>/captured.json --owned <plan-directory>/owned.json --timeout 150`,
  with `PYTHONPATH` from `/usr/bin/lldb -P`.

The helper prints only capture counts and status, and no key appears in command
arguments.

### Key copies and cleanup

Capture writes `<plan-directory>/captured.json` (raw keys) and, after
verification, `<plan-directory>/candidates.json`. Ordinary reads use only
`keys.json` in the state root, so these two files are redundant once the keys
are verified.

`setup run` and `setup recover` delete both files when the plan has reached
both of these states:

- Full required coverage verified (`status: verified`).
- Recovery completed (`recovery_status: recovered`).

Both commands print `key_copies_removed`. It is `true` only if both files are
gone afterward. It is `false` when coverage is partial, keys are unverified,
recovery is incomplete (for example `manual_restore_required`), the plan is
stale, a file is a symlink or not a regular file you own, or deletion failed.
A deletion failure does not fail or roll back setup. `setup recover` retries the
deletion for a verified plan, and a repeat run is safe.

Setup removes nothing else. `keys.json`, `identity.json`, the archive, the
`WeChat.disabled` app copy and the other plan files stay in place. Exclude the
state directory from backups. Setups run before this change may still have the
two files; delete them by hand after the plan reports `verified` and
`recovered`. The [security review](security-review.md) tracks this as finding
H-1.

## Failure codes and actions

Codes print as `{"error": "<code>"}` (exit 2) when a check fails before
capture starts. Once `setup run` has started copying, a failure appears as a
`status` value instead, and recovery then runs automatically. Both forms exit 2.

| Code | Where | Action |
| --- | --- | --- |
| `invalid_private_state` | any command | `config.json` is missing or unreadable. Run `discover` (step 2). |
| `setup_prerequisites_missing` | `setup prepare` | The Mac is not arm64 or is running as root. Stop and report. |
| `setup_command_failed` | `setup prepare` (error), `setup run` (status) | An Apple tool failed: `/usr/bin/lldb`, `/usr/bin/python3` with `lldb`, `codesign` or `ditto`. Most often the Command Line Tools are missing. Run `xcode-select --install`, then retry from step 4. |
| `invalid_setup_plan` | `setup run`, `recover` | The plan id is malformed, missing, or for another app path. Use the exact `plan_id` that `prepare` printed, or run `prepare` again. |
| `setup_plan_stale` | `setup run` | The plan expired (one hour), WeChat updated, or the databases changed. Run `prepare` again (step 4). |
| `client_exit_required` | `setup run` (error or status) | WeChat is running. Ask the person to quit it, repeat step 6, rerun `setup run`. |
| `process_inspection_unknown` | `setup run` | The process check failed. Retry. If it repeats, stop and report. |
| `setup_busy` | `setup run`, `recover` | Another setup or recovery process holds the lock. Wait for it to finish. |
| `setup_recovery_required` | `setup run` | An earlier plan is unrecovered. Run `setup recover --plan-id <that plan>` first. |
| `setup_copy_exists`, `invalid_setup_copy` | `setup run` (status) | The plan directory already holds a copy. Run `setup recover` for that plan, then `prepare` again. |
| `unsupported_build` | `archive-refresh`, `inspect-schema`, `serve` watcher | See [When to run setup](#when-to-run-setup). Do not retry setup. |
| `status: partial_coverage` | `setup run` | Some required databases lack a verified key. Existing keys stay. Run `verify-keys`, then `setup prepare` and `setup run` once more. If it repeats, stop and report. |
| `status: capture_timeout` | `setup run` | 150 s passed with no keys. Ask the person to confirm any login prompt in the WeChat window. Prepare and run again. |
| `status: capture_process_ended` | `setup run` | The copy exited or crashed before it yielded keys. Prepare and run again once. If it repeats, stop and report. |
| `status: capture_failed` | `setup run` | Capture failed. Existing keys stay. Stop and report. Do not retry in a loop. |
| `status: source_changed` | `setup run` | The databases changed during capture (WeChat wrote data). Prepare and run again. |
| `recovery_status: manual_restore_required` | `setup run`, `recover` | The temporary client could not be proved owned or did not exit. Ask the person to quit the temporary WeChat window normally, then run `setup recover`. |
| `recovery_status: original_signature_changed` | `setup run`, `recover` | The installed app changed during setup. The installed client is not launched automatically. Ask the person to open WeChat themselves. Stop and report; do not retry setup. |

## Recovery

After an interruption, or any `recovery_status` other than `recovered`, run:

```sh
uv run wechat-bridge setup recover --plan-id <same-plan-id>
```

It prints `{"status": "recovered"}` and exits 0 on success. Recovery is
idempotent. It compares an owned process's start identity and executable before
it terminates anything, and it never kills a process by name. A
process-inspection error or timeout does not count as proof of exit.

An unrecovered plan blocks new plans (`setup_recovery_required`). The
`setup/active.json` file in the state directory names the plan that owns setup.

## After setup

1. Import the history into the private archive. The first import is explicit
   and can take a minute or more:

   ```sh
   uv run wechat-bridge archive-refresh
   uv run wechat-bridge archive-status
   ```

   `archive-refresh` prints `"status": "complete"` and exits 0. If it prints
   `"status": "failed"`, read its `error` code: `source_changed` means WeChat
   wrote data during the import (run it again), and
   `archive_refresh_in_progress` means another refresh is running (wait and
   retry). `archive-status` exits 0 when `ready_for_local_queries` is `true`.

2. Register the MCP server with your client. Use the exact commands in
   [mcp.md](mcp.md#launching).

3. Start a new client session so it loads the tool, then call the
   `wechat_bridge` `status` tool and check that it reports a ready archive.
   Do not report the connection as working until that call succeeds.

Do not run setup again while `verify-keys` passes. A WeChat update that keeps
the same build number and schema does not need it. A new build needs
`unsupported_build` to clear first (see [compatibility.md](compatibility.md)).
