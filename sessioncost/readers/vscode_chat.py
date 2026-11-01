"""GitHub Copilot Chat in VS Code: <VS Code user folder>/workspaceStorage/<hash>/chatSessions/<session id>.json or
.jsonl, one file per chat; workspace.json next to chatSessions holds the project folder as a file:// URI. The user
folder is %APPDATA%/Code/User (Windows), ~/Library/Application Support/Code/User (macOS) or ~/.config/Code/User
(Linux), and "Code - Insiders" for the Insiders build.

What the files hold (checked on 141 chats of 2026; field names only):
- a .json file is the whole chat; a .jsonl file is a list of edits to it: kind 0 = the starting state, kind 1 = set
  the value at path `k`, kind 2 = put the list `v` into the list at `k` from index `i` (or at its end). Replaying
  them gives the same chat;
- `requests`: one per message you sent: `message.text`, `timestamp` (ms), `modelId` ("copilot/auto" when Copilot
  picks the model), `result.timings.totalElapsed`, and `result.metadata.toolCallRounds`: one round per model call,
  each with its reply text, its tool calls (`name`, `arguments` as JSON text, `id`), sometimes the real `modelId`
  behind "auto", and the hidden thinking's size; the tools' answers are in `result.metadata.toolCallResults` by id;
- token counts are rare: `promptTokens` / `outputTokens` (or `completionTokens`) on about a third of the requests,
  for the request as a whole, and nothing at all about the cache.
So the tokens are estimated from the size of the text (scaled to Copilot's own counts where it gave them), and the
cost is a range. Copilot is billed per premium request on a plan, so the money is API-equivalent anyway.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..model import Action, Call, Event, Session, Setup, SetupPart, Turn, Usage, said_kind
from ..textflags import fingerprint, text_flags
from .claude import short
from .codex import _command, _name, _patch

# Copilot trims older tool output so a prompt fits the model; the estimate carries everything, so it is capped here.
# An assumption (a prompt limit common to the models Copilot Chat offers), not a number from the log; the largest
# prompt Copilot itself counted in the chats checked was 42,606 tokens.
MAX_PROMPT = 128_000


def vscode_homes() -> list[Path]:
    """The VS Code user folders on this computer (stable and Insiders)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return [base / name / "User" for name in ("Code", "Code - Insiders")]


def is_chat(path: Path) -> bool:
    return path.suffix in (".json", ".jsonl") and path.parent.name == "chatSessions"


def replay(path: Path) -> dict:
    """The chat as VS Code holds it: a .json file as it is, a .jsonl file with its edits applied in order."""
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    state: dict = {}
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                k, v = r.get("k") or [], r.get("v")
                if r.get("kind") == 0:
                    state = v if isinstance(v, dict) else {}
                    continue
                parent = state
                for p in k[:-1]:
                    parent = parent[p]
                if r.get("kind") == 1:
                    parent[k[-1]] = v
                elif r.get("kind") == 2:
                    lst = parent[k[-1]]
                    if "i" in r:
                        lst[r["i"]:] = v
                    else:
                        lst.extend(v)
            except (ValueError, KeyError, IndexError, TypeError):
                continue                        # one broken edit does not lose the chat
    return state


def folder_of(chat: Path) -> str:
    """The project folder of a chat: workspace.json next to its chatSessions folder ('' when it names none)."""
    try:
        w = json.loads((chat.parent.parent / "workspace.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    uri = w.get("folder") if isinstance(w, dict) else ""
    if not isinstance(uri, str) or not uri.startswith("file://"):
        return ""
    from ..locate import _file_uri_path
    return _file_uri_path(uri)


def _model(m: str) -> str:
    """'copilot/gpt-5-mini' -> 'gpt-5-mini'; 'claude-haiku-4.5' -> 'claude-haiku-4-5' (the price table's names)."""
    m = str(m or "").split("/")[-1]
    return m.replace(".", "-") if m.startswith("claude-") else m


def _ms(v) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(v) / 1000, tz=timezone.utc) if v else None
    except (TypeError, ValueError, OSError):
        return None


def describe(name: str, raw) -> tuple[str, str, str, str, int]:
    """(tool, kind, plain-words label, target, written chars) for one tool call."""
    try:
        a = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except ValueError:
        a = {}
    a = a if isinstance(a, dict) else {}
    f = str(a.get("filePath") or a.get("path") or "")
    if name == "read_file":
        rng = f" (lines {a['startLine']}–{a['endLine']})" if a.get("startLine") and a.get("endLine") else ""
        return name, "read", f"read `{_name(f)}`{rng}", f, 0
    if name == "list_dir":
        return name, "search", f"listed `{_name(f) or f}`", f, 0
    if name in ("grep_search", "file_search", "semantic_search", "search"):
        q = str(a.get("query") or a.get("pattern") or "")
        return name, "search", f"searched for `{short(q, 60)}`", q, 0
    if name in ("run_in_terminal", "run_command"):
        return (name,) + _command(name, str(a.get("command") or "")) + (0,)
    if name == "apply_patch":
        return _patch(name, str(a.get("input") or a.get("patch") or ""))
    if name == "create_file":
        return name, "write", f"wrote `{_name(f)}`", f, len(str(a.get("content") or ""))
    if name in ("replace_string_in_file", "insert_edit_into_file", "edit_file", "multi_replace_string_in_file"):
        new = a.get("newString") or a.get("code") or a.get("replacements") or ""
        return name, "edit", f"edited `{_name(f)}`", f, len(new if isinstance(new, str) else json.dumps(new))
    if name in ("fetch_webpage", "open_simple_browser"):
        u = a.get("urls") or a.get("url") or ""
        u = u[0] if isinstance(u, list) and u else u
        return name, "web", f"opened {short(str(u), 70)}", str(u), 0
    if name == "manage_todo_list":
        return name, "plan", "updated its to-do list", "", 0
    if name in ("runSubagent", "run_subagent"):
        return name, "helper", "started a helper", "", 0
    if name.startswith("mcp_"):
        return name, "other", f"used {name.split('_')[-1]}", "", 0
    return name, "other", f"used {name}", "", 0


def _result_text(v) -> str:
    content = v.get("content") if isinstance(v, dict) else v
    if isinstance(content, list):
        return "\n".join(str(x.get("value") or x.get("text") or "") if isinstance(x, dict) else str(x) for x in content)
    return str(content or "")


def _reply_text(q: dict) -> str:
    """The reply as shown, when the request has no tool rounds: its plain text parts."""
    return "".join(str(x.get("value") or "") for x in q.get("response") or [] if isinstance(x, dict) and not x.get("kind"))


class VSCodeChatReader:
    name = "copilot-vscode"

    def can_read(self, path: Path) -> bool:
        return is_chat(path)

    def read(self, path: Path) -> Session:
        chat = replay(path)
        s = Session(agent=self.name, session_id=str(chat.get("sessionId") or path.stem), source=str(path),
                    cwd=folder_of(path), title=str(chat.get("customTitle") or ""),
                    billed_per_token=False, tokens_exact=False)
        turns: list[Turn] = [Turn(n=0, kind="start", text="(before your first message)")]
        conv_chars = 0                          # what the next call carries of the chat so far
        counted = []                            # (calls of a request, its prompt tokens, its output tokens)
        auto = False
        last_end = None
        for q in chat.get("requests") or []:
            if not isinstance(q, dict):
                continue
            text = str((q.get("message") or {}).get("text") or "").strip()
            start = _ms(q.get("timestamp"))
            if not text:
                continue
            waited = (start - last_end).total_seconds() if start and last_end and start > last_end else 0.0
            n = len(turns)
            turn = Turn(n=n, kind=said_kind(text), text=short(text, 300), ts=start, waited_s=waited)
            turn.events.append(Event(turn.kind, "you ran a command" if turn.kind == "command" else "you typed",
                                     short(text, 300), len(text), start))
            turns.append(turn)
            res = q.get("result") or {}
            md = res.get("metadata") or {}
            results = md.get("toolCallResults") or {}
            rounds = [r for r in md.get("toolCallRounds") or [] if isinstance(r, dict)] or [{"response": _reply_text(q)}]
            req_model = str(q.get("modelId") or "")
            auto = auto or req_model.endswith("/auto")
            elapsed = float((res.get("timings") or {}).get("totalElapsed") or 0)
            end = start + timedelta(milliseconds=elapsed) if start else None
            user_chars = len(text) + len(json.dumps(q.get("variableData") or {}, ensure_ascii=False)) // 2
            carried = conv_chars + user_chars
            calls = []
            for j, r in enumerate(rounds):
                model = _model(r.get("modelId") or req_model)
                ts_ = _ms(r.get("timestamp")) or start
                c = Call(n=0, msg_id=f"{n}.{j + 1}", agent="main", model=model if model != "auto" else "auto",
                         usage=Usage(), ts_first=ts_, ts_last=ts_, turn=n)
                reply = str(r.get("response") or "")
                c.text_chars = len(reply)
                if reply.strip():
                    c.first_text, c.last_text = short(reply, 120), reply.strip()[-300:]
                think = int(((r.get("thinking") or {}).get("tokens")) or 0)
                out_chars = len(reply)
                for tc in r.get("toolCalls") or []:
                    if not isinstance(tc, dict):
                        continue
                    raw = tc.get("arguments")
                    tool, kind, label, target, written = describe(str(tc.get("name") or "?"), raw)
                    a = Action(id=str(tc.get("id") or ""), tool=tool, kind=kind, label=label, target=target,
                               args_chars=len(raw if isinstance(raw, str) else json.dumps(raw or {})), written_chars=written, ts=ts_)
                    txt = _result_text(results.get(a.id)) if a.id in results else ""
                    a.result_chars, a.result_hash, a.result_flags = len(txt), fingerprint(txt), text_flags(txt)
                    a.is_error = txt.lstrip().lower().startswith(("error", "failed")) or "exit code: 1" in txt[:400].lower()
                    c.actions.append(a)
                    out_chars += a.args_chars
                # estimated from the size of the text: what it carried in, what it wrote (thinking: Copilot's count)
                c.usage = Usage(fresh_input=max(1, carried // 4), output=max(1, out_chars // 4 + think),
                                thinking=think or None)
                carried += out_chars + sum(a.result_chars for a in c.actions)
                calls.append(c)
            # where Copilot counted the tokens of this request, the estimate is scaled to them: the input so the last
            # call carries what Copilot counted, the output so all its calls together wrote what it counted
            p_tok = int(md.get("promptTokens") or q.get("promptTokens") or 0)
            o_tok = int(md.get("outputTokens") or q.get("completionTokens") or 0)
            if p_tok or o_tok:
                counted.append((calls, p_tok, o_tok))
            for k, c in enumerate(calls):
                c.ts_last = c.ts_first if k + 1 < len(calls) else (end or c.ts_first)
            turn.calls = calls
            conv_chars = carried
            last_end = end or start
        self._scale(counted, [c for t in turns for c in t.calls])
        s.turns = [t for t in turns if t.calls or t.n > 0]
        main = [c for t in s.turns for c in t.calls]
        for i, c in enumerate(main, 1):
            c.n = i
        if not s.title:
            s.title = next((t.text for t in s.turns if t.n > 0), "(no message from you)")
        stamps = [x for c in main for x in (c.ts_first, c.ts_last) if x]
        s.started, s.ended = (min(stamps), max(stamps)) if stamps else (None, None)
        models: dict[str, int] = {}
        for c in main:
            models[c.model] = models.get(c.model, 0) + 1
        s.model = max(models, key=models.get) if models else ""
        s.setup = Setup()                       # not in these files: its size is inside Copilot's own counts
        s.notes.append("Copilot Chat in VS Code records few token counts and nothing about the cache: most tokens here "
                       "are estimated from the size of the text, so the cost is a range.")
        if any(c.usage.fresh_input >= MAX_PROMPT for c in main):
            s.notes.append(f"Some calls are counted at {MAX_PROMPT // 1000}K tokens, an assumed prompt limit: Copilot "
                           "trims older tool output to fit the model, and the log does not say how much.")
        if auto:
            s.notes.append("Copilot picked the model itself (\"auto\") and did not always record which one, so some "
                           "calls are priced at a default rate.")
        return s

    @staticmethod
    def _scale(counted: list, every_call: list[Call]) -> None:
        """Bring the estimates in line with Copilot's own counts where it gave them; the calls of requests it did not
        count are scaled by the median of those corrections (text alone misses the system prompt and the tools)."""
        ratios, done = [], set()
        for calls, p_tok, o_tok in counted:
            if p_tok and calls[-1].usage.fresh_input:
                k = p_tok / calls[-1].usage.fresh_input
                ratios.append(k)
                for c in calls:
                    c.usage.fresh_input = max(1, round(c.usage.fresh_input * k))
                    done.add(id(c))
            if o_tok:
                est = sum(c.usage.output for c in calls) or 1
                for c in calls:
                    c.usage.output = max(1, round(c.usage.output * o_tok / est))
                    if c.usage.thinking:
                        c.usage.thinking = min(c.usage.thinking, c.usage.output)
        k = sorted(ratios)[len(ratios) // 2] if ratios else 1.0
        for c in every_call:
            if id(c) not in done:
                c.usage.fresh_input = min(MAX_PROMPT, max(1, round(c.usage.fresh_input * k)))
