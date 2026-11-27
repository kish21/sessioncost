"""Prices: the dated list (built in, or a newer one fetched by `sessioncost prices update`), then a team's shared file
(`sessioncost prices team <file>`), then your own (~/.sessioncost/prices.json: `prices set` / `prices import`), then
--prices <file>. Each later one wins, model by model."""
from __future__ import annotations

import csv
import io
import json
import math
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
        raw = json.loads(team_pointer().read_text(encoding="utf-8"))
        f = raw.get("file") if isinstance(raw, dict) else None
    except (OSError, ValueError, AttributeError):
        return None
    return Path(f) if isinstance(f, str) and f else None


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
    for k in ("team_problem", "own_problem"):
        if prices.get(k):
            notes.append(prices[k])
    return " ".join(notes)


def valid_list(p) -> bool:
    """A whole price list: dated, every model with all five rates as numbers of 0 or more."""
    if not isinstance(p, dict) or age_days(p.get("as_of", "")) is None or not isinstance(p.get("models"), dict):
        return False
    return all(isinstance(r, dict) and all(isinstance(r.get(c), (int, float)) and not isinstance(r.get(c), bool)
                                           and math.isfinite(r[c]) and r[c] >= 0 for c in CLASSES) for r in p["models"].values())


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
    if not math.isfinite(n) or n < 0:
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
        elif k == "providers" and isinstance(v, dict):
            provs = {p: {m: dict(r) for m, r in tbl.items()} for p, tbl in (base.get("providers") or {}).items()
                     if isinstance(tbl, dict)}
            for p, tbl in v.items():
                if isinstance(tbl, dict):
                    pk = norm_provider(p)
                    provs.setdefault(pk, {})
                    for m, r in tbl.items():
                        if isinstance(r, dict):
                            provs[pk][normalise(m)] = {**provs[pk].get(normalise(m), {}), **r}
            out["providers"] = provs
        elif k == "pricing" and isinstance(v, dict):
            out["pricing"] = {**(out.get("pricing") or {}), **{pk: pv for pk, pv in v.items() if pv is not None}}
        elif k == "modifiers" and isinstance(v, dict):
            mods = dict(base.get("modifiers") or {})
            for mk, mv in v.items():
                mods[mk] = {**(mods.get(mk) or {}), **mv} if isinstance(mv, dict) and isinstance(mods.get(mk), dict) else mv
            out["modifiers"] = mods
        elif k in ("provider", "tier", "region", "inference_geo") and v is not None:
            pr = dict(out.get("pricing") or {})
            pr["region" if k == "inference_geo" else k] = v
            out["pricing"] = pr
        else:
            out[k] = v
    return out


def load(extra_file: str | None = None, user_file=AUTO, *,
         provider: str = "", tier: str = "", region: str = "") -> dict:
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
    problems = []
    for f, label in ((mine, "your own"), (Path(extra_file).expanduser() if extra_file else None, "--prices")):
        if f and not Path(f).is_file():
            if label == "--prices":
                raise ValueError(f"the price file {f} does not exist")
            continue
        if f and Path(f).is_file():
            try:
                data = json.loads(Path(f).read_text(encoding="utf-8-sig"))
                if not isinstance(data, dict) or not isinstance(data.get("models", {}), dict):
                    raise ValueError('it needs {"models": {"<model>": {"input": .., "output": ..}}}')
            except (OSError, ValueError) as e:
                if label == "--prices":       # a file named on the command line: say so and stop
                    raise ValueError(f"the price file {f} could not be read ({e})") from None
                problems.append(f"Your own price file {f} could not be read ({e}), so list prices were used: fix "
                                "it, or delete it and set your prices again with `sessioncost prices set`.")
                continue
            bad = []
            for m, r in (data.get("models") or {}).items():
                if not isinstance(r, dict):
                    bad.append(m)
                    continue
                for c in CLASSES:
                    if c in r:
                        v = r[c]
                        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
                            bad.append(m)
                            break
            if bad:
                data = {**data, "models": {m: r for m, r in data["models"].items() if m not in bad}}
                problems.append(f"In the price file {f}, {', '.join(bad)} {'has' if len(bad) == 1 else 'have'} invalid "
                                "or non-numeric prices, so list prices were used for "
                                f"{'it' if len(bad) == 1 else 'them'}.")
            prices = _merge(prices, data)
            for m, r in (data.get("models") or {}).items():
                set_on = r.get("set_on") if isinstance(r, dict) else None
                src[m] = f"{label}, set {set_on}" if set_on else f"{label} ({Path(f).name})"
            if data.get("models") or data.get("providers"):
                own.append(Path(f).name)
    if own:
        prices["own"] = own
    if problems:
        prices["own_problem"] = " ".join(problems)
    cli_pricing = {k: v for k, v in (("provider", provider), ("tier", tier), ("region", region)) if v}
    if cli_pricing:
        prices = _merge(prices, {"pricing": cli_pricing})
    prices["from"] = src
    return prices


_PROVIDER_ALIASES = {
    "": "direct", "direct": "direct", "anthropic": "direct", "openai": "direct", "google": "direct",
    "xai": "direct", "cursor": "direct",
    "bedrock": "bedrock", "aws": "bedrock", "aws-bedrock": "bedrock", "aws_bedrock": "bedrock",
    "vertex": "vertex", "vertex-ai": "vertex", "vertex_ai": "vertex", "gcp": "vertex", "google-vertex": "vertex",
    "azure": "azure", "azure-openai": "azure", "azure_openai": "azure", "azure_ai": "azure",
    "azure-ai": "azure", "foundry": "azure",
}
_PROVIDER_NAMES = {
    "bedrock": "AWS Bedrock",
    "vertex": "Google Vertex AI",
    "azure": "Azure OpenAI",
}
_TIER_ALIASES = {
    "": "standard", "default": "standard", "standard": "standard", "on-demand": "standard", "on_demand": "standard",
    "batch": "batch", "flex": "flex", "priority": "priority", "fast": "fast",
}
_DEFAULT_MODIFIERS = {
    "tier": {
        "standard": 1.0,
        "batch": 0.5,
        "flex": 0.5,
        "priority": {"openai": 2.0, "anthropic": 1.0, "google": 1.0, "default": 2.0},
        "fast": {"cursor": 2.0, "default": 2.0},
    },
    "region": {
        "global": 1.0,
        "direct": {"anthropic": {"us": 1.1}},
        "bedrock": {"regional": 1.1},
        "vertex": {"regional": 1.1},
        "azure": {"regional": 1.1},
    },
}


def norm_provider(provider: str) -> str:
    p = (provider or "").strip().lower()
    return _PROVIDER_ALIASES.get(p, p or "direct")


def norm_tier(tier: str) -> str:
    t = (tier or "").strip().lower()
    return _TIER_ALIASES.get(t, t or "standard")


def norm_region(region: str) -> str:
    r = (region or "").strip().lower()
    if r in ("", "default", "global", "not_available", "none"):
        return "global"
    if r in ("us-only", "us_only"):
        return "us"
    return r


def _vendor(model: str) -> str:
    m = normalise(model)
    if m.startswith("claude"):
        return "anthropic"
    if re.match(r"(gpt|codex|o\d)", m):
        return "openai"
    if m.startswith("gemini"):
        return "google"
    if m.startswith(("composer", "grok-4.7")):
        return "cursor"
    return "default"


def parse_model(model: str) -> tuple[str, str, str]:
    """Extract (canonical_model, detected_provider, detected_region) from a raw model id.
    Handles AWS Bedrock ('us.anthropic.claude-opus-4-6-20251001-v1:0', 'global.anthropic...', 'bedrock/...'),
    Google Vertex AI ('publishers/anthropic/models/claude-...@20251001', 'vertex/us-east5/...', '...@20251001'),
    and Azure OpenAI ('azure/gpt-5.5', 'azure/eastus/gpt-5.5')."""
    raw = re.sub(r"\[.*?\]", "", (model or "").strip().lower())
    provider, region = "", ""
    if raw.startswith("bedrock/"):
        provider, raw = "bedrock", raw[len("bedrock/"):]
    elif raw.startswith(("vertex_ai/", "vertex/")):
        provider, raw = "vertex", raw.split("/", 1)[1]
        if "/" in raw and not raw.startswith("publishers/"):
            region, raw = raw.split("/", 1)
    elif raw.startswith(("azure_ai/", "azure/")):
        provider, raw = "azure", raw.split("/", 1)[1]
        if "/" in raw:
            region, raw = raw.split("/", 1)
    if raw.startswith("publishers/"):
        m = re.match(r"publishers/[^/]+/models/(.+)$", raw)
        if m:
            provider, raw = provider or "vertex", m.group(1)
    # AWS Bedrock cross-region / regional model IDs: [us|eu|ap|apac|global.]anthropic.claude-...-v1:0
    bm = re.match(r"^(?:(global|us|eu|ap|apac)\.)?(?:anthropic|amazon|meta|mistral|cohere)\.(.+)$", raw)
    if bm:
        provider = provider or "bedrock"
        prefix, raw = bm.group(1), bm.group(2)
        if not region:
            region = "ap" if prefix == "apac" else (prefix or "regional")
        raw = re.sub(r"-v\d+(?::\d+)?$", "", raw)
    # Google Vertex AI uses @YYYYMMDD instead of -YYYYMMDD
    if re.search(r"@\d{8}$", raw):
        provider = provider or "vertex"
        raw = re.sub(r"@\d{8}$", "", raw)
    raw = re.sub(r"-v\d+:\d+$", "", raw)
    raw = re.sub(r"-\d{8}$", "", raw)
    return raw, provider, region


def normalise(model: str) -> str:
    """'claude-opus-5[1m]' -> 'claude-opus-5'; 'claude-haiku-4-5-20251001' -> 'claude-haiku-4-5'."""
    m = re.sub(r"\[.*?\]", "", (model or "").strip().lower())
    m = re.sub(r"@\d{8}$", "", m)
    m = re.sub(r"-v\d+:\d+$", "", m)
    return re.sub(r"-\d{8}$", "", m)


def resolve_context(model: str, prices: dict, *, provider: str = "", tier: str = "", region: str = "") -> dict:
    """Resolve effective (provider, tier, region) with precedence: explicit call/session -> model id -> price file -> default."""
    canonical, det_provider, det_region = parse_model(model)
    cfg = prices.get("pricing") if isinstance(prices.get("pricing"), dict) else {}
    eff_provider = norm_provider(provider or det_provider or str(cfg.get("provider") or ""))
    eff_tier = norm_tier(tier or str(cfg.get("tier") or ""))
    eff_region = norm_region(region or det_region or str(cfg.get("region") or cfg.get("inference_geo") or ""))
    return {"canonical": canonical, "provider": eff_provider, "tier": eff_tier, "region": eff_region}


def pricing_label(info: dict) -> str:
    """Plain-English label of the non-default pricing applied, or '' for standard direct global pricing."""
    provider = info.get("provider") or "direct"
    tier = info.get("tier") or "standard"
    region = info.get("region") or "global"
    mult = float(info.get("multiplier") or 1.0)
    explicit = bool(info.get("explicit_provider_rate"))
    if provider == "direct" and tier == "standard" and region == "global" and not explicit:
        return ""
    mult_s = f"{mult:g}x" if abs(mult - 1.0) > 1e-6 else ""
    if provider in _PROVIDER_NAMES:
        bits = [region]
        if tier != "standard":
            bits.append(tier)
        if mult_s:
            bits.append(mult_s)
        elif explicit:
            bits.append("custom rates")
        return f"{_PROVIDER_NAMES[provider]} ({', '.join(bits)})"
    parts = []
    if tier != "standard":
        names = {"batch": "Batch API", "flex": "Flex tier", "priority": "Priority tier", "fast": "Fast mode"}
        parts.append(names.get(tier, f"{tier} tier"))
    if region != "global":
        parts.append("US-only inference" if region == "us" else f"{region} region")
    text = ", ".join(parts) if parts else provider
    return f"{text} ({mult_s})" if mult_s else text


def rates(model: str, prices: dict, *, provider: str = "") -> tuple[dict, bool, str]:
    """(rates per million, checked?, the price-table key used)."""
    table = prices["models"]
    canonical, det_provider, _ = parse_model(model)
    eff_provider = norm_provider(provider or det_provider)
    if eff_provider != "direct":
        prov_tbl = (prices.get("providers") or {}).get(eff_provider) or {}
        for k in (model, canonical, normalise(model)):
            if k in prov_tbl:
                r = prov_tbl[k]
                return r, bool(r.get("checked", True)), f"{eff_provider}/{k}"
        prefixed = f"{eff_provider}/{canonical}"
        if prefixed in table:
            r = table[prefixed]
            return r, bool(r.get("checked", True)), prefixed
    for key in (model, normalise(model), canonical):
        if key in table:
            r = table[key]
            return r, bool(r.get("checked")), key
    m = canonical or normalise(model)
    key = "default-openai" if re.match(r"(gpt|codex|o\d)", m) else "default-gemini" if m.startswith("gemini") else ""
    key = key if key in table else "default"
    return table[key], False, key


def apply_modifiers(r: dict, model: str, prices: dict, *, provider: str = "", tier: str = "", region: str = "",
                    prompt_tokens: int = 0, explicit_provider_rate: bool = False) -> tuple[dict, dict]:
    """Apply prompt-length rules and tier/provider/region multipliers to a model's rates.
    Returns (adjusted_rates, pricing_info)."""
    ctx = resolve_context(model, prices, provider=provider, tier=tier, region=region)
    canonical = ctx["canonical"]
    eff_provider, eff_tier, eff_region = ctx["provider"], ctx["tier"], ctx["region"]
    vendor = _vendor(canonical)
    mods = prices.get("modifiers") if isinstance(prices.get("modifiers"), dict) else {}
    tier_mods = mods.get("tier") if isinstance(mods.get("tier"), dict) else _DEFAULT_MODIFIERS["tier"]
    reg_mods = mods.get("region") if isinstance(mods.get("region"), dict) else _DEFAULT_MODIFIERS["region"]

    # Cursor Fast mode on a base model name (e.g. grok-4.7 or composer-2.5 with tier="fast"):
    # <=256k is 2x standard, >256k is 3x standard (whereas a *-fast model already has 2x / 3x in prices.json).
    already_fast = canonical.endswith("-fast")
    if eff_tier == "fast" and vendor == "cursor" and not already_fast:
        over = r.get("over")
        long_ctx = bool(over and prompt_tokens > over["above"])
        tier_mult = 3.0 if long_ctx else 2.0
        base = r
    else:
        base = for_prompt(r, prompt_tokens)
        if eff_tier == "fast" and already_fast:
            tier_mult = 1.0
        else:
            raw_t = tier_mods.get(eff_tier, _DEFAULT_MODIFIERS["tier"].get(eff_tier, 1.0))
            if isinstance(raw_t, dict):
                tier_mult = float(raw_t.get(vendor, raw_t.get("default", 1.0)))
            else:
                tier_mult = float(raw_t)

    region_mult = 1.0
    if not explicit_provider_rate and eff_region != "global":
        if eff_provider == "direct":
            d_tbl = reg_mods.get("direct") if isinstance(reg_mods.get("direct"), dict) else _DEFAULT_MODIFIERS["region"]["direct"]
            v_tbl = d_tbl.get(vendor) if isinstance(d_tbl.get(vendor), dict) else {}
            if eff_region in v_tbl:
                region_mult = float(v_tbl[eff_region])
        elif eff_provider in ("bedrock", "vertex", "azure"):
            p_tbl = reg_mods.get(eff_provider) if isinstance(reg_mods.get(eff_provider), dict) else _DEFAULT_MODIFIERS["region"].get(eff_provider, {})
            region_mult = float(p_tbl.get(eff_region, p_tbl.get("regional", 1.1)))

    mult = round(tier_mult * region_mult, 6)
    out = dict(base)
    if abs(mult - 1.0) > 1e-6:
        for c in CLASSES:
            if c in out and isinstance(out[c], (int, float)):
                out[c] = round(out[c] * mult, 6)
    info = {
        "provider": eff_provider,
        "tier": eff_tier,
        "region": eff_region,
        "multiplier": mult,
        "explicit_provider_rate": explicit_provider_rate,
    }
    info["label"] = pricing_label(info)
    return out, info


def call_rates(model: str, prices: dict, *, day: str = "", prompt_tokens: int = 0,
               provider: str = "", tier: str = "", region: str = "") -> tuple[dict, bool, str, dict]:
    """Full per-call rate resolution: model/provider lookup -> dated change -> prompt length -> modifiers."""
    ctx = resolve_context(model, prices, provider=provider, tier=tier, region=region)
    r, checked, key = rates(model, prices, provider=ctx["provider"])
    explicit_prov = ctx["provider"] != "direct" and key.startswith(f"{ctx['provider']}/")
    if day:
        r = on_day(r, day)
    adj, info = apply_modifiers(
        r, model, prices,
        provider=ctx["provider"], tier=ctx["tier"], region=ctx["region"],
        prompt_tokens=prompt_tokens, explicit_provider_rate=explicit_prov,
    )
    return adj, checked, key, info


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
