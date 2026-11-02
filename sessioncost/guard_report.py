"""The live guard's report across every guarded chat: what it blocked and asked, why, where, by which agent, and
whether each chat's log is still intact. Read from the guard's own logs on this computer; nothing is sent.

    python guard.py report            writes ~/.sessioncost/reports/guard.html and prints its path
    python guard.py report --json     prints the same numbers as JSON
"""
from __future__ import annotations

import collections
import html
import json
import os
from pathlib import Path

from .guard import WHY, home, verify_status

AGENT = {"claude": "Claude Code", "codex": "Codex", "cursor": "Cursor", "antigravity": "Antigravity"}
BROKEN = {"incomplete": "cut off", "unreadable": "could not be read"}     # any other failed check: "edited"


def collect(folder: Path | None = None) -> dict:
    from .analyse import _hide_keys
    from .risks import SEVERITY
    chats, steps = [], []
    kinds = {"blocked": collections.Counter(), "asked": collections.Counter()}
    days = collections.defaultdict(collections.Counter)
    for p in sorted((folder or home()).glob("*.jsonl")):
        v = verify_status(p)
        entries = v["entries"]
        if not entries and v["status"] != "unreadable":                 # a log it cannot read is shown, not skipped
            continue
        c = {"id": p.stem, "first": entries[0].get("ts", "") if entries else "", "last": entries[-1].get("ts", "") if entries else "",
             "cwd": "", "via": "claude", "turns": 0, "checked": 0, "blocked": 0, "asked": 0, "allowed": 0, "injections": 0,
             "intact": v["status"] in ("intact", "empty"), "status": v["status"]}
        for e in entries:
            ev = e.get("event")
            if e.get("cwd") and not c["cwd"]:
                c["cwd"], c["via"] = e["cwd"], e.get("via") or c["via"]
            if ev == "prompt":
                c["turns"] = max(c["turns"], int(e.get("turn") or 0))
            elif ev == "injection":
                c["injections"] += 1
            elif ev == "allowed":
                c["allowed"] += 1
            elif ev == "pre":
                c["checked"] += 1
                d = e.get("decision")
                if d in ("blocked", "asked"):
                    c[d] += 1
                    days[e.get("ts", "")[:10]][d] += 1
                    reasons = [m for m in e.get("marks") or [] if SEVERITY.get(m) != "info"] or ["other"]
                    kinds[d].update(reasons)
                    steps.append({"ts": e.get("ts", ""), "chat": p.stem, "decision": d, "tool": e.get("tool", ""),
                                  "what": _hide_keys(e.get("what", "")), "marks": reasons,   # a key in a step: "[secret hidden]"
                                  "helper": e.get("agent") not in (None, "main")})
        c["project"] = os.path.basename(c["cwd"].rstrip("\\/")) or "?"
        for s in steps:
            if s["chat"] == p.stem:
                s["project"], s["via"] = c["project"], c["via"]
        chats.append(c)
    chats.sort(key=lambda c: c["last"], reverse=True)
    steps.sort(key=lambda s: s["ts"], reverse=True)
    total = collections.Counter()
    for c in chats:
        total.update({k: c[k] for k in ("checked", "blocked", "asked", "allowed", "injections")})
    by = lambda key: sorted(((k, sum(c["blocked"] for c in chats if c[key] == k), sum(c["asked"] for c in chats if c[key] == k),
                              sum(1 for c in chats if c[key] == k)) for k in {c[key] for c in chats}), key=lambda r: -(r[1] + r[2]))
    return {"chats": chats, "steps": steps, "total": dict(total), "intact": sum(c["intact"] for c in chats),
            "kinds": {d: kinds[d].most_common() for d in kinds}, "days": sorted((d, v["blocked"], v["asked"]) for d, v in days.items()),
            "by_agent": by("via"), "by_project": by("project")}


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def render(r: dict, folder: Path | None = None) -> str:
    t, chats = r["total"], r["chats"]
    tile = lambda n, label, cls="": f'<div class="tile {cls}"><b class="num">{n}</b><span>{label}</span></div>'
    reasons = sorted({k for d in r["kinds"].values() for k, _ in d},
                     key=lambda k: -(dict(r["kinds"]["blocked"]).get(k, 0) * 10 + dict(r["kinds"]["asked"]).get(k, 0)))
    reason_rows = "".join(f'<tr><td>{_e(WHY.get(k, k))}</td>'
                          f'<td class="n num bad">{dict(r["kinds"]["blocked"]).get(k, 0) or ""}</td>'
                          f'<td class="n num warn">{dict(r["kinds"]["asked"]).get(k, 0) or ""}</td></tr>' for k in reasons)
    peak = max([b + a for _, b, a in r["days"]] or [1])
    day_rows = "".join(f'<tr><td class="num">{_e(d)}</td><td class="bars"><span class="b" style="width:{100 * b / peak:.1f}%"></span>'
                       f'<span class="a" style="width:{100 * a / peak:.1f}%"></span></td><td class="n num">{b}</td><td class="n num">{a}</td></tr>'
                       for d, b, a in r["days"][::-1][:30])
    group = lambda rows, name: "".join(f'<tr><td>{_e(name(k))}</td><td class="n num">{n}</td><td class="n num bad">{b or ""}</td>'
                                       f'<td class="n num warn">{a or ""}</td></tr>' for k, b, a, n in rows)
    chat_rows = "".join(
        f'<tr><td class="num">{_e(c["last"].replace("T", " ")[:16])}</td><td>{_e(AGENT.get(c["via"], c["via"]))}</td>'
        f'<td title="{_e(c["cwd"])}">{_e(c["project"])}</td><td class="n num">{c["turns"] or ""}</td><td class="n num">{c["checked"]}</td>'
        f'<td class="n num bad">{c["blocked"] or ""}</td><td class="n num warn">{c["asked"] or ""}</td>'
        f'<td class="n num">{c["injections"] or ""}</td><td>{"untouched" if c["intact"] else "<b class=bad>" + BROKEN.get(c["status"], "edited") + "</b>"}</td>'
        f'<td class="muted num">{_e(c["id"][:8])}</td></tr>' for c in chats[:200])
    step_rows = "".join(
        f'<tr><td class="num">{_e(s["ts"].replace("T", " ")[:16])}</td><td class="{"bad" if s["decision"] == "blocked" else "warn"}">'
        f'{"stopped" if s["decision"] == "blocked" else "asked you"}</td><td>{_e(s.get("project", ""))}<div class="muted">{_e(AGENT.get(s.get("via"), s.get("via", "")))}'
        f'{" · a helper" if s["helper"] else ""}</div></td><td><code>{_e(s["tool"])}</code></td><td><code>{_e(s["what"])}</code></td>'
        f'<td>{_e("; ".join(WHY.get(m, m) for m in s["marks"]))}</td></tr>' for s in r["steps"][:100])
    broken = len(chats) - r["intact"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SessionCost guard report</title><style>
:root{{--bg:oklch(0.17 0.012 280);--surface:oklch(0.21 0.014 280);--ink:oklch(0.96 0.004 280);--ink-2:oklch(0.72 0.012 280);
--line:oklch(0.30 0.012 280);--bad:oklch(0.70 0.19 20);--warn:oklch(0.78 0.14 75);--ok:oklch(0.72 0.13 155);--bar:oklch(0.62 0.17 265);color-scheme:dark}}
@media (prefers-color-scheme: light){{:root{{--bg:oklch(0.945 0.008 265);--surface:oklch(1 0 0);--ink:oklch(0.24 0.02 260);
--ink-2:oklch(0.45 0.015 260);--line:oklch(0.88 0.008 265);--bad:oklch(0.53 0.20 25);--warn:oklch(0.55 0.13 70);--ok:oklch(0.51 0.13 155);color-scheme:light}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "Segoe UI",system-ui,sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:28px 16px 60px}}h1{{font-size:26px;margin:0 0 4px}}h2{{font-size:17px;margin:34px 0 10px}}
.muted{{color:var(--ink-2);font-size:12.5px}}.num{{font-variant-numeric:tabular-nums;font-family:Consolas,ui-monospace,monospace}}
code{{font-family:Consolas,ui-monospace,monospace;font-size:12.5px;overflow-wrap:anywhere}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:18px}}
.tile{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 14px}}.tile b{{display:block;font-size:24px}}
.tile span{{color:var(--ink-2);font-size:13px}}.tile.bad b{{color:var(--bad)}}.tile.warn b{{color:var(--warn)}}.tile.ok b{{color:var(--ok)}}
.wrap{{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:10px}}
table{{border-collapse:collapse;width:100%;font-size:13.5px}}th,td{{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{color:var(--ink-2);font-weight:600;font-size:12.5px;white-space:nowrap}}td.n,th.n{{text-align:right}}tr:last-child td{{border-bottom:0}}
.bad{{color:var(--bad)}}.warn{{color:var(--warn)}}.cols{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}}
.bars{{width:55%;min-width:120px}}.bars span{{display:inline-block;height:10px;vertical-align:middle}}.bars .b{{background:var(--bad)}}.bars .a{{background:var(--warn)}}
</style></head><body><main>
<h1>SessionCost guard report</h1>
<p>What the SessionCost guard did on this computer. The guard is an optional safety check: turned on
(<code>/livesession</code> in Claude Code, or <code>sessioncost guard on</code>; every chat until it is turned off), it looks at every step your AI agent is about to take, stops the dangerous ones and asks you
before the risky ones. This page covers <b>every chat where it was on, in every project</b>.</p>
<div class="tiles">{tile(len(chats), "chats with the guard on")}{tile(t.get("checked", 0), "risky steps it noticed")}
{tile(t.get("blocked", 0), "stopped (did not run)", "bad")}{tile(t.get("asked", 0), "asked you first", "warn")}{tile(t.get("allowed", 0), "you said yes")}
{tile(t.get("injections", 0), "hidden instructions found", "bad" if t.get("injections") else "")}
{tile(f'{r["intact"]}/{len(chats)}', "records untouched", "bad" if broken else "ok")}</div>
<p class="muted"><b>Stopped</b>: the step never ran. <b>Asked you first</b>: you were asked before it ran; <b>you said yes</b> counts the
ones you let through. <b>Hidden instructions</b>: a web page or file the agent read tried to give it orders (prompt injection).
<b>Records untouched</b>: each chat's record is chained line by line, so an edited or deleted line would show as "edited".
Read from the guard's own records ({_e(folder or home())}); nothing here was sent anywhere.</p>
<h2>Why the guard stepped in</h2><div class="wrap"><table><thead><tr><th>Reason</th><th class="n">Stopped</th><th class="n">Asked you</th></tr></thead>
<tbody>{reason_rows or '<tr><td colspan="3" class="muted">Nothing stopped or asked about yet.</td></tr>'}</tbody></table></div>
<div class="cols"><div><h2>By agent</h2><div class="wrap"><table><thead><tr><th>Agent</th><th class="n">Chats with the guard on</th><th class="n">Stopped</th><th class="n">Asked you</th></tr></thead>
<tbody>{group(r["by_agent"], lambda k: AGENT.get(k, k))}</tbody></table></div></div>
<div><h2>By project</h2><div class="wrap"><table><thead><tr><th>Project (folder)</th><th class="n">Chats with the guard on</th><th class="n">Stopped</th><th class="n">Asked you</th></tr></thead>
<tbody>{group(r["by_project"][:15], str)}</tbody></table></div></div></div>
<h2>By day <span class="muted">(red: stopped, amber: asked you; the last 30 days with any)</span></h2><div class="wrap"><table><thead><tr><th>Day</th><th></th>
<th class="n">Stopped</th><th class="n">Asked you</th></tr></thead><tbody>{day_rows}</tbody></table></div>
<h2>The latest steps it stopped or asked about</h2><div class="wrap"><table><thead><tr><th>When</th><th>What the guard did</th><th>Project</th><th>Tool</th><th>The step</th><th>Why</th></tr></thead>
<tbody>{step_rows}</tbody></table></div>
<h2>Each chat with the guard on</h2><div class="wrap"><table><thead><tr><th>Last step</th><th>Agent</th><th>Project</th><th class="n">Turns</th><th class="n">Risky steps</th>
<th class="n">Stopped</th><th class="n">Asked you</th><th class="n">Hidden instructions</th><th>Record</th><th>Chat</th></tr></thead><tbody>{chat_rows}</tbody></table></div>
</main></body></html>"""


def write(as_json: bool = False, out: Path | None = None, folder: Path | None = None) -> str:
    r = collect(folder)
    if as_json:
        return json.dumps(r, indent=1)
    out = out or Path.home() / ".sessioncost" / "reports" / "guard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(r, folder), encoding="utf-8")
    return (f"SessionCost guard report: {len(r['chats'])} chats with the guard on, {r['total'].get('blocked', 0)} steps stopped, "
            f"{r['total'].get('asked', 0)} asked you first, {r['intact']}/{len(r['chats'])} records untouched: {out}")
