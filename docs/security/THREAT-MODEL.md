# SessionCost LiveGuard: Threat Model

Version: 1.2.11 · Updated: 2026-11-28 · Audience: Developers, team leads, and InfoSec reviewers.

See [SECURITY.md](../../SECURITY.md) for a concise summary of local file access, network behavior, and vulnerability reporting.

## What it is

SessionCost LiveGuard is a local hook-based guard for AI coding agents. **Claude Code, GitHub Copilot (VS Code Copilot Chat & Copilot CLI), Cursor, Codex, and Antigravity** invoke it through their native hook systems before and after tool steps.

- **100% local, zero AI model calls, zero telemetry:** LiveGuard evaluates every step locally using deterministic rules and writes each decision to a SHA-256 hash-chained local audit log (`~/.sessioncost/guard/`).
- **Strict offline mode (zero outbound network calls):** run `sessioncost setup --offline`, `sessioncost guard offline on`, or set `SESSIONCOST_OFFLINE=1` to disable all outbound network requests across LiveGuard, price updates, and self-updates.
- **Default online mode (one narrow check):** when offline mode is off, the only automatic outbound request is LiveGuard's fresh-release check, which queries `pypi.org` or `registry.npmjs.org` with a public package's name when an agent installs a public package, to warn on newly published versions. Installs targeting private/custom registries (`--index-url`, `--extra-index-url`, `--find-links`, `--registry`, `PIP_INDEX_URL`, `UV_INDEX_URL`, `NPM_CONFIG_REGISTRY`, etc.) and scoped npm packages (`@org/pkg`) are skipped automatically so internal package names are never sent to public registries. If the registry is unreachable, the check fails open. Disable this check alone with `sessioncost guard fresh-releases off`.
- **Installation and updates:** SessionCost recommends pinned package-manager installs (`python -m pip install sessioncost==1.2.11` or `uv tool install sessioncost==1.2.11`). Existing installs update via `sessioncost update` (or `sessioncost update --to <version>`), which passes LiveGuard cleanly on Windows and macOS/Linux while `irm | iex` and `curl | sh` remain strictly blocked.

LiveGuard is **a strong extra safety layer, not a sandbox**. It inspects each tool step as text before execution. A determined attacker running with the same OS user privileges can bypass text-based rules. Combine LiveGuard with an OS-level sandbox (see [Recommended setup with a sandbox](#recommended-setup-with-a-sandbox)).

## What it protects against

| Threat | What the guard does | OWASP |
|---|---|---|
| Prompt injection in a web page, file, or tool output steering the agent | Scans tool results for injection signs (steering phrases, fake system tags, invisible Unicode). After an injection, risky steps in that turn are **blocked**. After untrusted content, pushes, outbound sends, secrets, and rule changes are **asked** (even if allowed earlier). | LLM01:2025, ASI01 |
| Agent leaking secrets | Reading or sending `.env`, keys, `~/.ssh`, or secret-looking values (API keys, tokens, private keys) in a command, URL, or MCP call is **blocked**. Dumping all environment variables is **blocked**. | LLM02:2025, ASI03 |
| Running remote or obfuscated code | `curl … \| sh`, `irm … \| iex`, any URL piped into a shell, encoded PowerShell, `base64 -d \| sh`: **blocked** (NEVER list). Running a staged script on disk containing dangerous steps: **asked** before execution. | ASI05 |
| Excessive agency: risky or irreversible actions | Force-push and history rewrites, disabling commit hooks, disabling security tools: **blocked**. Push, install, writing outside the project, and destructive MCP tools: **asked** (or recorded, depending on risk). | LLM06:2025, ASI02 |
| Persistence | `cron`, scheduled tasks, Windows Run keys, startup folders, `systemctl enable`, shell startup files: **blocked**. | ASI10 |
| Memory and rule poisoning | Edits to `CLAUDE.md`, `AGENTS.md`, agent settings, `.mcp.json`, git hooks, and CI configs that drop checks or follow untrusted content: **asked**. The agent's automatic memory folder is guarded when notes look injected, contain NEVER-list commands, or follow untrusted content. | ASI06 |
| Supply chain: fresh releases, skills, plugins, MCP servers | Fresh-release checks pause installs of public package versions published in the last few days (unless offline or disabled). SHA-256 fingerprints of skills, plugins, and MCP server configs are tracked across sessions so new or changed entries are flagged. | LLM03:2025, ASI04 |
| Agent disabling the guard or loosening rules | Switching the guard off, editing `~/.sessioncost/guard/`, project allow files, agent hook configs, `disableAllHooks`, `hooks = false`, or installed plugins: **always blocked**. Project allow rules cannot override this. Only the user can change these directly. | LLM06:2025, ASI10 |
| Subagents / helpers | Claude Code runs the same hooks inside subagents, and the audit log records which subagent acted. | ASI07, ASI08 |

## Supported agents and platform behavior

LiveGuard supports all five agents with native hook integration (including native Windows support without requiring `bash` on `PATH`):

- **Claude Code:** Full hook lifecycle (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `Stop`, `SessionEnd`) including subagents. Plugin installs ship both `hooks/guard.sh` (macOS/Linux) and `hooks/guard.ps1` (native Windows PowerShell launcher using `pythonw.exe` to avoid console window flashes). Non-plugin installs register hooks in `~/.claude/settings.json`.
- **GitHub Copilot (VS Code Copilot Chat & Copilot CLI):** Dedicated hook file `~/.copilot/hooks/sessioncost-guard.json` (`sessionStart`, `userPromptSubmitted`, `preToolUse`, `postToolUse`, `postToolUseFailure`, `agentStop`, `sessionEnd`). Registers `command`, `windows`, `powershell`, and `bash` entries; on Windows it invokes `pythonw.exe` directly without requiring `bash` on `PATH` or PowerShell `.ps1` execution policy overrides. Note: Copilot's 10-second hook timeout fails open if a machine is severely overloaded.
- **Codex:** Hooks in `~/.codex/hooks.json` (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`). Codex cannot display a native "ask" dialog, so every "ask" becomes a block that tells the agent to ask you in chat; replying yes in your next message allows that step once. Codex's built-in web search does not fire hooks.
- **Cursor:** Hooks in `~/.cursor/hooks.json` (`sessionStart`, `beforeSubmitPrompt`, `beforeShellExecution`, `afterShellExecution`, `beforeMCPExecution`, `afterMCPExecution`, `beforeReadFile`, `stop`, `sessionEnd`). Cursor has no per-chat `/liveguard` prompt switch (toggle via `sessioncost guard on|off` in a terminal), and `beforeReadFile` has no "ask" decision so read asks map to deny.
- **Antigravity:** Named hook entry `sessioncost-guard` in `~/.gemini/config/hooks.json` (`SessionStart`, `PreInvocation`, `PreToolUse`, `PostToolUse`, `Stop`). `PreToolUse` maps "ask" to `force_ask` so "Always allow" memory cannot bypass a guard question. `PostToolUse` does not pass tool output to hooks, so injection signs in tool results are not scanned there, and untrusted content remains active until the chat ends.

## What it does not protect against (known gaps)

The 5 known red-team gaps below are tracked as explicit test cases in the red-team suite on every test run:

- **Decoded commands run via command substitution (`enc-3`):** piping decoded output into a shell (`base64 -d | sh`) is blocked, whereas `$(echo ... | base64 -d)` command substitution is not.
- **Escaped characters in environment-dump commands (`env-5`):** common env-dump commands and scripts (`env`, `printenv`, `print(os.environ)`, `console.log(process.env)`) are blocked, whereas shell-escaped command names (e.g. `e\nv`) are not.
- **Target paths assembled dynamically at runtime (`tam-6`):** variables, command substitution, or string concatenation that build a protected path at runtime can evade static command inspection.
- **Harmful edits to ordinary application code after an injection (`inj-4`):** only sensitive rule/config/CI/hook files are guarded; subtle logic backdoors in ordinary `src/` files are not.
- **Poisoned MCP tool descriptions (`mcp-5`):** agent hooks do not receive MCP tool descriptions; only new or changed MCP server configurations are detected.

Additional architectural boundaries:

- **Network-level exfiltration and reverse shells:** LiveGuard does not attempt to classify every possible network utility or DNS/HTTP covert channel. Use an OS sandbox with a network allow-list for egress control.
- **Tamper-evident, not tamper-proof audit logs:** every log line in `~/.sessioncost/guard/<session>.jsonl` is SHA-256 hash-chained to the previous line, and the agent is blocked from modifying `~/.sessioncost/guard/`. However, a malicious process running as the same OS user outside hook enforcement could still rewrite the entire chain. True tamper-proofing requires an external anchor (separate OS user or off-host log shipping).

## Measured results

Measured against the internal red-team suite and real-world session replay (2026-11-19):

- **Red-team suite:** **81 cases** — **76 handled as intended**, **5 known gaps** (documented above).
- **Real-world replay (false-alarm rate):** **637 recent sessions** across Claude Code (290), Antigravity (153), Codex (119), and Cursor (75), totaling **59,111 tool steps**, replayed assuming the user answered yes to each prompt:
  - **Passed:** 57,899 steps (**97.95%**)
  - **Blocked:** 767 steps (**1.30%**; includes genuine risky actions such as pushes after untrusted web content or `.env` access)
  - **Asked:** 445 steps (**0.75%**, ~0.7 prompts per session)

## Recommended setup with a sandbox

For high-assurance environments, pair LiveGuard with an OS-enforced sandbox such as Claude Code's built-in sandbox (`code.claude.com/docs/en/sandboxing`):

- **What the sandbox controls:** restricts shell file writes to the project and temp directories and routes network traffic through an allow-listed proxy.
- **How to enable:** `/sandbox`, or `"sandbox": {"enabled": true}` in settings.
- **Recommended hardening:**
  - `sandbox.failIfUnavailable: true` (refuse to run unsandboxed if the sandbox cannot start)
  - `sandbox.allowUnsandboxedCommands: false`
  - `sandbox.filesystem.denyRead` for `~/.ssh` and cloud credential directories
- **Platforms:** macOS, Linux, and WSL2 (native Windows does not sandbox shell commands; use WSL2 when sandboxing is required).

**Defense in depth:**
- **The OS sandbox** enforces what a shell command *can* access.
- **LiveGuard** evaluates whether a step *should* run, covers non-shell tools (edits, web, MCP), and records a hash-chained audit trail in the session report.
- **Agent permission deny rules** provide a third built-in enforcement layer.

## References

- OWASP Top 10 for LLM Applications 2025: https://genai.owasp.org/llm-top-10
- OWASP Top 10 for Agentic Applications 2026: https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026
- Claude Code hooks & sandboxing: https://code.claude.com/docs/en/hooks · https://code.claude.com/docs/en/sandboxing
- GitHub Copilot hooks: https://docs.github.com/en/copilot
- Codex hooks: https://learn.chatgpt.com/docs/hooks
- Cursor hooks: https://cursor.com/docs/hooks
