"""The watch list and the live guard's decisions as SARIF 2.1.0, the format security tools read (GitHub code
scanning, most SIEM and review tools): one result per step worth a look, with its severity, the reason, where it
happened (turn, call, step) and what the guard did. The run carries the session log's SHA-256 and whether the guard's
log is intact, so a finding can be traced back to the evidence. Written on this computer; nothing is sent.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from . import __version__
from .risks import SEVERITY

LEVEL = {"high": "error", "review": "warning", "info": "note"}
TEXT = {
    "guard_tamper": "Tried to change or switch off the SessionCost guard or the rules the user set",
    "pipe_to_shell": "Ran a script straight from the internet", "encoded_command": "Ran a hidden (encoded) command",
    "env_dump": "Printed every environment variable", "persistence": "Set something up to run again later",
    "security_off": "Switched off a security tool", "force_push": "Rewrote shared git history",
    "injected": "A result showed signs of a prompt injection",
    "after_untrusted": "A risky step taken right after untrusted content came in",
    "guardrails": "Changed the agent's own rules, settings or memory", "mcp_action": "An MCP tool that changes or sends something",
    "secrets": "Touched a secrets file or key", "skips_commit_checks": "Tried to switch off the commit checks",
    "wipe": "Deleted the whole disk or the home folder", "privilege": "Raised its own rights or changed the computer itself (sudo, accounts, services, the machine's settings)",
    "git_push": "Pushed with git", "deletes": "Deleted files", "installs": "Installed or fetched packages",
    "outside_project": "Changed a file outside the project", "network": "Used the network",
    "inline_script": "Ran code written into the command",
    "key_in_output": "A key or token reached the chat (a tool result showed one, or a script printed one)",
    "poisoned_tool": "Used a tool of an MCP server whose descriptions showed signs of a prompt injection",
}
OWASP = {   # OWASP Top 10 for LLM Applications 2025 (genai.owasp.org/llm-top-10) and for Agentic Applications 2026
    "injected": ["LLM01:2025", "ASI01"], "after_untrusted": ["LLM01:2025", "LLM06:2025", "ASI01", "ASI02"],
    "guardrails": ["ASI06"], "guard_tamper": ["LLM06:2025", "ASI10"], "secrets": ["LLM02:2025", "ASI03"],
    "env_dump": ["LLM02:2025"], "key_in_output": ["LLM02:2025", "ASI03"], "poisoned_tool": ["LLM01:2025", "ASI04"], "pipe_to_shell": ["ASI05"], "encoded_command": ["ASI05"], "inline_script": ["ASI05"],
    "persistence": ["ASI10"], "security_off": ["LLM06:2025"], "mcp_action": ["ASI02"], "network": ["ASI02"],
    "installs": ["LLM03:2025", "ASI04"], "git_push": ["LLM06:2025"], "force_push": ["LLM06:2025"], "deletes": ["LLM06:2025"],
    "outside_project": ["LLM06:2025"], "skips_commit_checks": ["LLM06:2025"],
    "wipe": ["LLM06:2025", "ASI02"], "privilege": ["LLM06:2025", "ASI03"],
}


def _sha(path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except (OSError, TypeError):
        return ""


def build(a: dict, session_path) -> dict:
    g = a.get("guard") or {}
    steps = (g.get("steps") or {}) if g else {}
    results = []
    for ci, c in enumerate(a.get("calls", [])):
        for si, x in enumerate(c.get("actions", [])):
            for m in x.get("risks", []):
                gd = steps.get(x.get("id")) or {}
                where = f"turn {c.get('turn')}, {'call' if c.get('agent') == 'main' else 'helper call'} {c.get('n')}, step {si + 1}"
                results.append({
                    "ruleId": m, "level": LEVEL.get(SEVERITY.get(m, "review"), "warning"),
                    "message": {"text": f"{TEXT.get(m, m)}: {x.get('label', '').replace('`', '')} ({where})"},
                    "locations": [{"logicalLocations": [{"name": where, "kind": "function",
                                                         "fullyQualifiedName": f"session/{a['meta'].get('session_id', '')}/turn/{c.get('turn')}/call/{ci}/step/{si}"}]}],
                    "properties": {"tool": x.get("tool"), "kind": x.get("kind"), "where": x.get("where", ""),
                                   "toolUseId": x.get("id", ""), "time": c.get("time"),
                                   **({"after": x["after"]} if x.get("after") else {}),
                                   **({"guard": gd.get("decision"), "userAllowed": bool(gd.get("allowed"))} if gd else {})}})
    rules = [{"id": m, "name": m, "shortDescription": {"text": TEXT.get(m, m)},
              "defaultConfiguration": {"level": LEVEL.get(SEVERITY.get(m, "review"), "warning")},
              "properties": {"severity": SEVERITY.get(m), "owasp": OWASP.get(m, [])}} for m in SEVERITY]
    return {"$schema": "https://json.schemastore.org/sarif-2.1.0.json", "version": "2.1.0", "runs": [{
        "tool": {"driver": {"name": "SessionCost", "version": __version__, "informationUri": "https://github.com/kish21/sessioncost",
                            "rules": rules}},
        "results": results,
        "properties": {"sessionId": a["meta"].get("session_id"), "agent": a["meta"].get("agent"),
                       "sessionLogSha256": _sha(session_path),
                       "guard": ({"on": True, "logIntact": g.get("intact"), "logLines": g.get("lines"), "blocked": g.get("blocked"),
                                  "asked": g.get("asked"), "allowed": g.get("allowed"), "injections": g.get("injections")}
                                 if g else {"on": False})}}]}
