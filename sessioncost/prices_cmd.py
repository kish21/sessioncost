"""`sessioncost prices [set|import|team|update]`: the prices used, where each came from and its date, and your own."""
from __future__ import annotations

import argparse
import json
import urllib.request
from datetime import date
from pathlib import Path

from . import prices as P

# our own list, filled from LiteLLM's after the owner's review; fetched only when you run `sessioncost prices update`
UPDATE_URL = "https://raw.githubusercontent.com/kish21/sessioncost/main/sessioncost/prices.json"
TIMEOUT = 15
HOW = ("sessioncost prices                      the prices used, where each came from and its date\n"
       "sessioncost prices set <model> --input X --output Y [--cache-read R] [--cache-write W]\n"
       "sessioncost prices import <file.csv|file.json>   columns: model, input, output, cache_read, cache_write\n"
       "sessioncost prices team <file>|off      use a price file shared by your team\n"
       "sessioncost prices update               fetch SessionCost's newest price list (only when you run it)\n"
       "Prices are US dollars per million tokens.")


def _num(v: float) -> str:
    return f"{v:g}"


def _save_own(models: dict) -> Path:
    f = P.own_file()
    data = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    today = date.today().isoformat()
    data["models"] = {**(data.get("models") or {}), **{m: {**r, "set_on": today} for m, r in models.items()}}
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    return f


def _own_data_ok() -> str:
    """'' if your own price file can be added to; else why not."""
    f = P.own_file()
    try:
        if f.is_file() and not isinstance(json.loads(f.read_text(encoding="utf-8")), dict):
            raise ValueError("not a JSON object")
    except ValueError as e:
        return f"SessionCost prices: {f} could not be read ({e}). Fix or remove it first; nothing was changed."
    return ""


def cmd_set(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="sessioncost prices set")
    ap.add_argument("model")
    for name in ("input", "output", "cache-read", "cache-write", "cache-write-1h"):
        ap.add_argument("--" + name, required=name in ("input", "output"))
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 2)
    problem = _own_data_ok()
    if problem:
        print(problem)
        return 2
    model = P.normalise(a.model)
    given = {"input": a.input, "output": a.output, "cache_read": a.cache_read, "cache_write": a.cache_write,
             "cache_write_1h": a.cache_write_1h}
    try:
        r, as_input = P.fill(model, given, P.the_list()["models"])
    except ValueError as e:
        print(f"SessionCost prices: nothing saved: {e}.")
        return 2
    f = _save_own({model: r})
    print(f"SessionCost prices: {model} is now ${_num(r['input'])} input, ${_num(r['output'])} output per million tokens "
          f"(cache read ${_num(r['cache_read'])}, cache write ${_num(r['cache_write_5m'])}, "
          f"${_num(r['cache_write_1h'])} for 1 hour), saved in {f}. Reports now say \"at your own prices\".")
    if as_input:
        print("Cache prices were not given and the price list does not know this model, so they are priced as input. "
              "Add --cache-read and --cache-write if your provider lists them.")
    return 0


def cmd_import(argv: list[str]) -> int:
    if len(argv) != 1:
        print("sessioncost prices import <file.csv|file.json>")
        return 2
    problem = _own_data_ok()
    if problem:
        print(problem)
        return 2
    try:
        got = P.read_table(Path(argv[0]).expanduser(), P.the_list()["models"])
    except (OSError, ValueError) as e:
        print(f"SessionCost prices: nothing imported: {e}.")
        return 2
    f = _save_own(got)
    print(f"SessionCost prices: imported {len(got)} model{'s' if len(got) != 1 else ''} "
          f"({', '.join(sorted(got))}) into {f}. Reports now say \"at your own prices\".")
    return 0


def cmd_team(argv: list[str]) -> int:
    if not argv:
        t = P.team_file()
        print(f"SessionCost prices: team file {t}" if t else "SessionCost prices: no team file. "
              "`sessioncost prices team <file>` uses one your team shares (a .csv or .json).")
        return 0
    if argv[0] == "off":
        P.team_pointer().unlink(missing_ok=True)
        print("SessionCost prices: no team file now.")
        return 0
    f = Path(argv[0]).expanduser().resolve()
    try:
        got = P.read_table(f, P.the_list()["models"])
    except (OSError, ValueError) as e:
        print(f"SessionCost prices: team file not set: {e}.")
        return 2
    P.team_pointer().parent.mkdir(parents=True, exist_ok=True)
    P.team_pointer().write_text(json.dumps({"file": str(f)}), encoding="utf-8")
    print(f"SessionCost prices: using the team file {f} ({len(got)} models). It is read again for every report, so "
          "a change there is used at once. Your own prices still win over it.")
    return 0


def cmd_update() -> int:
    now = P.the_list()
    try:
        with urllib.request.urlopen(UPDATE_URL, timeout=TIMEOUT) as r:
            got = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError) as e:
        print(f"SessionCost prices: could not fetch the price list ({e}). Nothing changed; the list from "
              f"{P.checked_day(now['as_of'])} is still used.")
        return 1
    if not P.valid_list(got):
        print("SessionCost prices: the fetched price list is not complete, so it was not used. Nothing changed.")
        return 1
    if str(got["as_of"]) <= str(now["as_of"]):
        print(f"SessionCost prices: already up to date (prices checked {P.checked_day(now['as_of'])}).")
        return 0
    changed = sum(1 for m, r in got["models"].items() if any(r.get(c) != now["models"].get(m, {}).get(c)
                                                             for c in P.CLASSES))
    P.update_file().parent.mkdir(parents=True, exist_ok=True)
    P.update_file().write_text(json.dumps(got, indent=1), encoding="utf-8")
    print(f"SessionCost prices: updated to the list checked {P.checked_day(got['as_of'])} (was "
          f"{P.checked_day(now['as_of'])}); {changed} model{'s' if changed != 1 else ''} new or changed. Your own and "
          "your team's prices still win over it.")
    return 0


def show() -> int:
    p = P.load()
    days = P.age_days(p["as_of"])
    where = "fetched by `sessioncost prices update`" if p["as_of"] != P.built_in().get("as_of") else "built in"
    team, mine = P.team_file(), P.own_file()
    print("SessionCost prices, US dollars per million tokens")
    print(f"Price list: checked {P.checked_day(p['as_of'])}" + (f" ({days} days ago)" if days is not None else "") +
          f", {where}")
    print(f"Team file: {team}" if team else "Team file: none")
    print(f"Your own: {mine}" if mine.is_file() else "Your own: none (`sessioncost prices set <model> --input X "
                                                       "--output Y`)")
    note = P.stale_note(p)
    if note:
        print("⚠ " + note)
    print()
    print(f"{'model':28} {'input':>7} {'output':>7} {'cache read':>10} {'cache write 5m/1h':>18}  from")
    for m in sorted(p["models"]):
        r = p["models"][m]
        if not all(isinstance(r.get(c), (int, float)) for c in P.CLASSES):
            continue
        src = p["from"].get(m, "list") + ("" if r.get("checked") else ", estimate")
        print(f"{m:28} {_num(r['input']):>7} {_num(r['output']):>7} {_num(r['cache_read']):>10} "
              f"{_num(r['cache_write_5m']) + '/' + _num(r['cache_write_1h']):>18}  {src}")
    print("\n" + HOW)
    return 0


def main(argv: list[str]) -> int:
    sub, rest = (argv[0], argv[1:]) if argv else ("show", [])
    if sub == "show":
        return show()
    if sub in ("set", "import", "team"):
        return {"set": cmd_set, "import": cmd_import, "team": cmd_team}[sub](rest)
    if sub == "update":
        return cmd_update()
    print(HOW)
    return 2
