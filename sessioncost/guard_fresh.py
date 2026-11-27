"""Fresh releases (guard 5, on by default; owner 2026-11-19 after supply-chain compromise news): an install of a
package VERSION released in the last few days pauses with the reason. The version's age counts, not the package's:
a hijacked old package ships a poisoned new version, and those are usually found and pulled within days.

The one thing the guard sends out: the name of a package an install names, to the registry the install itself is
about to contact (pypi.org, registry.npmjs.org), to read when its versions came out. Nothing else; no project data.
Off with `sessioncost guard fresh-releases off`. Unreachable, slow or odd answers never stop the install (fail open):
each lookup waits at most TIMEOUT seconds, a whole command at most DEADLINE, and a registry that did not answer is
not asked again for a few minutes. Answers are kept for a few hours in the guard's folder (fresh-cache.json).

Which version: the one pinned on the command line (pkg==1.2.3, pkg@1.2.3), else the newest one the registry offers
(what pip, uv and npm pick). Installs from a lockfile or requirements file (npm ci, pip install -r) are not looked up.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

DAYS = 3                       # proposed default: "the last few days"; config.json "fresh_days" changes it
TIMEOUT, DEADLINE, MAX_PKGS = 4.0, 8.0, 5
KEEP_S, DOWN_S = 6 * 3600, 10 * 60
MAX_BYTES = 12 * 1024 * 1024
URLS = {"py": "https://pypi.org/rss/project/{}/releases.xml", "js": "https://registry.npmjs.org/{}"}
_PUBLIC_HOSTS = {
    "py": {"pypi.org", "www.pypi.org", "files.pythonhosted.org"},
    "js": {"registry.npmjs.org", "registry.yarnpkg.com"},
}
_CUSTOM_INDEX_FLAGS = {"-i", "--index-url", "--extra-index-url", "-f", "--find-links", "--registry"}
_CUSTOM_INDEX_ENV = {
    "py": ("PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_EXTRA_INDEX_URL"),
    "js": ("NPM_CONFIG_REGISTRY", "npm_config_registry"),
}

_PY = re.compile(r"^\s*(?:(?:python3?|py)(?:\.exe)?\s+-m\s+pip3?|pip3?(?:\.exe)?|uv\s+pip|pipx)\s+install\s+(.*)$|"
                 r"^\s*(?:uv|poetry)\s+add\s+(.*)$", re.I)
_JS = re.compile(r"^\s*(?:npm|pnpm|bun)(?:\.cmd)?\s+(?:i|install|add)\s+(.*)$|^\s*yarn\s+(?:global\s+)?add\s+(.*)$", re.I)
_TAKES_VALUE = {"-r", "--requirement", "-c", "--constraint", "-e", "--editable", "-i", "--index-url", "--extra-index-url",
                "-f", "--find-links", "-t", "--target", "--prefix", "--root", "--group", "--registry", "--tag", "--python",
                "--reinstall-package"}
_PY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_JS_NAME = re.compile(r"^(?:@[a-z0-9][\w.-]*/)?[a-z0-9][\w.-]*$")
_PRE = re.compile(r"(?:a|b|rc|alpha|beta|dev|pre|c)\d*$|\.dev|\.post", re.I)


def _is_public_registry(eco: str, url: str) -> bool:
    """True only when `url` points at the standard public registry for `eco`."""
    host = (urllib.parse.urlparse((url or "").strip()).hostname or "").lower()
    return bool(host) and host in _PUBLIC_HOSTS.get(eco, set())


def _env_private_registry(eco: str) -> bool:
    """True when an ecosystem index/registry env var is configured to a non-public host."""
    for var in _CUSTOM_INDEX_ENV.get(eco, ()):
        val = (os.environ.get(var) or "").strip()
        if val and not _is_public_registry(eco, val):
            return True
    return False


def _cmd_private_registry(eco: str, words: list[str]) -> bool:
    """True when the install command specifies a non-public index/registry or local find-links."""
    it = iter(words)
    for raw in it:
        w = raw.strip("'\"")
        if w in _CUSTOM_INDEX_FLAGS:
            val = next(it, "").strip("'\"")
            if w in ("-f", "--find-links") or (val and not _is_public_registry(eco, val)):
                return True
            continue
        if "=" in w:
            flag, _, val = w.partition("=")
            if flag in _CUSTOM_INDEX_FLAGS:
                if flag in ("-f", "--find-links") or (val and not _is_public_registry(eco, val)):
                    return True
    return False


_ALLOWED_HOSTS = frozenset({"pypi.org", "registry.npmjs.org"})


class _RegistryRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        import urllib.error
        import urllib.parse
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in _ALLOWED_HOSTS:
            raise urllib.error.URLError(f"Refusing redirect to untrusted host: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _gunzip_bounded(data: bytes, limit: int) -> bytes:
    import io
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
        out = gz.read(limit + 1)
    if len(out) > limit:
        raise ValueError("decompressed answer too large")
    return out


def _fetch(url: str, timeout: float) -> bytes:
    from .guard_store import offline_on
    if offline_on():
        raise RuntimeError("SessionCost offline mode is on: outbound network calls are disabled")
    req = urllib.request.Request(url, headers={"User-Agent": "sessioncost-guard", "Accept-Encoding": "gzip"})
    opener = urllib.request.build_opener(_RegistryRedirectHandler)
    with opener.open(req, timeout=timeout) as r:      # noqa: S310 - fixed https registry addresses
        body = r.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("answer too large")
        return _gunzip_bounded(body, MAX_BYTES) if r.headers.get("Content-Encoding") == "gzip" else body


FETCH = _fetch                 # tests put a fake registry here


def named(cmd: str) -> list[tuple[str, str, str]]:
    """(ecosystem, name, pinned version or '') for each public package an install command names from a registry.
    Skips commands targeting non-public/internal registries and scoped npm packages (@org/pkg) so private package
    names are never sent to public registries."""
    from .risks import _parts, _shell_part, _unquote
    out = []
    for part in _parts(_unquote(_shell_part(cmd or ""))):
        part = re.sub(r"^\s*(?:&\s*)?['\"]?[^\s'\"]*[\\/](?=(?:python3?|pip3?|py)(?:\.exe)?['\"]?\s)", "", part)
        part = re.sub(r"^((?:python3?|pip3?|py)(?:\.exe)?)['\"]", r"\1", part)
        for eco, rx in (("py", _PY), ("js", _JS)):
            m = rx.match(part)
            if not m:
                continue
            words = (m.group(1) or m.group(2) or "").split()
            if _env_private_registry(eco) or _cmd_private_registry(eco, words):
                continue
            skip = False
            for w in words:
                w = w.strip("'\"")
                if skip or w.startswith("-"):
                    skip = not skip and w in _TAKES_VALUE
                    continue
                if re.search(r"[\\/]|://|^\.|\.(?:whl|tar\.gz|zip|tgz)$", w) and not w.startswith("@") or ":" in w:
                    continue                  # a path, a web address, a git+ or file: source
                pkg = _py_pkg(w) if eco == "py" else _js_pkg(w)
                if pkg:
                    out.append((eco, *pkg))
    return list(dict.fromkeys(out))[:MAX_PKGS]


def _py_pkg(w: str) -> tuple[str, str] | None:
    m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*(?:==\s*([\w.!+-]+))?", w)
    if not m or not _PY_NAME.match(m.group(1)):
        return None                           # a range (>=, ~=): pip takes the newest that fits, so the newest is judged
    return re.sub(r"[-_.]+", "-", m.group(1)).lower(), m.group(2) or ""


def _js_pkg(w: str) -> tuple[str, str] | None:
    if w.startswith("@"):
        return None                           # scoped packages (@org/pkg) are often internal: never leak to public npmjs
    name, _, spec = w.partition("@")
    name = name.lower()
    if not _JS_NAME.match(name):
        return None
    return name, spec if re.fullmatch(r"\d+\.\d+\.\d+(?:-[\w.]+)?", spec or "") else ""


def _vkey(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:6])


def _py_times(body: str) -> tuple[str, dict]:
    """(newest stable version, {version: release time}) from PyPI's release feed (its newest releases)."""
    times = {}
    for item in re.findall(r"<item>(.*?)</item>", body, re.S):
        v, d = re.search(r"<title>\s*([^<\s]+)\s*</title>", item), re.search(r"<pubDate>\s*([^<]+?)\s*</pubDate>", item)
        if v and d:
            try:
                times[v.group(1)] = parsedate_to_datetime(d.group(1)).timestamp()
            except (TypeError, ValueError):
                pass
    stable = [v for v in times if not _PRE.search(v)]
    return (max(stable, key=_vkey) if stable else ""), times


def _js_times(body: bytes) -> tuple[str, dict]:
    d = json.loads(body)
    times = {}
    for v, t in (d.get("time") or {}).items():
        try:
            times[v] = datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return str((d.get("dist-tags") or {}).get("latest") or ""), times


def released(eco: str, name: str, pin: str, cache: dict, timeout: float) -> tuple[str, float | None] | None:
    """(version, release time or None when older than the feed shows) for the version an install gets; None when
    the registry could not tell (unreachable, slow, or an answer it could not read)."""
    from .guard_store import offline_on
    if offline_on():
        return None
    key, now = f"{eco}:{name}", time.time()
    hit = cache.get(key)
    if not hit or now - hit.get("at", 0) > (KEEP_S if hit.get("latest") is not None else DOWN_S):
        try:
            body = FETCH(URLS[eco].format(urllib.parse.quote(name, safe="@")), timeout)
            latest, times = _py_times(body.decode("utf-8", "replace")) if eco == "py" else _js_times(body)
            hit = {"at": now, "latest": latest, "times": {v: t for v, t in times.items() if now - t < 60 * 86400}}
        except Exception:                     # offline, a timeout, a dead connection, a page that is not the registry
            hit = {"at": now, "latest": None}
        cache[key] = hit
    if hit.get("latest") is None:
        return None
    v = pin or hit["latest"]
    return (v, hit["times"].get(v)) if v else None


def fresh(cmd: str, days: float = DAYS) -> tuple[list[str], list[str]]:
    """(the versions released less than `days` ago, as "name version (N hours ago)"; the packages it could not
    check). Fails open: what it could not check never pauses the install."""
    from .guard_store import _read, _write, home, offline_on
    if offline_on():
        return [], []
    pkgs = named(cmd)
    if not pkgs:
        return [], []
    path = home() / "fresh-cache.json"
    cache, start, hot, unknown = _read(path, {}), time.time(), [], []
    for eco, name, pin in pkgs:
        left = DEADLINE - (time.time() - start)
        r = released(eco, name, pin, cache, min(TIMEOUT, left)) if left > 0.2 else None
        if r is None:
            unknown.append(name)
            continue
        v, t = r
        if t is not None and 0 <= time.time() - t < days * 86400:
            age = time.time() - t
            hot.append(f"{name} {v} ({round(age / 3600)} hours ago)" if age < 48 * 3600 else
                       f"{name} {v} ({round(age / 86400)} days ago)")
    try:
        _write(path, {k: v for k, v in cache.items() if time.time() - v.get("at", 0) < KEEP_S})
    except OSError:
        pass
    return hot, unknown


def check_on() -> tuple[bool, float]:
    """(on, days) from the guard's config.json: on unless offline mode is on or the user switched it off."""
    from .guard_store import _read, home, offline_on
    c = _read(home() / "config.json", {})
    try:
        days = float(c.get("fresh_days", DAYS))
    except (TypeError, ValueError):
        days = DAYS
    if offline_on(c):
        return False, days
    return c.get("fresh_check", True) is not False, days
