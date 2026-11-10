"""Prices: the dated list (built in, or a newer one fetched by `sessioncost prices update`), then a team's shared file
(`sessioncost prices team <file>`), then your own (~/.sessioncost/prices.json: `prices set` / `prices import`), then
--prices <file>. Each later one wins, model by model."""
from __future__ import annotations

import csv
import io
import json
import os
import re
from datetime import date, datetime
from pathlib import Path

from .model import Usage

HERE = Path(__file__).parent
CLASSES = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")
STALE_DAYS = 60          # proposed: past this, every report says the list is old and how to update it
AUTO = "auto"            # load(): your files under ~/.sessioncost (None: the list alone)


def home() -> Path:
    return Path(os.environ.get("SESSIONCOST_DIR") or Path.home() / ".sessioncost")


def own_file() -> Path:
    return home() / "prices.json"


def update_file() -> Path:
    return home() / "prices-update.json"


def team_pointer() -> Path:
    return home() / "prices-team.json"


def team_file() -> Path | None:
    try:
        f = json.loads(team_pointer().read_text(encoding="utf-8")).get("file")
    except (OSError, ValueError, AttributeError):
        return None
    return Path(f) if f else None


def checked_day(as_of: str) -> str:
    """'2026-10-29' -> '29 Oct 2026': the day the price list was checked, said so it is not read as the report's date."""
    try:
        d = datetime.strptime(as_of, "%Y-%m-%d")
    except ValueError:
        return as_of
    return f"{d.day} {d.strftime('%b %Y')}"


def age_days(as_of: str, today: date | None = None) -> int | None:
    try:
        return ((today or date.today()) - datetime.strptime(str(as_of), "%Y-%m-%d").date()).days
    except ValueError:
        return None


def stale_note(prices: dict) -> str:
    """What a report says when its prices may be wrong: an old list, or a team file it could not read. "" if neither."""
    notes = []
    days = age_days(prices.get("as_of", ""))
    if days is not None and days > STALE_DAYS:
        notes.append(f"The price list is {days} days old (checked {checked_day(prices['as_of'])}): run "
                     "`sessioncost prices update`, or set your own with `sessioncost prices set`.")
    if prices.get("team_problem"):
        notes.append(prices["team_problem"])
    return " ".join(notes)


def valid_list(p) -> bool:
    """A whole price list: dated, every model with all five rates as numbers of 0 or more."""
    if not isinstance(p, dict) or age_days(p.get("as_of", "")) is None or not isinstance(p.get("models"), dict):
        return False
    return all(isinstance(r, dict) and all(isinstance(r.get(c), (int, float)) and not isinstance(r.get(c), bool)
                                           and r[c] >= 0 for c in CLASSES) for r in p["models"].values())


def built_in() -> dict:
    return json.loads((HERE / "prices.json").read_text(encoding="utf-8"))


def the_list() -> dict:
    """The built-in list, or the one `prices update` fetched when that is newer."""
    built = built_in()
    try:
        got = json.loads(update_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return built
    return got if valid_list(got) and str(got["as_of"]) > str(built.get("as_of", "")) else built


def number(v, what: str) -> float | None:
    """A price as typed: '4', '$4', '0.30'. None if left empty; ValueError if not a price."""
    if v is None or str(v).strip() == "":
        return None
    try:
        n = float(str(v).strip().lstrip("$"))
    except ValueError:
        raise ValueError(f"{what} must be a number (dollars per million tokens), not {v!r}") from None
    if n < 0 or n != n:
        raise ValueError(f"{what} cannot be {v!r}")
    return n


def fill(model: str, given: dict, table: dict) -> tuple[dict, bool]:
    """Your rates for a model: input and output must be given; a cache price left out follows the list's ratio to input
    for a model the list knows, else is priced as input. (rates, True if a cache price was priced as input)."""
    inp, out = number(given.get("input"), f"{model}: input"), number(given.get("output"), f"{model}: output")
    if inp is None or out is None:
        raise ValueError(f"{model}: input and output prices are both needed")
    w = number(given.get("cache_write"), f"{model}: cache_write")
    want = {k: number(given.get(k), f"{model}: {k}") for k in ("cache_read", "cache_write_5m", "cache_write_1h")}
    for k in ("cache_write_5m", "cache_write_1h"):
        want[k] = w if want[k] is None else want[k]
    known = table.get(model) or table.get(normalise(model))
    r, as_input = {"input": inp, "output": out}, False
    for k, v in want.items():
        if v is not None:
            r[k] = v
        elif known and known.get("input"):
            r[k] = round(known[k] / known["input"] * inp, 6)
        else:
            r[k], as_input = inp, True
    r["checked"] = True
    return r, as_input


def read_table(path, table: dict) -> dict:
    """Prices from a .csv (model, input, output; cache_read, cache_write optional) or a .json (our format, or
    {model: {...}}), each model filled as fill() does. ValueError names the first bad row; nothing half-read."""
    text = Path(path).read_text(encoding="utf-8-sig")
    if Path(path).suffix.lower() == ".json":
        data = json.loads(text)
        rows = data.get("models", data) if isinstance(data, dict) else None
        if not isinstance(rows, dict):
            raise ValueError('a .json price file needs {"models": {"<model>": {"input": .., "output": ..}}}')
        rows = [{"model": m, **(r if isinstance(r, dict) else {})} for m, r in rows.items()]
    else:
        reader = csv.DictReader(io.StringIO(text))
        cols = {(c or "").strip().lower() for c in reader.fieldnames or []}
        if not {"model", "input", "output"} <= cols:
            raise ValueError("a .csv price file needs the columns model, input, output (cache_read, cache_write "
                             "optional)")
        rows = [{(k or "").strip().lower(): v for k, v in row.items()} for row in reader]
    out = {}
    for row in rows:
        m = normalise(str(row.get("model") or ""))
        if m:
            out[m], _ = fill(m, row, table)
    if not out:
        raise ValueError("no prices in it")
    return out


def _merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in extra.items():
        if k == "models" and isinstance(v, dict):
            models = {m: dict(r) for m, r in base.get("models", {}).items()}
            for m, r in v.items():
                models[m] = {**models.get(m, {}), **r}
            out["models"] = models
        else:
            out[k] = v
    return out


def load(extra_file: str | None = None, user_file=AUTO) -> dict:
    """user_file: AUTO = the list (or its update), your team's file and your own under ~/.sessioncost; None = the built-in
    list alone; a path = that file as your own."""
    personal = user_file == AUTO
    prices = the_list() if personal else built_in()
    src = {m: "list" for m in prices["models"]}       # where each model's price came from, for `sessioncost prices`
    own = []          # your own price files merged in, named on the money line so it is not read as the list prices
    team = team_file() if personal else None
    if team:
        try:
            got = read_table(team, prices["models"])
            prices = _merge(prices, {"models": got})
            src.update({m: f"team file {team.name}" for m in got})
            own.append(team.name)
        except (OSError, ValueError) as e:
            prices["team_problem"] = f"Your team's price file could not be read ({e}), so list prices were used."
    mine = own_file() if personal else user_file
    for f, label in ((mine, "your own"), (Path(extra_file).expanduser() if extra_file else None, "--prices")):
        if f and Path(f).is_file():
            data = json.loads(Path(f).read_text(encoding="utf-8"))
            prices = _merge(prices, data)
            for m, r in (data.get("models") or {}).items():
                set_on = r.get("set_on") if isinstance(r, dict) else None
                src[m] = f"{label}, set {set_on}" if set_on else f"{label} ({Path(f).name})"
            own.append(Path(f).name)
    if own:
        prices["own"] = own
    prices["from"] = src
    return prices


def normalise(model: str) -> str:
    """'claude-opus-5[1m]' -> 'claude-opus-5'; 'claude-haiku-4-5-20251001' -> 'claude-haiku-4-5'."""
    m = re.sub(r"\[.*?\]", "", (model or "").strip().lower())
    return re.sub(r"-\d{8}$", "", m)


def rates(model: str, prices: dict) -> tuple[dict, bool, str]:
    """(rates per million, checked?, the price-table key used)."""
    table = prices["models"]
    for key in (model, normalise(model)):
        if key in table:
            r = table[key]
            return r, bool(r.get("checked")), key
    m = normalise(model)
    key = "default-openai" if re.match(r"(gpt|codex|o\d)", m) else "default-gemini" if m.startswith("gemini") else ""
    key = key if key in table else "default"
    return table[key], False, key


def on_day(r: dict, day: str) -> dict:
    """The rates on a day ('2026-12-01'): a model whose vendor announced a new price has "changes", [{"on": <day>,
    <rates>}, ...]; each one from its day on. An "over" entry inside a change replaces the long-prompt rates."""
    for ch in sorted(r.get("changes") or [], key=lambda x: str(x.get("on"))):
        if str(ch.get("on")) <= day:
            r = {**r, **{k: v for k, v in ch.items() if k != "on"}}
    return r


def for_prompt(r: dict, prompt_tokens: int) -> dict:
    """The rates for a call whose prompt (everything it sent: new, written and re-read) is this long: a model priced by
    prompt length has an "over" entry, {"above": N, <rates>}, used when the prompt is over N tokens."""
    over = r.get("over")
    if not over or prompt_tokens <= over["above"]:
        return r
    return {**r, **{k: v for k, v in over.items() if k != "above"}}


def cost(u: Usage, r: dict) -> dict:
    """USD per token class for one call. Every token is counted in exactly one class."""
    return {
        "input": u.fresh_input * r["input"] / 1e6,
        "cache_write_5m": u.cache_write_5m * r["cache_write_5m"] / 1e6,
        "cache_write_1h": u.cache_write_1h * r["cache_write_1h"] / 1e6,
        "cache_read": u.cache_read * r["cache_read"] / 1e6,
        "output": u.output * r["output"] / 1e6,
    }


def new_input_rate(u: Usage, r: dict) -> float:
    """Average USD per million for the new (not re-read) input of this call."""
    n = u.new_input
    if not n:
        return r["cache_write_1h"] if u.cache_read else r["input"]
    return (u.fresh_input * r["input"] + u.cache_write_5m * r["cache_write_5m"]
            + u.cache_write_1h * r["cache_write_1h"]) / n
