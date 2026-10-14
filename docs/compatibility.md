# Compatibility and observed coverage

The adapter supports one tested environment and an explicit list of builds.
Production rejects any other installed build with `unsupported_build`. A new
build needs new evidence, plus fixtures and an adapter change when its schema
differs.

## Tested environment

| Component | Version |
| --- | --- |
| macOS | 27.0, arm64 |
| WeChat | 4.1.15, builds 270102 and 270135 |
| Python | 3.14.6 |
| Native SQLCipher / SQLite | 4.19.0 community / 3.53.4 (checksum-verified source build) |
| Archive SQLite | 3.53.3 (Python's bundled engine) |
| MCP SDK | `mcp==2.3.0` (pinned) |
| Image runtime | Pillow 12.3.0, cryptography 50.0.2 (locked) |

`select_adapter` (`src/wechat_bridge/adapters/wechat_4_1.py`) gates support in
three steps:

1. The installed app's `CFBundleShortVersionString` must be `4.1.15`, and its
   `CFBundleVersion` must be in `SUPPORTED_BUILDS` (`270102`, `270135`).
   Otherwise the adapter raises `unsupported_build`.
2. The required tables and columns must match the explicit mappings, and the
   rowid and primary-key identity constraints must hold. Otherwise the adapter
   raises `unsupported_schema`.
3. Columns the adapter does not interpret report `partial` instead of
   rejecting.

The gate stops short of a literal account-wide schema fingerprint, because
dynamic conversation tables make one non-portable.

### Build 270135 (14 October 2026)

WeChat auto-updated from 270102 to 270135 (still 4.1.15), and refresh failed
closed with `unsupported_build`, leaving the last generation active. The
adapter now accepts the build on this evidence, gathered with ordinary reads:

- All required databases: every table, column, declared type and primary key
  matched the schemas recorded under 270102 (only the observation time
  differed). The comparison ran the package's own `inspect_schema`.
- `select_adapter`'s required-mapping and rowid checks pass on the live schemas.
- Saved keys still verify for every required database, with no setup rerun.

Not re-checked for 270135: media formats, message-type coverage and the LLDB
setup capture. A future setup on this build needs its own revalidation.

## Format support

| Area | Supported | Unsupported or explicit |
| --- | --- | --- |
| Messages | Text, Zstandard text, image, file, reply | System variants and unknown types stay as unsupported records |
| Replies | Local originals resolve, including across shards | Unresolved references stay explicit; quoted names are content, not identity |
| Groups | Recorded membership and group nicknames | Groups without recorded membership report it as unsupported |
| Images | Static JPEG/PNG/WebP, legacy XOR, verified V1/V2 DAT | Animated, WXGF, HEVC; never replaced by a first frame |
| Files | Any type, retrieved with size/MD5/SHA-256 and mutation checks | No document text extraction |
| Sources | `contact/contact.db`, `session/session.db`, `message/message_resource.db`, `hardlink/hardlink.db` and every `message/message_<n>.db` shard, each with its own key | Auxiliary KVDB unhandled; no atomic account-wide snapshot |

## Observations from live runs

These are dated observations from one maintainer machine and one account. They
are not independent ground truth, and counts differ from account to account.

### 8 October 2026 (build 270102)

| Check | Result |
| --- | --- |
| Keys | Every discovered database verified at schema-read scope, including all required ones (the four fixed databases and the message shards) |
| Archive | Messages, contacts, conversations, memberships, replies and attachment references imported in one complete generation |
| Local coverage | Only conversations with a local message table had messages. Others had none locally |
| Ordinary files | All retrieved with verification |
| Images | Thumbnails decoded. Some originals were not local, and the local originals were unsupported WXGF |
| MCP client | The SDK stdio client exercised all nine tools, binary and resource delivery, bounds and reconnect |
| Query latency | Warm p95 about 3 ms on the live archive and about 32 ms on 10,000 synthetic records |

A later refresh observed `source_changed` during the scan. The bridge kept the
previous generation and marked it stale, as designed.

The database count rose afterwards because WeChat added message shards. The
required count equals four plus the number of message shards, so it grows as
WeChat adds shards.

### 13 October 2026

Measured against a live archive of roughly ten thousand messages:

- A full refresh took about 30 s and was CPU-bound in per-message attachment
  mapping.
- Warm query p95 stayed under 100 ms.
- Larger archives and sustained synchronization lag are unmeasured.

Reproduce the query-latency numbers with the synthetic benchmark:

```sh
uv run python -m benchmarks.search --samples 200 --synthetic-records 10000
```

## Known gaps

- **Live acceptance has not passed.** `wechat-bridge prove` needs a supported
  full original image to pass its gate, and the prepared sample expectations
  await independent confirmation. A thumbnail cannot satisfy the original-image
  check, and source self-consistency is not ground truth.
- Some iPhone history is missing from Mac WeChat. The bridge mirrors the local
  Mac history and has no iPhone backup importer.
- Key verification covers a schema read, not a full-page integrity scan.
- Archive size and synchronization lag beyond the measurements above are
  unmeasured.
