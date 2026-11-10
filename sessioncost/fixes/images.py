"""Check 5 (1.1): screenshots or images that stay in the conversation and are re-sent on every later call."""
from __future__ import annotations

from ..model import Fix, Session
from .common import short_target, tok, turns_where

MIN_CARRIED_IMAGE_TOKENS = 5_000
MIN_LATER_CALLS = 2
_IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")


def images(s: Session, pr) -> Fix | None:
    calls = s.calls
    if len(calls) <= MIN_LATER_CALLS:
        return None
    idx_by_n = {c.n: i for i, c in enumerate(calls)}
    items: list[str] = []
    per_turn: dict[int, int] = {}
    hit_calls: list[tuple[str, int]] = []
    total_tok = 0
    total_usd = 0.0
    img_count = 0

    # 1. Images attached directly to a user turn
    for t in s.turns:
        img_evs = [e for e in t.events if e.kind == "image"]
        if not img_evs or not t.calls:
            continue
        first_c = t.calls[0]
        i = idx_by_n.get(first_c.n, 0)
        later = calls[i + 1 :]
        if len(later) < MIN_LATER_CALLS:
            continue
        itok = sum(max(1_200, tok(e.chars)) for e in img_evs)
        carried = itok * len(later)
        usd = sum(itok * pr.rates(lc)["cache_read"] / 1e6 for lc in later)
        total_tok += carried
        total_usd += usd
        img_count += len(img_evs)
        for lc in later:
            per_turn[lc.turn] = per_turn.get(lc.turn, 0) + itok
        if ("main", first_c.n) not in hit_calls:
            hit_calls.append(("main", first_c.n))
        items.append(f"{len(img_evs)} image{'s' if len(img_evs) != 1 else ''} attached in turn {t.n} "
                     f"(~{itok:,} tokens) — carried across {len(later)} later calls (~{carried:,} tokens)")

    # 2. Images read from disk or returned by a tool (e.g. Read on .png or browser screenshot)
    for i, c in enumerate(calls):
        later = calls[i + 1 :]
        if len(later) < MIN_LATER_CALLS:
            continue
        for a in c.actions:
            is_img_file = a.kind == "read" and a.target.lower().endswith(_IMG_EXTS)
            if not (a.images > 0 or is_img_file):
                continue
            cnt = max(1, a.images)
            itok = max(1_200 * cnt, tok(a.image_chars or a.result_chars))
            carried = itok * len(later)
            usd = sum(itok * pr.rates(lc)["cache_read"] / 1e6 for lc in later)
            total_tok += carried
            total_usd += usd
            img_count += cnt
            for lc in later:
                per_turn[lc.turn] = per_turn.get(lc.turn, 0) + itok
            if ("main", c.n) not in hit_calls:
                hit_calls.append(("main", c.n))
            name = short_target(a.target or a.label, s.cwd)
            items.append(f"`{name}` (call #{c.n}, ~{itok:,} tokens) — carried across {len(later)} later calls "
                         f"(~{carried:,} tokens)")

    if total_tok < MIN_CARRIED_IMAGE_TOKENS:
        return None
    turns = sorted(per_turn)
    return Fix(
        id="images",
        title=f"{img_count} image{'s' if img_count != 1 else ''} stayed in the chat across later calls (~{total_tok:,} tokens)",
        why=(f"Each screenshot or image costs ~1,200–2,000 tokens and is re-sent on every call after it enters the chat. "
             f"Carrying {img_count} image{'s' if img_count != 1 else ''} across later calls added ~{total_tok:,} cached tokens."),
        saving_usd=total_usd, saving_tokens=total_tok,
        where=turns_where(turns), turns=turns, items=items[:6], per_turn=per_turn, calls=hit_calls,
        how="When sharing a screenshot or image, crop it to the part that matters or describe it in words instead.",
        paste=True,
    )
