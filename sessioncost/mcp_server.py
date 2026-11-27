"""`sessioncost mcp`: local-only JSON-RPC 2.0 MCP server over stdio.

Any MCP-capable agent (Claude Code, Codex, Cursor, Copilot, Antigravity) can ask SessionCost directly:
  - sessioncost_summary: this chat's cost, token breakdown, and top fix
  - sessioncost_status:  guard state, token tips, number exactness, and cache warmth
  - sessioncost_fix:     preview token-waste fixes, or apply/undo only after explicit user confirmation
  - sessioncost_report:  build the HTML report, return its path, and optionally open it

Invariants:
  - Local stdio only (zero network access, zero third-party dependencies).
  - No tool can turn the guard off or alter guard/tip settings; only the user can.
  - `sessioncost_fix` never mutates files unless `confirmed` is strictly True.
  - Tool descriptions stay very short so they never trigger token-waste checks.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import os
import sys
import types
from pathlib import Path
from typing import Any, TextIO

from . import __version__

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL_VERSION = "2025-03-26"
MAX_LINE_BYTES = 1_048_576  # 1 MiB cap per JSON-RPC message line to prevent memory DoS

TOOLS: list[dict[str, Any]] = [
    {
        "name": "sessioncost_summary",
        "description": "Show this chat's cost, token breakdown, and top fix.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project folder (default: cwd)."},
                "target": {"type": "string", "description": "'last', session id, or log path."},
            },
        },
    },
    {
        "name": "sessioncost_status",
        "description": "Show guard state, token tips, numbers, and cache warmth.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project folder (default: cwd)."},
                "target": {"type": "string", "description": "'last', session id, or log path."},
            },
        },
    },
    {
        "name": "sessioncost_fix",
        "description": "Preview token-waste fixes, or apply/undo after user says yes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project folder (default: cwd)."},
                "target": {"type": "string", "description": "'last', session id, or log path."},
                "only": {"type": "string", "description": "Comma-separated fix ids."},
                "apply": {"type": "boolean", "description": "Apply fixes (needs confirmed=true)."},
                "confirmed": {"type": "boolean", "description": "True only after user explicitly said yes."},
                "undo": {"type": "boolean", "description": "Undo last fix (needs confirmed=true)."},
            },
        },
    },
    {
        "name": "sessioncost_report",
        "description": "Build the HTML report, return its path, optionally open it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project folder (default: cwd)."},
                "target": {"type": "string", "description": "'last', session id, or log path."},
                "open": {"type": "boolean", "description": "Open report in browser (default: true)."},
            },
        },
    },
]


def _text_result(text: str, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _rpc_ok(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _rpc_err(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _make_ns(project: str | None, target: str | None, write_report: bool) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        target=target or "last",
        project=project or None,
        cursor_usage=None,
        prices=None,
        subscription=False,
        json=not write_report,
        live=False,
        no_open=True,
        brief=False,
        sarif=None,
    )


def _load_chat(project: str | None, target: str | None, write_report: bool = False) -> tuple[dict | None, Path | None, str]:
    """Find and analyse a chat with stdout/stderr captured so stdio JSON-RPC is never polluted."""
    from . import cli, locate, readers

    ns = _make_ns(project, target, write_report=write_report)
    out_buf, err_buf = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
        path = cli.find_session(ns)
        if path is None:
            msg = (err_buf.getvalue() or out_buf.getvalue()).strip() or locate.none_found(ns.project)
            return None, None, msg
        if not path.is_file() and ns.target != "last":
            path = cli.find_session(ns, chat="last")
            if path is None:
                msg = (err_buf.getvalue() or out_buf.getvalue()).strip() or locate.none_found(ns.project)
                return None, None, msg
        try:
            a, report_path = cli.build(path, ns)
        except (readers.NotSupported, ValueError, OSError) as e:
            return None, None, f"Could not read chat: {e}"
    return a, report_path, ""


def _token_breakdown(a: dict) -> str:
    from . import cli

    by_class = (a.get("totals") or {}).get("by_class") or []
    parts = [f"{c.get('label') or c.get('key')}: {cli.tokens(c.get('tokens', 0))}"
             for c in by_class if c.get("tokens")]
    return f"Breakdown: {', '.join(parts)}" if parts else ""


def _opt_str(args: dict[str, Any], key: str) -> str | None:
    val = args.get(key)
    if val is None:
        return None
    if not isinstance(val, str):
        raise ValueError(f"{key!r} must be a string")
    return val


def _validate_path_arg(val: str | None, *, key: str, reject_secrets: bool = True) -> str | None:
    """Lexically reject UNC/network paths, NUL bytes, and secret/credential paths before any disk probe."""
    if val is None or val == "" or (key == "target" and val == "last"):
        return val
    if "\x00" in val:
        raise ValueError(f"{key!r} contains NUL byte")
    if val.startswith(("\\\\", "//")):
        raise ValueError(f"{key!r} refuses UNC/network path")
    if reject_secrets:
        from .risk_patterns import _SECRET
        norm = "/" + val.replace("\\", "/").lstrip("/")
        if _SECRET.search(norm):
            raise ValueError(f"{key!r} refuses secret or credential file path")
    return val


def _opt_bool(args: dict[str, Any], key: str, default: bool) -> bool:
    if key not in args or args[key] is None:
        return default
    val = args[key]
    if not isinstance(val, bool):
        raise ValueError(f"{key!r} must be a boolean")
    return val


def _reject_nan(token: str) -> None:
    raise ValueError(f"Non-standard JSON constant not allowed: {token}")


def _valid_req_id(req_id: Any) -> bool:
    if req_id is None or isinstance(req_id, str):
        return True
    if isinstance(req_id, bool):
        return False
    if isinstance(req_id, int):
        return True
    if isinstance(req_id, float):
        return math.isfinite(req_id)
    return False


def _tool_summary(args: dict[str, Any]) -> dict[str, Any]:
    from . import cli

    project = _validate_path_arg(_opt_str(args, "project"), key="project")
    target = _validate_path_arg(_opt_str(args, "target"), key="target")
    a, _, err = _load_chat(project, target, write_report=False)
    if err or a is None:
        return _text_result(err or "No chat found.", is_error=True)
    text = cli.summary(a, None)
    breakdown = _token_breakdown(a)
    if breakdown:
        text = f"{text}\n{breakdown}"
    return _text_result(text)


def _tool_status(args: dict[str, Any]) -> dict[str, Any]:
    from . import status
    from .guard_compat import compat_warnings

    project_arg = _validate_path_arg(_opt_str(args, "project"), key="project")
    target_arg = _validate_path_arg(_opt_str(args, "target"), key="target")
    project = project_arg or os.getcwd()
    a, _, _ = _load_chat(project_arg, target_arg, write_report=False)
    if a is not None:
        return _text_result(status.block(a, a.get("fix_project") or project))
    lines = [status.guard_line(in_chat=False), status.tips_line()]
    for warn in compat_warnings():
        lines.append(f"Warning: {warn}")
    lines.append("Chat: no recorded chat found for this project yet.")
    return _text_result("\n".join([status.HEAD, *("  " + x for x in lines)]))


def _tool_fix(args: dict[str, Any]) -> dict[str, Any]:
    from . import fixer

    project_arg = _validate_path_arg(_opt_str(args, "project"), key="project")
    target_arg = _validate_path_arg(_opt_str(args, "target"), key="target")
    only_arg = _opt_str(args, "only")
    project = fixer.project_path(project_arg or os.getcwd())
    undo = args.get("undo") is True
    apply_now = args.get("apply") is True
    confirmed = args.get("confirmed") is True

    if undo:
        if not confirmed:
            return _text_result(
                "Refused to undo: undoing a fix modifies files and requires explicit user confirmation "
                "in the chat (confirmed=true). Nothing was changed.",
                is_error=True,
            )
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code, msg = fixer.undo(project)
        return _text_result(msg, is_error=(code != 0))

    target = target_arg or "last"
    a, _, err = _load_chat(project_arg, target, write_report=False)
    if err or a is None:
        return _text_result(err or "No chat found.", is_error=True)

    only_raw = (only_arg or "").strip()
    only = {x.strip() for x in only_raw.split(",") if x.strip()} if only_raw else None
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        fixer.record_seen(a, project)
        plan = fixer.plan(a, project, only=only)
        rendered = fixer.render(plan, fixer.verify_lines(project), newest=(target == "last"))
        fixer.mark_shown(plan)

    if not apply_now:
        return _text_result(rendered)

    if not confirmed:
        return _text_result(
            rendered + "\n\nRefused to apply: applying fixes modifies project files and requires explicit "
            "user confirmation in the chat (confirmed=true). Nothing was changed.",
            is_error=True,
        )

    if not any(i.can_apply or i.kind == "handover" for i in plan.items):
        return _text_result(rendered + "\n\nNothing to apply: no change is needed for what was found.")

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        code, msg = fixer.apply(plan)
    return _text_result(rendered + "\n\n" + msg, is_error=(code != 0))


def _tool_report(args: dict[str, Any]) -> dict[str, Any]:
    from . import cli, opener

    project_arg = _validate_path_arg(_opt_str(args, "project"), key="project")
    target_arg = _validate_path_arg(_opt_str(args, "target"), key="target")
    open_it = _opt_bool(args, "open", True)
    a, out, err = _load_chat(project_arg, target_arg, write_report=True)
    if err or a is None or out is None:
        return _text_result(err or "Could not build report.", is_error=True)
    if open_it:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            opener.open_report(out)
    return _text_result(cli.summary(a, out))


_HANDLERS = {
    "sessioncost_summary": _tool_summary,
    "sessioncost_status": _tool_status,
    "sessioncost_fix": _tool_fix,
    "sessioncost_report": _tool_report,
}


def handle_request(msg: Any) -> dict[str, Any] | None:
    """Process one decoded JSON-RPC 2.0 message. Returns a response dict, or None for notifications."""
    if not isinstance(msg, dict):
        return _rpc_err(None, -32600, "Invalid Request")
    has_id = "id" in msg
    req_id = msg.get("id")
    if has_id and not _valid_req_id(req_id):
        return _rpc_err(None, -32600, "Invalid Request: invalid id")
    method = msg.get("method")
    if not isinstance(method, str) or not method:
        return _rpc_err(req_id, -32600, "Invalid Request") if has_id else None

    params = msg.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _rpc_err(req_id, -32602, "Invalid params") if has_id else None

    # Notifications (no "id"): accept silently with no response per JSON-RPC 2.0.
    if not has_id:
        return None

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION
        return _rpc_ok(
            req_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "sessioncost", "version": __version__},
            },
        )
    if method == "ping":
        return _rpc_ok(req_id, {})
    if method == "tools/list":
        return _rpc_ok(req_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        raw_args = params.get("arguments")
        arguments = {} if raw_args is None else raw_args
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return _rpc_err(req_id, -32602, "Invalid params")
        handler = _HANDLERS.get(name)
        if handler is None:
            return _rpc_err(req_id, -32602, f"Unknown tool: {name}")
        try:
            return _rpc_ok(req_id, handler(arguments))
        except Exception as e:  # noqa: BLE001 (keep MCP server alive on unexpected tool errors)
            return _rpc_ok(req_id, _text_result(f"SessionCost error: {e}", is_error=True))

    return _rpc_err(req_id, -32601, f"Method not found: {method}")


def serve_stdio(stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Run the newline-delimited JSON-RPC 2.0 MCP loop on stdin/stdout until EOF."""
    inp = sys.stdin if stdin is None else stdin
    out = sys.stdout if stdout is None else stdout
    while True:
        raw = inp.readline(MAX_LINE_BYTES + 1)
        if not raw:
            break
        if len(raw) > MAX_LINE_BYTES:
            # Drain remainder of oversized line so the stream stays framed.
            if not raw.endswith("\n"):
                while True:
                    chunk = inp.readline(MAX_LINE_BYTES)
                    if not chunk or chunk.endswith("\n"):
                        break
            resp: dict[str, Any] | None = _rpc_err(None, -32600, "Invalid Request: line exceeds size limit")
        else:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line, parse_constant=_reject_nan)
            except ValueError:
                resp = _rpc_err(None, -32700, "Parse error")
            else:
                resp = handle_request(msg)
        if resp is not None:
            out.write(json.dumps(resp, ensure_ascii=False, allow_nan=False) + "\n")
            out.flush()
    return 0


def mcp_config() -> dict[str, Any]:
    """Ready-to-paste MCP server config using this exact Python executable."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").is_file():
        exe = exe.with_name("python.exe")
    return {
        "mcpServers": {
            "sessioncost": {
                "command": exe.as_posix(),
                "args": ["-m", "sessioncost", "mcp"],
            }
        }
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="sessioncost mcp",
        description="Local-only JSON-RPC 2.0 MCP server over stdio. Use `sessioncost mcp config` to print the snippet "
                    "for your agent's MCP settings.",
    )
    ap.add_argument("action", nargs="?", choices=("serve", "config"), default="serve",
                    help="'serve' (default): run the stdio MCP server; 'config': print the MCP JSON config snippet")
    args = ap.parse_args(argv or [])
    if args.action == "config":
        print(json.dumps(mcp_config(), indent=2, ensure_ascii=False))
        return 0
    return serve_stdio()
