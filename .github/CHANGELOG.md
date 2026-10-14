# Changelog

All notable changes are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project aims
to follow [Semantic Versioning](https://semver.org/) once it reaches 1.0.

## [Unreleased]

## [0.1.1] - 2026-10-15

### Changed

- `discover` stops with `source_inventory_limit` after 4096 entries under
  `db_storage`, matching the reconcile walk.
- The CLI prints `{"error": "internal_error"}` and exits 2 on unexpected
  exceptions instead of a traceback.
- Build backend pinned to `hatchling==1.32.4`.

### Removed

- The write-only decoded-image cache (`state/decoded/`). Existing folders can
  be deleted.

## 0.1.0 - Initial release (not published as a tag)

First public release. Experimental. Source acceptance remains blocked (see
Known limitations).

### Added

- Source access: private discovery and redacted diagnostics (`doctor`,
  `discover`), a read-only, query-only, authorizer-restricted SQLCipher reader
  with WAL coverage, key verification and partial-coverage reporting
  (`inspect-schema`, `verify-keys`).
- One-time, explicit, recoverable key capture (`setup prepare`, `setup run`,
  `setup recover`).
  After keys are verified and recovery completes, setup deletes its raw-key
  copies (`captured.json`, `candidates.json`) and reports `key_copies_removed`.
- Adapter for WeChat 4.1.15 builds 270102 and 270135 on macOS 27 (arm64):
  messages (text, Zstandard text, image, file, reply), cross-shard replies,
  contacts, groups and locally observed membership. Unsupported or unmapped
  records are retained explicitly.
- Private, owner-only archive published in atomic, complete generations, with
  bounded full reconciliation, a debounced watcher and freshness/coverage
  diagnostics (`archive-refresh`, `archive-status`, `archive-watch`).
- Generation-scoped literal Unicode/Chinese substring search with bounded
  queries and opaque authenticated paging cursors (`search-index`, `search`).
- Stdio MCP server with nine read-only tools (`status`, `list_conversations`,
  `list_contacts`, `get_group`, `search_messages`, `get_messages`,
  `get_context`, `list_attachments`, `get_attachment`) and bounded, verified
  attachment delivery (`serve`). Concurrent `serve` sessions coordinate on a
  per-state-directory watcher lock.
- Attachment resolution for files (size and digest verified) and static
  JPEG/PNG/WebP, legacy XOR and V1/V2 DAT images. Each result carries an
  original or thumbnail label, and one never stands in for the other.
- Source-acceptance proof tool (`prove`) and a query-latency benchmark.
- Project documentation, contributing guide, security policy and code of
  conduct.

### Known limitations

- Live source acceptance is blocked on a supported full original image and
  independent confirmation of the proof samples.
- WXGF image originals are not decoded; they are returned only as labeled
  source bytes. Animated images and HEVC are unsupported.
- Attachment delivery is capped at 8 MiB per call; no chunked transfer.
- Some attachment metadata variants are unmapped and reported as unsupported.
- Setup leaves the `WeChat.disabled` app copy, and setups from before the
  cleanup change may keep raw-key copies (`setup/<plan>/captured.json` and
  `candidates.json`) in the private state directory. Exclude that directory
  from backups. See [SECURITY.md](SECURITY.md).
- The bridge supports the tested WeChat line and fails closed on other builds
  with `unsupported_build`.

[Unreleased]: https://github.com/xerlxzx/Wechat-MCP/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/xerlxzx/Wechat-MCP/releases/tag/v0.1.1
