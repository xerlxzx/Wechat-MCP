# Security policy for Wechat-MCP

Wechat-MCP handles private chat history and the encryption keys to the
databases that hold it. Report security issues through the private channel below.

## Reporting a vulnerability

**Do not open a public issue, pull request or discussion for a vulnerability.**

Report through GitHub private vulnerability reporting: open the repository's
**Security** tab and choose **Report a vulnerability**. No email address
accepts reports.

Include the affected version or commit, the macOS version and WeChat build, what
an attacker needs (a local user, malicious message content, a malicious MCP
client), reproduction steps that use **synthetic data**, and the impact you
observed.

**Never include real keys, message content, account identifiers, database
files, or unredacted diagnostics in a report.** If a report needs such
material, describe it. If you believe you exposed private data, treat the keys
and identity secret as compromised (see
[If private material leaks](#if-private-material-leaks)).

A volunteer maintains this project, with no bug bounty and no service-level
guarantee. The maintainer aims to respond within a reasonable time, plans a
fix or mitigation after triage, and credits reporters in the changelog unless
they ask otherwise.

## Supported versions

Pre-1.0: the latest commit on `main` receives fixes.

## Scope

In scope:

- Anything that lets the bridge write to WeChat's databases, send messages or
  act on the account
- Leaks of keys, the identity secret, message content, identifiers or private
  paths into logs, errors, diagnostics, tracked files or the wheel
- Escapes from the MCP surface: arbitrary SQL, shell, path or network access,
  path traversal or symlink following in media resolution, bypass of size, count
  or time bounds, cursor forgery or cross-generation confusion
- State-directory permission, atomicity or symlink-handling flaws
- Setup-lifecycle flaws that could terminate the wrong process, leave the
  installed app modified or unsigned, or leave a debugger attached
- Supply-chain issues in pinned dependencies or the SQLCipher build script

Out of scope:

- Weaknesses in WeChat itself, its encryption or its servers (report to the
  vendor)
- An attacker who already has your user account, full-disk access or root on the
  machine (they can read the files)
- Misbehavior of an MCP client or model that follows instructions embedded in
  messages. The bridge labels content as untrusted, and clients decide what to
  do with it
- Unsupported WeChat builds, platforms or accounts
- Findings that require committing real data to the repository

## Threat model summary

Assets: the per-database keys (`keys.json`), the identity secret
(`identity.json`), the archive (`archive.sqlite3`), decoded media caches and
setup and proof evidence, all in the private state directory.

Trust boundaries and assumptions:

- **The local user owns the account.** The tool assumes the person running it
  owns the WeChat account and the Mac. State files carry owner-only modes
  (0700/0600) and sit unencrypted on disk, so FileVault and backup hygiene
  matter.
- **Source data is untrusted input.** Database rows, protobuf and XML fields and
  media files come from other people. Parsers are bounded, pinned to the
  tested build's schema, and fail closed. The bridge never executes or
  interprets message text as instructions.
- **The MCP client is semi-trusted.** The server exposes nine typed read-only
  tools over stdio, with annotated read-only semantics, bounded replies and
  opaque authenticated cursors. It has no SQL, shell, send, network-listener or
  arbitrary-path capability. The client and its model see whatever the client
  retrieves.
- **Setup is a privileged, explicit step.** Key capture runs an ad-hoc-signed
  copy of the app under LLDB. It never re-signs the installed bundle, needs no
  sudo or SIP change, and passes no key on a command line. See
  [docs/setup.md](../docs/setup.md).
- **No network egress at runtime.** The one network access is the optional,
  checksum-verified SQLCipher source download in `scripts/build_sqlcipher.py`.

Known limits:

- Key verification proves a schema read, not full-page integrity.
- Source reads happen per database. No atomic account-wide snapshot exists.
- The bridge cannot stop a trusted client from exfiltrating what it may read.

Details: [docs/architecture.md](../docs/architecture.md) and the invariants in
[CONTRIBUTING.md](CONTRIBUTING.md#invariants-you-must-not-break). The
independent review in [docs/security-review.md](../docs/security-review.md)
tracks the open hardening items.

## Known limitations

- **Setup leaves a disabled app copy, and older setups may leave key copies.**
  Key capture writes `setup/<plan>/captured.json` and
  `setup/<plan>/candidates.json` under the private state directory. Setup now
  deletes both after the keys are verified and recovery completes, and reports
  `key_copies_removed`. Setups from older versions may still have them; delete
  them by hand. The `WeChat.disabled` app copy stays as a recovery artifact. A
  backup tool such as Time Machine copies the state directory, so exclude it
  from backups. Ordinary reads use `keys.json` in the directory root. This is
  finding H-1 in the [security review](../docs/security-review.md), partially
  resolved.
- **State at rest is plaintext.** Keys, the identity secret and the archive
  are readable by any process that runs as your user.
- **Retrieved content carries no per-item trust marker.** A message can contain
  text that tries to steer your assistant. Run clients without write or network
  tools when you can (finding H-5).

## Your responsibilities as a user

- Run the tool on your own account and computer, where you have permission to.
- Keep the state directory private. Do not sync it to cloud storage, commit it,
  or attach it to bug reports. Default location:
  `~/Library/Application Support/WechatBridge`.
- Exclude the state directory from Time Machine and other backups.
- Treat the archive and `keys.json` like the chat history itself. Use FileVault
  and a screen lock.
- Register the MCP server with clients and models you trust with your
  messages, and review what a client does with retrieved content.
- Review diagnostic output before you share it. Diagnostics are redacted
  aggregates by design, and only `search` and MCP results carry personal
  content.
- Respect the privacy of the people in your chats and the law that applies to
  you.

## If private material leaks

1. Stop the server and any MCP clients.
2. Remove the leaked material from wherever it was published, and assume
   someone copied it.
3. WeChat's databases own the database keys, and this tool cannot rotate them.
   Treat the chat history in the affected account as exposed, and review your
   account-security options with WeChat.
4. If the identity secret leaked, delete `identity.json` and the archive to
   change derived IDs. This changes every stable ID and invalidates cursors.
5. If the material reached a repository, a history rewrite does not undo the
   exposure. Rotate what you can and tell the maintainer through a private
   security advisory so the exposure is documented.
