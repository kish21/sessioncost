# Security

SessionCost runs **100% locally** on your machine. It makes **zero AI model calls** and collects **zero telemetry, analytics, or crash reports**. Your source code, prompts, and session logs never leave your computer.

For the full LiveGuard security architecture, supported agent hooks, known gaps, and red-team measurements, see [docs/security/THREAT-MODEL.md](docs/security/THREAT-MODEL.md).

## What SessionCost accesses

| Category | What is accessed | Notes |
|---|---|---|
| **Files read** | Local AI agent session logs (`~/.claude/projects/`, `~/.codex/sessions/`, `~/.cursor/`, `~/.gemini/antigravity/`, `~/.copilot/` & VS Code Copilot Chat workspace storage) and agent/project skill, hook, and MCP configs | Read-only analysis to compute token counts, detect waste, and fingerprint local skills/MCP servers. Nothing is uploaded. |
| **Files written** | `~/.sessioncost/` (HTML reports in `reports/`, LiveGuard config/state/SHA-256 hash-chained audit logs in `guard/`, optional price overrides in `prices.json`) and agent skill/hook configs during `sessioncost setup` (`~/.claude/`, `~/.codex/`, `~/.cursor/`, `~/.gemini/config/`, `~/.copilot/hooks/sessioncost-guard.json`) | Existing agent config files are backed up as `*.before-sessioncost` before first modification. Project files are only edited when you explicitly approve `/sessioncost fix` (with backup and `/sessioncost fix --undo`). |
| **Network access (strict offline mode)** | **None — zero outbound network calls** | Enable via `sessioncost setup --offline`, `sessioncost guard offline on`, or `SESSIONCOST_OFFLINE=1`. Disables package release-age lookups, `sessioncost prices update`, and `sessioncost update`. |
| **Network access (default online mode)** | LiveGuard fresh-release lookup: sends **only** the public package name to `pypi.org` or `registry.npmjs.org` when an agent installs a public package | Private/custom registries (`--index-url`, `--extra-index-url`, `--find-links`, `--registry`, `PIP_INDEX_URL`, `UV_INDEX_URL`, `NPM_CONFIG_REGISTRY`) and scoped npm packages (`@org/pkg`) are skipped automatically so internal package names are never sent to public registries. Disable with `sessioncost guard fresh-releases off`. |
| **Network access (explicit opt-in commands)** | `sessioncost update` / `sessioncost update --to <version>` (PyPI / GitHub release tags) and `sessioncost prices update` (fetches `sessioncost/prices.json` from GitHub) | Only run when you explicitly invoke them; both are blocked when offline mode is enabled. |
| **AI model calls & telemetry** | **None** | Zero LLM API calls, zero telemetry, zero tracking beacons. |

## Safe installation and updates

- **Recommended (corporate / InfoSec-friendly):** install a pinned release directly from PyPI (`python -m pip install sessioncost==1.2.10` or `uv tool install sessioncost==1.2.10`) and run `sessioncost setup --offline` for zero-network environments.
- **Updates:** run `sessioncost update` (or `/sessioncost update` in chat) — do not pipe remote install scripts into a shell (`irm | iex` and `curl | sh` are intentionally blocked by LiveGuard).

## Reporting a vulnerability

Please report security issues privately rather than opening a public GitHub issue:

1. Open the **Security** tab of this repository on GitHub.
2. Choose **Report a vulnerability** (GitHub Private Vulnerability Reporting).

We aim to acknowledge reports within a few business days and coordinate a fix before public disclosure.

## Supported versions

Only the latest release is supported. Security fixes ship as a new pinned version on PyPI and GitHub Releases.
