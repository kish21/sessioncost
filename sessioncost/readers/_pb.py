"""A tiny protobuf reader for logs stored without their schema (Antigravity). Read-only and forgiving: a blob that
does not parse as a message gives no fields instead of an error."""
from __future__ import annotations


def _varint(b: bytes, i: int) -> tuple[int, int]:
    r = s = 0
    while True:
        x = b[i]
        i += 1
        r |= (x & 0x7F) << s
        s += 7
        if x < 0x80:
            return r, i


def fields(b: bytes | None) -> dict[int, list]:
    """Field number -> values in order (ints for numbers, bytes for strings and nested messages)."""
    out: dict[int, list] = {}
    if not b:
        return out
    i, n = 0, len(b)
    try:
        while i < n:
            key, i = _varint(b, i)
            f, wt = key >> 3, key & 7
            if f == 0:
                return {}
            if wt == 0:
                v, i = _varint(b, i)
            elif wt == 1:
                v, i = b[i:i + 8], i + 8
            elif wt == 5:
                v, i = b[i:i + 4], i + 4
            elif wt == 2:
                ln, i = _varint(b, i)
                v, i = b[i:i + ln], i + ln
            else:
                return {}
            if i > n:
                return {}
            out.setdefault(f, []).append(v)
    except IndexError:
        return {}
    return out


def get(b: bytes | None, *path: int):
    """The first value at a field path, e.g. get(meta, 9, 2); None when absent."""
    vals = all_(b, *path)
    return vals[0] if vals else None


def all_(b: bytes | None, *path: int) -> list:
    """Every value at a field path (repeated fields at any level)."""
    level = [b] if b else []
    for k, f in enumerate(path):
        nxt = []
        for x in level:
            if isinstance(x, (bytes, bytearray)):
                nxt += fields(bytes(x)).get(f, [])
        level = nxt
    return level


def num(b: bytes | None, *path: int) -> int | None:
    v = get(b, *path)
    return v if isinstance(v, int) else None


def text(b: bytes | None, *path: int) -> str:
    v = get(b, *path)
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).decode("utf-8", "replace")
    return ""
