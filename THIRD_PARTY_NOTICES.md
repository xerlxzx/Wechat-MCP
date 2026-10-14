# Dependency and reference provenance

SQLCipher is an external native dependency. The repository does not vendor it. Tested source: [v4.19.0](https://github.com/sqlcipher/sqlcipher/tree/v4.19.0), archive SHA-256 `7075f96cbabe45b4ecfc2e6b1745a625f856f695b0827a5506ce9ed85b906aa0` (also published by Homebrew). SQLCipher is BSD-3-Clause; underlying SQLite is public domain. The installed dependency's source/license is retained in the local build directory; see upstream LICENSE.md and README.md before redistributing binaries.

The native Python reader is its own implementation of the documented SQLite/SQLCipher C interfaces. Public WeChat reference snapshots informed the format work, and none of their code is included. One extraction reference is adapted: the capture helper identified below. The project includes no GPL or noncommercial application code.

## Adapted setup capture helper

`src/wechat_bridge/setup/lldb_capture.py` adapts `capture_keys.py` from [acmerfight's local toolkit](https://gist.github.com/acmerfight/0a01249ef72970d07a2603dbb629e80f), gist revision `c98dbb10cf23c30e83d5d744905101b0880887a0`. The inspected original file SHA-256 is `0b0b9f9296bf02219c07537827ed9ce97671611ffeab36572bedb267a7704d65`. MIT license retrieved at that revision: `72380798af61d6131287b0dd3c8dc5345535df641002fece3a02d7ef109c5f8e`; full text in `third_party/licenses/acmerfight-toolkit.txt`.

Changes: private PID/start/executable ownership record, source symlink refusal,
sanitized launch/top-level error output, and integration with this project's
explicit setup lifecycle and native verification. No upstream UI/sending code
is included. Tests cover native page authentication with synthetic fixtures.

## Source-format and image dependencies

The identity, protobuf/XML, resource mapping and image modules are independent
implementations informed by observed metadata and public format
references. Parser or media source code was not
copied from those references. The public V1 format constant is not an account key.

Locked runtime dependencies, with license expressions from installed package
metadata: Pillow 12.3.0 (`MIT-CMU`), cryptography 50.0.2 (`Apache-2.0 OR BSD-3-Clause`),
cffi 2.1.1 (`MIT-0`) and pycparser 3.0 (`BSD-3-Clause`). Their distributions retain
their own notices. The project wheel includes this file and the full adapted
capture-helper MIT license.

## MCP runtime

The official Python MCP SDK is locked to `mcp==2.3.0` (`MIT`) and
`mcp-types==2.3.0` (`MIT`). No SDK source is copied into this project.
The lock also includes the following runtime distributions. License names
below come from the installed distribution metadata; each distribution
retains its own license files and notices.

| License | Distributions and locked versions |
| --- | --- |
| MIT | annotated-types 0.8.0; anyio 4.15.1; attrs 26.1.0; h11 0.16.0; jsonschema 4.26.0; jsonschema-specifications 2025.9.1; pydantic 2.13.5; pydantic-core 2.46.5; PyJWT 2.15.1; referencing 0.37.0; rpds-py 2026.9.1; truststore 0.10.4; typing-inspection 0.4.4 |
| BSD-3-Clause | click 8.5.0; httpcore2 2.13.1; httpx2 2.13.1; idna 3.20; sse-starlette 3.5.0; starlette 1.7.0; uvicorn 0.54.0 |
| Apache-2.0 | opentelemetry-api 1.45.1; python-multipart 0.0.32 |
| PSF-2.0 | typing-extensions 4.16.0 |

`uv.lock` also lists `pywin32` (Windows only) and `httpx2-jsfetch`
(Emscripten only). Their markers exclude them from a macOS install, so they
are not part of this project's runtime.
