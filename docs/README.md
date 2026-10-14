# Documentation index

Each fact has one owning document. Link to it instead of restating it.

New here? Read [../README.md](../README.md), then [architecture.md](architecture.md).
Want to contribute? Read [.github/CONTRIBUTING.md](../.github/CONTRIBUTING.md).

## Project policy and community

| Document | Owns |
| --- | --- |
| [../README.md](../README.md) | Project scope, requirements, quick start, CLI overview, MCP client config, status and limitations, legal disclaimer |
| [.github/CONTRIBUTING.md](../.github/CONTRIBUTING.md) | Dev setup, tests, invariants contributors must keep, adding a WeChat build, branch/commit/PR conventions |
| [.github/SECURITY.md](../.github/SECURITY.md) | Vulnerability reporting, scope, threat model summary, user data-handling responsibilities |
| [.github/CODE_OF_CONDUCT.md](../.github/CODE_OF_CONDUCT.md) | Community standards and enforcement |
| [.github/CHANGELOG.md](../.github/CHANGELOG.md) | User-visible changes, Keep a Changelog format |
| [../THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) | Licenses and provenance for adapted source and runtime dependencies (full text in [../third_party/licenses/](../third_party/licenses)) |

## Living documents: keep in sync with the code

| Document | Owns |
| --- | --- |
| [architecture.md](architecture.md) | Packages, dependency direction, runtime state, key contracts and limits |
| [mcp.md](mcp.md) | MCP tools, arguments, limits, attachment delivery, paging, errors, client registration |
| [setup.md](setup.md) | One-time key capture procedure, outcomes and recovery |
| [compatibility.md](compatibility.md) | Tested environment, format support, latest live observations, known gaps |
| [troubleshooting.md](troubleshooting.md) | Error codes and symptoms, what each means, the safe next step |
| [data-flow.md](data-flow.md) | One message from encrypted source to MCP response: owners, bounds, read-only boundaries |
| [glossary.md](glossary.md) | Terms used across the docs, each linked to its owning document |
| [uninstall.md](uninstall.md) | Removing the MCP registration, state directory, app copy and backup exclusion |

## Reviews

Point-in-time assessments of the codebase. Each describes the code as of its
writing.

| Document | Subject |
| --- | --- |
| [security-review.md](security-review.md) | Security review of the codebase and its trust boundaries |
| [architecture-review.md](architecture-review.md) | Architecture and package-structure review |
| [test-coverage-review.md](test-coverage-review.md) | Test coverage and hardening review |


## Maintenance rules

- A change that alters behavior, a command, a limit, a tool contract, a
  dependency or the package layout updates the owning living document in the
  same commit.
- Every number or claim must be verifiable from code or tests. Date
  observations that a reader cannot re-run from the repository.
- Keep private material (keys, identifiers, message content, account paths,
  hashes of private files) out of every tracked document.
- Correct or remove stale statements instead of appending contradicting ones.
