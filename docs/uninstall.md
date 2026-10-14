# Uninstall

Removing Wechat-MCP takes four steps: unregister the server, delete the private
state directory, delete the project folder, and clear any backup copy. None of
it touches WeChat or its data. The bridge only ever read from them.

Paths below use `<state-dir>` for the private folder. The default is
`~/Library/Application Support/WechatBridge`. If you set `--state-dir` or
`WECHAT_BRIDGE_STATE_DIR`, use that path instead.

## 1. Remove the MCP registration

Close any client session that uses the server. Each client starts its own
`serve` process.

Claude Code:

```sh
claude mcp remove wechat_bridge
```

Without `--scope`, Claude Code removes the server from whichever scope holds
it. The README registers it with `--scope local`, which Claude Code keeps per
folder, so run the command from the folder where you added it or pass
`--scope local|user|project`. Confirm with `claude mcp list`.

Codex:

```sh
codex mcp remove wechat_bridge
codex mcp list
```

The README shows `codex mcp add` and `codex mcp list`. `remove` is the matching
subcommand of the same CLI.

If you registered a client by hand with a JSON definition, delete the
`wechat_bridge` entry from that client's configuration file.

## 2. Delete the state directory

The directory holds your keys, the identity secret and a searchable copy of
your history. Deleting it is the step that removes your data.

```sh
rm -r "<state-dir>"
```

| Path | Contents |
| --- | --- |
| `keys.json` | Verified database keys |
| `identity.json` | Identity secret |
| `archive.sqlite3` (and `-wal`, `-shm`) | Archive and search index |
| `config.json`, `inventory.json`, `adapter-schemas.json` | Account selection and database inventory |
| `setup/` | Setup plans, journals and capture logs |
| `proofs/` | Private proof evidence, if you ran `prove` |
| `.lock`, `.archive-import.lock`, `.archive-watcher.lock`, `.setup.lock` | Lock files |

[architecture.md](architecture.md#runtime-state) lists the full layout.

### Leftovers inside the state directory

Deleting the directory removes all of these. Read this list only if you keep
the directory and clean it up by hand.

- **`setup/<plan-id>/WeChat.disabled`.** The ad-hoc-signed copy of WeChat that
  setup disabled after capture. It is a full app bundle, so it takes real disk
  space. Setup keeps it as a recovery artifact. Delete it after `setup recover`
  reports `recovered` for that plan.
- **`setup/<plan-id>/captured.json` and `candidates.json`.** Raw key copies
  from setups that older versions ran. Current versions delete both once the
  keys verify and recovery completes. Delete them by hand if they remain.
- **`decoded/`.** A decoded-image cache that earlier versions wrote. Version
  0.1.1 removed the cache, so the folder is safe to delete.

### Folders outside the state directory

- `~/.local/share/wechat-bridge/sqlcipher-4.19.0` exists only if you ran
  `scripts/build_sqlcipher.py`. Delete `~/.local/share/wechat-bridge`.
  Homebrew's `sqlcipher` lives elsewhere; `brew uninstall sqlcipher` removes it
  if nothing else needs it.
- The project folder, for example `~/Wechat-MCP` with its `.venv`. Delete it
  after step 1, because the registered command pointed into it.

## 3. Clear the Time Machine exclusion

If you excluded the state directory from backups, macOS stored the exclusion
against that path. Remove it with:

```sh
tmutil removeexclusion "<state-dir>"
```

The command can fail once the directory no longer exists. A stale exclusion is
harmless, so skip it in that case.

Backups made before you added the exclusion may still hold the keys and the
archive. Delete those copies in Time Machine if you want the history gone from
your backups too.

## 4. Confirm nothing remains

```sh
claude mcp list                          # no wechat_bridge entry
codex mcp list                           # no wechat_bridge entry
ls -d "<state-dir>"                      # No such file or directory
pgrep -fl wechat-bridge                  # no output
ls -d ~/.local/share/wechat-bridge       # absent unless you built SQLCipher
```

If `pgrep` prints a process, quit the client that started it. Do not run
`setup`, `kill` or any WeChat command to finish the removal.

## What stays

- WeChat and its data folder. The bridge never wrote to them, and it never
  modified or re-signed `/Applications/WeChat.app`.
- Whatever your assistant already received. A model that read messages in an
  earlier session keeps what it saw according to that client's own policy.
- If the keys or identity secret may have leaked, follow
  [SECURITY.md](../.github/SECURITY.md#if-private-material-leaks).
