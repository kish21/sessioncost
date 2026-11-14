"""sessioncost fix: the token-waste to-do list for this project's newest chat, and the change that fixes each item.

Token waste only: safety belongs to the guard (/liveguard), so nothing here writes a safety rule or a permission.
SessionCost calls no AI: the detectors in fixes/ find the waste, rules.py has the rule texts, and the user's own
agent writes any words (the handover). The loop is fix -> verify -> remember.

Everything kept on this computer lives under one folder, base() (~/.sessioncost, or $SESSIONCOST_DIR):
  fixes/<project>.json        what was applied here, when, which files; the chats seen since (verify)
  fixes/live/<project>.json   the newest chat's to-do list, written after each answer while `fix auto` is on
  fixes/chats/<chat>.json     what one chat was already shown (each item once per chat)
  backups/<project>/          the files as they were before each fix, for `sessioncost fix --undo`
  handover/<project>.md       the handover the agent writes when a chat has grown too long
The `fix auto` switch sits in the guard's config.json, next to the `auto` reports switch.
"""
from __future__ import annotations

import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import practices
from .fixer_plan import Plan, plan
from .fixer_store import (ASKED_FIX, BIG_TIP_TOKENS, CHAT_KEEP_S, HANDOVER_ASK, HEAD, KEEP_SEEN, MIN_TIP_TOKENS,
                          NOTE_MAX_AGE_S, OFFER_QUIET_S, RULE_OF, SAY_FIX, SAY_HANDOVER, SETTINGS, _load, _norm, _now, _save, _sha, _size, _tok, _ts, _usd,
                          auto_on, backups_dir, base, chat_file, find_handover, fixes_file, handover_file, known,
                          live_file, offered_file, project_key, project_path, start_folder)
# Names other modules and tests use as fixer.<name>; they live in fixer_store.py and fixer_plan.py.
from .fixer_plan import for_report, render  # noqa: F401
from .fixer_store import LONG_CHAT, auto_text, budget_text, budget_usd, learn, learned, long_chat, set_auto, set_budget, set_long_chat  # noqa: F401


def apply(p: Plan) -> tuple[int, str]:
    """Back up, then write every change of the plan. (exit code, what happened)."""
    todo = [i for i in p.items if i.can_apply]
    out: list[str] = []
    paths = sorted(p.finals)
    for path in paths:
        now = Path(path).read_bytes() if Path(path).exists() else None
        if now != p.originals.get(path):
            return 1, f"{path} changed while SessionCost was working; nothing was applied. Run `sessioncost fix` again."
    if p.finals:                               # the fixes, and the handover's .gitignore line
        bid = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        folder = backups_dir(p.project) / bid
        folder.mkdir(parents=True, exist_ok=True)
        entries, data = [], {}
        for n, path in enumerate(paths):
            raw = p.originals.get(path)
            bom = b"\xef\xbb\xbf" if raw and raw.startswith(b"\xef\xbb\xbf") else b""
            data[path] = bom + p.finals[path].encode("utf-8")
            made, d = [], Path(path).parent
            while not d.exists() and d != d.parent:          # folders the fix makes: undo takes them out again
                made.append(str(d))
                d = d.parent
            e = {"path": path, "existed": raw is not None, "backup": "", "written_sha256": _sha(data[path]),
                 "made_dirs": made}
            if raw is not None:
                e["backup"] = f"{n}-{Path(path).name}"
                (folder / e["backup"]).write_bytes(raw)
            entries.append(e)
        mf = backups_dir(p.project) / "manifest.json"
        _save(mf, _load(mf, []) + [{"id": bid, "at": _now(), "chat": p.chat,
                                    "items": [i.id for i in todo] or ["long-chat"], "entries": entries}])
        for path in paths:                     # the backup is on disk before any file changes
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(data[path])
    if not todo and p.finals:
        out.append(f"Added {handover_file(p.project).name} to .gitignore, so the handover is not committed "
                   "(`sessioncost fix --undo` takes it out).")
    if todo:
        mem = _load(fixes_file(p.project), {})
        mem["project"] = str(p.project)
        mem["applied"] = [r for r in mem.get("applied", []) if r.get("id") not in {i.id for i in todo}] + [
            {"id": i.id, "title": i.title, "at": _now(), "chat": p.chat,
             "files": [str(c.path) for c in [i.change, *i.also]],
             "before_tokens": i.tokens, "before_usd": i.usd, "before_calls": p.calls, "backup": bid} for i in todo]
        _save(fixes_file(p.project), mem)
        out.append(f"Applied {len(todo)} fix{'es' if len(todo) != 1 else ''} (backup kept; `sessioncost fix --undo` "
                   "puts it back):")
        for i in todo:
            where = []
            for c in [i.change, *i.also]:      # a rule goes into every agent's file in the project
                try:
                    where.append(c.path.relative_to(p.project).as_posix())
                except ValueError:
                    where.append(c.path.as_posix())
            out.append(f"  - {i.title}: {'a rule added to' if i.kind == 'rule' else 'set in'} {', '.join(where)}")
        out.append(f"In this chat they would have cut {_size(p.saving_tokens, p.saving_usd)}. The next reports show "
                   "what they save from now on.")
    for i in p.items:
        if i.problem:
            out.append(f"Not changed: {i.title}: {i.problem}")
    for i in p.items:
        if i.kind == "manual" or (i.kind == "setting" and i.steps):
            out.append(f"{'You do the rest' if i.change else 'You do this one'}: {i.title}\n"
                       + "\n".join("  " + x for x in i.steps.splitlines()))
    if any(i.kind == "handover" for i in p.items):
        h = handover_file(p.project)
        out.append(f"Handover: write it now in {h.as_posix()} (replace what is there): the goal, what is done, files "
                   "changed, tests run, what is next. Show it in your reply, then tell the user to start a new chat "
                   "and say \"continue from handover\" there.")
        out.append("In the same handover, add a short section \"Other token waste in this chat\": what cost tokens in "
                   "this chat that SessionCost does not know yet. It knows: " + "; ".join(known()) + ". Leave out "
                   "anything on that list, except what you already recorded in this chat with `fix learn`: list that "
                   "here too, and do not run it again. For each new one (at most three), run "
                   f"`{run_cmd()} fix learn \"<what happened, one line>\" --rule \"<one rule that prevents it, one line>\" "
                   f"--agent {p.agent} --project {_q(p.project.as_posix())}`; SessionCost keeps it and offers the rule in "
                   "every project from then on. No new waste: write \"none\" and run nothing.")
    if not out:
        out.append("Nothing to apply: no change is needed for what was found.")
    return 0, "\n".join(out)


def undo(project) -> tuple[int, str]:
    """Put back this project's newest fix, unless a file changed since SessionCost wrote it."""
    project = project_path(project)
    mf = backups_dir(project) / "manifest.json"
    man = _load(mf, [])
    if not man:
        return 0, "Nothing to undo in this project: no fix was applied here (or every fix was undone)."
    last = man[-1]
    folder = backups_dir(project) / str(last.get("id"))
    changed, keep = [], []
    for e in last.get("entries", []):
        p = Path(e["path"])
        if not p.is_file() or _sha(p.read_bytes()) != e.get("written_sha256"):
            changed.append(p.name)
            keep.append(e)
    if changed and len(keep) == len(last.get("entries", [])):
        return 1, (f"Not undone: {', '.join(changed)} changed after SessionCost's fix, and undoing would lose that. "
                   f"Take the SessionCost lines out by hand (they are under \"{HEAD}\" or in {SETTINGS.as_posix()}).")
    done = []
    for e in last.get("entries", []):
        p = Path(e["path"])
        if e in keep:
            continue                          # you changed it since: it stays as you left it
        if e.get("existed"):
            p.write_bytes((folder / e["backup"]).read_bytes())
            done.append(f"  - put back {p.as_posix()}")
        else:
            p.unlink()
            done.append(f"  - removed {p.as_posix()} (SessionCost had made it)")
            for d in e.get("made_dirs", []):
                try:
                    Path(d).rmdir()                  # only when empty: anything you put there stays
                except OSError:
                    break
    mem = _load(fixes_file(project), {})
    if keep:                                  # only part of it: the rest stays, and a later undo can try it again
        kept = {_norm(e["path"]) for e in keep}
        _save(mf, man[:-1] + [{**last, "entries": keep}])
        mem["applied"] = [r for r in mem.get("applied", []) if r.get("backup") != last.get("id")
                          or any(_norm(f) in kept for f in r.get("files") or [])]
        _save(fixes_file(project), mem)
        return 0, ("Undone:\n" + "\n".join(done) + f"\nNot undone: {', '.join(changed)} changed after SessionCost's "
                   "fix, so it stays as it is now. Take the SessionCost lines out of it by hand if you want them gone "
                   f"(they are under \"{HEAD}\" or in {SETTINGS.as_posix()}).")
    _save(mf, man[:-1])
    shutil.rmtree(folder, ignore_errors=True)
    mem["applied"] = [r for r in mem.get("applied", []) if r.get("backup") != last.get("id")]
    _save(fixes_file(project), mem)
    return 0, "Undone:\n" + "\n".join(done)


# ---- verify and remember ----
def record_seen(a: dict, project) -> None:
    """After a chat: note its waste and unused-tool targets, for verify and the 5-chat unused-tool check."""
    p = fixes_file(project_path(project))
    mem = _load(p, {})
    meta = a.get("meta") or {}
    seen = mem.setdefault("seen", {})
    seen[str(meta.get("session_id") or "")] = {
        "started": meta.get("started"), "calls": int((a.get("totals") or {}).get("calls") or 0),
        "waste": {f["id"]: int(f.get("saving_tokens") or 0) for f in a.get("fixes") or []},
        "waste_usd": {f["id"]: float(f.get("saving_usd") or 0.0) for f in a.get("fixes") or []},
        "unused_targets": {f["id"]: list(f.get("targets") or []) for f in a.get("fixes") or []
                           if f.get("id") in ("unused-tools", "unused-builtin")}}
    if len(seen) > KEEP_SEEN:
        mem["seen"] = dict(sorted(seen.items(), key=lambda x: -_ts(x[1].get("started")))[:KEEP_SEEN])
    _save(p, mem)


def verify(project) -> list[dict]:
    mem = _load(fixes_file(project_path(project)), {})
    out = []
    for r in {r.get("id"): r for r in mem.get("applied", [])}.values():
        at, day = _ts(r.get("at")), str(r.get("at"))[:10]
        later = [s for c, s in (mem.get("seen") or {}).items() if c != r.get("chat") and _ts(s.get("started")) > at]
        base_ = {"id": r.get("id"), "title": r.get("title"), "at": r.get("at")}
        if str(r.get("id")).startswith("learned-"):
            out.append({**base_, "status": "not measured", "tokens": 0,
                        "line": f"{r.get('title')} (fixed {day}, found by your agent): no check measures it yet."})
            continue
        if not later:
            out.append({**base_, "status": "waiting", "tokens": 0,
                        "line": f"{r.get('title')} (fixed {day}): no new chat since the fix yet."})
            continue
        newest = max(later, key=lambda s: _ts(s.get("started")))
        back = int((newest.get("waste") or {}).get(r.get("id"), 0))
        back_usd = float((newest.get("waste_usd") or {}).get(r.get("id"), 0.0))
        if back:
            out.append({**base_, "status": "came back", "tokens": back,
                        "line": f"{r.get('title')} (fixed {day}): it came back, {_size(back, back_usd)} of it in your "
                                "newest chat."})
            continue
        chats = f"{len(later)} chat{'s' if len(later) != 1 else ''} since"
        if r.get("id") in RULE_OF:
            # a rule prevents waste that might never have happened in those chats: say what is known, no number
            out.append({**base_, "status": "saved", "tokens": 0,
                        "line": f"{r.get('title')} (fixed {day}): no waste of this kind in {chats}."})
            continue
        rate = int(r.get("before_tokens") or 0) / max(1, int(r.get("before_calls") or 0))
        saved = sum(max(0, round(rate * int(s.get("calls") or 0)) - int((s.get("waste") or {}).get(r.get("id"), 0)))
                    for s in later)
        # the same money per token as before the fix (an older record has none: tokens only)
        per_tok = float(r.get("before_usd") or 0.0) / max(1, int(r.get("before_tokens") or 0))
        money = f" (~{_usd(saved * per_tok)})" if saved * per_tok >= 0.005 else ""
        out.append({**base_, "status": "saved", "tokens": saved, "usd": saved * per_tok,
                    "line": f"{r.get('title')} (fixed {day}): ~{_tok(saved)} fewer tokens{money} sent in {chats}, mostly "
                            "cheap cache re-reads (what it sent with each call before the fix, times the calls since)."})
    return out


def verify_lines(project) -> list[str]:
    return [v["line"] for v in verify(project)]


# ---- live: the to-do list after each answer, shown as the last line of the next reply ----
def write_note(a: dict, project, home: Path | None = None) -> dict:
    p = plan(a, project, home=home)
    note = {"chat": p.chat, "agent": p.agent, "at": time.time(), "context": p.context,
            "items": [{"id": i.id, "kind": "learned" if i.id.startswith("learned-") else i.kind, "title": i.title,
                       "line": i.line, "tokens": i.tokens} for i in p.items if (i.can_apply or i.kind == "handover")]}
    _save(live_file(p.project), note)
    now = time.time()
    for f in (base() / "fixes" / "chats").glob("*.json"):
        try:
            if now - f.stat().st_mtime > CHAT_KEEP_S:
                f.unlink()
        except OSError:
            pass
    return note


def mark_shown(p: Plan) -> None:
    """`sessioncost fix` showed this chat's list: the live tip leaves those items out in that chat."""
    if not p.chat or not p.items:
        return
    st = _load(chat_file(p.chat), {})
    st["shown"] = sorted(set(st.get("shown", [])) | {i.id for i in p.items})
    _save(chat_file(p.chat), st)


def _q(s: str) -> str:
    return f'"{s}"' if re.search(r"\s", s) else s


def run_cmd() -> str:
    """This copy of SessionCost, as a command the agent can run (python.exe, never pythonw.exe: it prints nothing)."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").is_file():
        exe = exe.with_name("python.exe")
    root = Path(__file__).resolve().parent.parent
    run = _q((root / "run.py").as_posix()) if (root / "run.py").is_file() else "-m sessioncost"
    return f"{_q(exe.as_posix())} {run}"


def _quiet(note: dict, chat: str, cwd: str, st: dict) -> list[dict]:
    """The items worth a line in the chat now (see MIN_TIP_TOKENS and the rest above)."""
    same = note.get("chat") == chat
    shown = set(st.get("shown", []))
    offered = _load(offered_file(cwd), {})
    out = []
    for i in note.get("items", []):
        iid, tok, kind = str(i.get("id")), int(i.get("tokens") or 0), i.get("kind")
        if iid in shown or (iid == "long-chat" and not same):
            continue                               # once a chat; a long chat only in that chat
        if iid != "long-chat" and time.time() - float(offered.get(iid) or 0) < OFFER_QUIET_S:
            continue                               # offered in this project this week and not fixed: that was a no
        if kind not in ("handover", "learned") and tok < MIN_TIP_TOKENS:
            continue                               # small: `sessioncost fix` lists it
        if st.get("lists") and iid != "long-chat" and tok < BIG_TIP_TOKENS:
            continue                               # one list a chat, unless something big turned up
        out.append(i)
    if not any(i.get("kind") not in ("learned", "handover") for i in out):
        out = [i for i in out if i.get("kind") != "learned"]   # a learned rule rides along with measured waste only
    return out


def _one_line(items: list[dict], same: bool) -> str:
    waste = [i for i in items if i.get("id") != "long-chat" and i.get("kind") != "learned"]
    learnt = [i for i in items if i.get("kind") == "learned"]
    long = next((i for i in items if i.get("id") == "long-chat"), None)
    where = "this chat" if same else "your last chat here"
    parts = []
    if waste:
        def one(n: int, i: dict) -> str:
            bp = practices.get(str(i.get("id"))) if n == 0 else None   # the top item says why, in one short clause
            return f"{i.get('title') or i['id']} (~{_tok(i['tokens'])} tokens{': ' + bp.short if bp else ''})"
        parts.append(f"token waste in {where}: " + "; ".join(one(n, i) for n, i in enumerate(waste)))
    if learnt:
        parts.append("a rule your agent found in an earlier chat: " + "; ".join(str(i.get("title") or i["id"])
                                                                              for i in learnt))
    # its own words, never "Shall I ...?": the agent may have asked its own question higher up the reply, and a bare
    # "yes" would answer either (owner, 2026-11-11). The command is named too, for those who prefer to type it.
    typed = "(or type /sessioncost fix)"
    line = f"💡 SessionCost: {'. Also '.join(parts)}." if parts else ""
    if not long:
        return f'{line} Say "{SAY_FIX}" {typed} to fix these.' if line else ""
    line = (line + " " if line else "💡 SessionCost: ") + long.get("line", "")
    if parts:
        return f'{line} Say "{SAY_FIX}" {typed} to fix these and write a handover, so you can start a fresh chat.'
    return f'{line} Say "{SAY_HANDOVER}" {typed} so you can start a fresh chat.'


def _same_agent(hook_agent: str, note_agent: str) -> bool:
    """The hooks' name (claude, codex, cursor, copilot, antigravity) and the reader's (claude-code, copilot-vscode, ...)."""
    return not hook_agent or not note_agent or note_agent.split("-")[0] == hook_agent


def tip(chat: str, cwd: str, prompt: str = "", start: bool = False, turn: str | None = None, agent: str = "") -> str:
    """What the agent is told at the start of a reply: the to-do list not shown yet in this chat (fix auto on), which
    it says as the last line of that reply (at the top it scrolled away while the agent worked: owner, 2026-11-11),
    and the handover when the user asks to continue from it. `turn`: a key that changes with each user message, for
    agents whose hook runs before every model call (Antigravity), so the list comes only at the start of a turn."""
    out: list[str] = []
    st = _load(chat_file(chat), {})
    dirty = False
    if turn is not None:
        if st.get("turn") == turn:
            return ""
        st["turn"], dirty = turn, True
        if st.get("pending"):                 # the list was shown last turn and this agent forgets it: the command
            out.append(f"If the user's message says \"{SAY_FIX}\" or \"{SAY_HANDOVER}\" (or names the SessionCost fixes you "
                       f"listed), run `{st['pending']}` "
                       "(keep only the ids they chose) and show the lines it prints. Otherwise ignore this.")
            st["pending"] = ""
    h = find_handover(cwd) if cwd else None
    if h and prompt and HANDOVER_ASK.search(prompt):
        out.append(f"The user wants to continue from the handover of an earlier chat: read "
                   f"{h.as_posix()} first, then carry on from its \"what is next\".")
    elif h and start and time.time() - h.stat().st_mtime < CHAT_KEEP_S:
        out.append(f"A handover from an earlier chat in this project is at {h.as_posix()}. Read it "
                   "only if the user asks to continue from it.")
    if cwd and auto_on():
        note = _load(live_file(cwd), {})
        # another agent's chat in this project (a colleague in Antigravity, say): its fixes go into that agent's file
        if note and not _same_agent(agent, str(note.get("agent") or "")):
            note = {}
        if note and time.time() - float(note.get("at") or 0) < NOTE_MAX_AGE_S:
            items = _quiet(note, chat, cwd, st)
            if items and ASKED_FIX.search(prompt or ""):
                # the user asked for the list or the report themselves: it shows the same, so no second list now
                st["shown"] = sorted(set(st.get("shown", [])) | {i["id"] for i in items})
                items, dirty = [], True
            if items:
                ids = ",".join(i["id"] for i in items)
                where = _q(Path(cwd).as_posix())
                of = f"{_q(str(note['chat']))} " if note.get("chat") else ""   # the chat the list came from
                cmd = f"{run_cmd()} fix -y {of}--only {ids} --project {where}"
                look = f"{run_cmd()} fix --preview {of}--only {ids} --project {where}"
                # chat-start text stays in the agent's context for the whole chat (Copilot, Cursor): said once, or the
                # agent ends every reply with it, even after the fix ran (owner, 2026-11-12)
                when = ("put this one line only at the end of your first reply in this chat and never repeat it in a "
                        "later reply (also not after the fix ran or the user said no); in that first reply put it "
                        if start else "end your reply with this one line, ")
                out.append(
                    "SessionCost (fix auto is on) found token waste. Answer the user's message as usual first; when "
                    f"all your work for it is done, {when}as written, as a quote block "
                    "(begin it with \"> \"), as the very last thing in the reply, so the user sees it where the chat "
                    f"stops:\n{_one_line(items, note.get('chat') == chat)}\n"
                    "Put nothing after that line. Do not end that reply with a question of your own: the user answers this one first. "
                    f"If the user asks what these are, run `{look}` and show what it prints. Only if the user says "
                    f"\"{SAY_FIX}\" or \"{SAY_HANDOVER}\" (or \"fix these\", \"fix all\", \"fix the first one\": all, "
                    f"or some), run `{cmd}` "
                    "(keep only the ids they chose) and show the lines it prints. A bare \"yes\" answers your own "
                    "question, not this line: do not run it then. If "
                    "it asks you to write a handover, do it. If running it is blocked or refused (auto mode, for "
                    "example), do not try another way: tell the user to type /sessioncost fix and answer yes there. "
                    "If they say no, or say nothing about it, drop it. Never run it without their yes.")
                st["shown"] = sorted(set(st.get("shown", [])) | {i["id"] for i in items})
                st["last_tip"] = {"at": time.time(), "line": _one_line(items, note.get("chat") == chat)}   # status.py
                st["lists"] = int(st.get("lists") or 0) + 1
                st["pending"] = cmd if turn is not None else ""
                dirty = True
                seen = _load(offered_file(cwd), {})
                _save(offered_file(cwd), {**{k: v for k, v in seen.items() if time.time() - float(v) < OFFER_QUIET_S},
                                          **{i["id"]: time.time() for i in items if i["id"] != "long-chat"}})
    if dirty:
        _save(chat_file(chat), st)
    return "\n".join(out)
