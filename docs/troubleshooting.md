# Troubleshooting

Find what you saw, read what it means, and take the listed step. The CLI prints
errors as `{"error": "<code>"}`. MCP tools return the same code with `isError`
set. Source: `BridgeError` in `src/wechat_bridge/core/errors.py`.

One rule covers every row: **an ordinary query never needs setup.** Search,
refresh and `serve` use the keys already saved in `<state-dir>/keys.json`. Run
`setup` only when a row below says so, and only after `verify-keys` fails.
[setup.md](setup.md) holds the procedure.

`<state-dir>` is `~/Library/Application Support/WechatBridge` unless you set
`--state-dir` or `WECHAT_BRIDGE_STATE_DIR`.

## Start here

Two read-only commands give most answers:

```sh
uv run wechat-bridge doctor          # environment, WeChat version and build
uv run wechat-bridge archive-status  # generation, coverage, freshness
```

`archive-status` prints counts and no message text. The MCP `status` tool
returns the same fields plus the saved key count.

## Setup and key errors

| You see | What it means | Safe next step |
| --- | --- | --- |
| `invalid_private_state` | `config.json` is missing or unreadable. | Run `uv run wechat-bridge discover`. It reads no messages. |
| `setup_required` | `keys.json` does not exist. No capture has finished on this Mac. | Run the setup procedure once. If you had keys before, stop: do not recreate the file, and see [setup.md](setup.md#when-to-run-setup). |
| `key_coverage_incomplete`, or `verify-keys` exits 2 with `"ready": false` | A required database has no verified key. WeChat may have created a new message shard. | Run `verify-keys` again and read `issues`. If a required database still shows `missing_key`, run setup once. Saved keys stay in place. |
| `invalid_key_manifest` | `keys.json` failed validation. | Do not edit or delete it. Stop and report. Deleting it forces a new capture. |
| `unsupported_build` | The installed WeChat is not 4.1.15 build 270102 or 270135. | Wait for a project update. Setup cannot fix this and no downgrade is needed. See [compatibility.md](compatibility.md). |
| `source_version_unavailable` | The bridge could not read `/Applications/WeChat.app/Contents/Info.plist`. | Check that WeChat is installed in `/Applications`, then run `doctor`. |
| `unsupported_schema` | The database layout differs from the tested build. | Run `inspect-schema` and report the code. Do not run setup. |
| `setup_prerequisites_missing` | The Mac is not arm64, or the command runs as root. | Run `setup prepare` as your normal user on an Apple silicon Mac. |
| `setup_command_failed` | An Apple tool (`lldb`, `codesign`, `ditto` or the system `python3`) failed. | Run `xcode-select --install`, then start again from `setup prepare`. |
| `client_exit_required` | A WeChat process is running. | Quit WeChat yourself (Cmd+Q), confirm with `pgrep -x WeChat`, then rerun `setup run`. Do not kill the process. |
| `setup_plan_stale`, `invalid_setup_plan` | The plan expired after one hour, WeChat or its databases changed, or the plan id is wrong. | Run `setup prepare` again and use the new `plan_id`. |
| `setup_busy` | Another `setup run` or `setup recover` holds `.setup.lock`. | Wait for it to finish, then retry. The kernel frees the lock when the holder exits. |
| `setup_recovery_required` | An earlier plan never finished recovery. | Run `setup recover --plan-id <that plan>`. The plan id is in `<state-dir>/setup/active.json`. |
| `setup_copy_exists`, `invalid_setup_copy` | The plan directory already holds a copy of the app. | Run `setup recover` for that plan, then `setup prepare`. |
| `process_inspection_unknown` | The process check failed. | Retry once. If it repeats, stop and report. |
| `status: capture_timeout`, `capture_process_ended`, `capture_failed`, `source_changed`, `partial_coverage` | `setup run` ended without full coverage. | See the status table in [setup.md](setup.md#failure-codes-and-actions). Retry no more than once, and never in a loop. |
| `recovery_status: manual_restore_required` | The temporary WeChat copy was not proved gone. | Quit the extra WeChat window yourself, then run `setup recover --plan-id <id>`. |

## Discovery and source errors

| You see | What it means | Safe next step |
| --- | --- | --- |
| `account_selection_required` | More than one account directory exists. | Stop. This needs an explicit private configuration; see [architecture.md](architecture.md#runtime-state). |
| `source_not_found` | No WeChat account directory exists on this Mac. | Sign in to WeChat once, then run `discover`. |
| `required_source_missing` (in `issues`) | WeChat has not created a required database for this account. | Stop and report. Opening WeChat and loading your chats may create the files, after which `discover` can run again. |
| `source_inventory_limit` | More than 4096 entries sit under `db_storage`. The walk stops rather than run unbounded. | Stop and report. The limit is in `source/discovery.py` and `archive/reconcile.py`. |
| `source_symlink` | A symlink sits inside the WeChat data folder. The bridge refuses to follow it. | Find and remove the symlink only if you created it. Otherwise report it. |
| `source_unreadable`, `source_header_invalid` | A database or the data folder cannot be read, or a file has a truncated header. | Check that macOS has granted your terminal access to WeChat's app data, then retry. |
| `source_busy`, `query_timeout`, `result_too_large` | WeChat held a lock longer than 2 seconds, a query ran past 5 seconds, or a result passed 8 MiB. | Retry. If it repeats, quit WeChat and run `archive-refresh` again. |

## Archive and refresh errors

| You see | What it means | Safe next step |
| --- | --- | --- |
| `archive_refresh_in_progress` | Another refresh (the `serve` watcher or a manual run) holds `.archive-import.lock`. | Wait a minute and run `archive-status`. |
| `archive-refresh` prints `"status": "failed"` with `source_changed` | WeChat wrote data while the import ran. No partial generation is published. | Run `archive-refresh` again. The previous generation stays in use. |
| `source_shard_missing` | A message database seen before is gone. The bridge does not infer deletion from absence. | Stop and report. Do not delete `archive.sqlite3` or `inventory.json`. |
| `message_table_mapping_incomplete` | The source holds a message table that no conversation maps to. Publication stays blocked. | Stop and report. The prior generation remains active. |
| `archive_account_mismatch` | The archive was built for a different account than the one discovered now. | Stop and report. The watcher waits an hour before it retries this code. |
| `archive_refresh_failed`, `operation_cancelled` | A refresh failed for an unlisted reason or you interrupted it. | Run `archive-refresh` again. A new run reclaims an abandoned generation. |
| `archive_not_ready` | No complete generation exists yet. | Run `archive-refresh`, then restart `serve` so its watcher starts. |
| `search_not_ready` | The archive exists but the search index is not built. | Run `uv run wechat-bridge search-index`. |
| `archive_query_failed` | The archive query failed or passed its 2 second deadline. | Retry with a narrower filter or a smaller `limit`. |

## MCP tool errors

Codes and causes are listed in [mcp.md](mcp.md#errors-and-trust). The ones
people ask about most:

| You see | What it means | Safe next step |
| --- | --- | --- |
| `cursor_expired` | The cursor's generation was removed. The bridge keeps the current and previous generation. | Repeat the first request without a cursor. |
| `invalid_cursor` | The cursor was altered, or you changed a filter or `limit` between pages. | Start again without a cursor and keep the filters fixed while paging. |
| `invalid_arguments`, `invalid_filter` | An unknown argument, or a `kind`, `type`, `representation` or `availability` outside the allowed values. | Check the argument table in [mcp.md](mcp.md#tools). |
| `content_too_large` | A reply or attachment passed a size bound (8 MiB attachment maximum). | Lower `max_bytes`, or accept that the bridge has no chunked transfer. |
| `internal_error` | An unexpected failure. The bridge hides the cause so no private path or text leaks. | Run `doctor` and `archive-status`, then retry once. Report it with the command you ran, and no message content. |
| The assistant lists no `wechat_bridge` tools | The client has not loaded the server, or the path moved. | Run `claude mcp list` or `codex mcp list`, check the path to `.venv/bin/wechat-bridge`, and start a new session. |

## Symptoms without an error code

| You see | What it means | Safe next step |
| --- | --- | --- |
| Results look old | `archive-status` shows `"stale": true`. That value is true when no generation is active, the source files changed since publication, or the last refresh failed. | Read `last_refresh_error` and `source_change_detected`. Run `archive-refresh`. With `serve` running, the watcher reconciles every 60 seconds. |
| `serve` runs but never refreshes | No generation existed when `serve` started, so no watcher started. Or a failure that only a key change can clear is blocking the watcher. | Run `archive-refresh`, then restart `serve`. Blocked codes retry when `keys.json` changes or after an hour. |
| Search returns no results | Matching is a literal substring, after Unicode normalization and case folding. It is not fuzzy or semantic. The query is at most 256 characters. | Try a shorter word. Check the filters (`conversation_id`, `start`, `end`, `type`). Check `coverage` in `archive-status` for missing message tables. |
| A message you saw on your phone is absent | Messages that never synced to this Mac do not exist locally. `complete_phone_history` is always `false`. | Nothing to fix. The bridge reports what the Mac holds. |
| An image arrives as a labeled thumbnail or source bytes | The original is WXGF (unsupported), animated, HEVC, or not downloaded to this Mac. The bridge never swaps a thumbnail in for an original. | Read `availability` and `diagnostic_code` on the attachment. See [compatibility.md](compatibility.md). |
| WeChat updated and nothing works | The new build may be unsupported. | Run `doctor`. If `wechat_build` is not 270102 or 270135, wait for a project update. |

## What not to do

- Do not delete `keys.json`, `identity.json` or `archive.sqlite3` to "reset".
  Deleting `identity.json` changes every stable ID and invalidates every cursor.
- Do not quit WeChat by process name, use `sudo`, or re-sign the installed app.
- Do not paste keys, `config.json`, or message content into an issue. See
  [SECURITY.md](../.github/SECURITY.md).
