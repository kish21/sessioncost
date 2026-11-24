"""The watch list's network-exposure patterns (see risks.py for the marks): what opens this computer to others
(remote_access, blocked) and servers on every network address (open_server, asked)."""
from __future__ import annotations

import re

# opens this computer to others: a public tunnel or a reverse shell (blocked). ssh -R only among ssh's own options,
# before the host (ssh host ls -r is a remote command; ssh-keygen -R removes a known host). autossh and PuTTY's
# plink take the same -R. cloudflared: only what runs a tunnel; list, info, delete, login ... change nothing here
_SSH = r"(?:^|[\s;&|(])(?:[\w.:~-]*[\\/])*(?:autossh|ssh|plink)(?:\.exe)?"     # also /usr/bin/ssh, C:\...\ssh.exe
_NC = r"\b(?:nc|ncat|netcat)(?:\.exe)?\b"
_SH = (r"(?:(?:env|sudo|busybox|exec)\s+)?(?:[\w./\\-]*[\\/])?(?:(?:ba|z|da|k)?sh|pwsh|powershell|cmd)"
       r"(?:\.exe)?\b")
# OpenSSH's options after the host: the letters that take no value, or one that takes a value with its value
# (ssh host -t chmod -R 755 x: -t takes none, so chmod is the remote command and -R is chmod's)
_SSH_OPTS = r"(?:\s+-(?-i:[46AaCfGgKkMNnqsTtVvXxYy]+)\b|\s+-(?-i:[BbcDEeFIiJLlmOoPpQSWw])\s*[^\s-]\S*)*"
_REMOTE = re.compile(
    r"\bngrok(?:\.exe)?\b[^\n;|&]*?\s(?:http|tcp|tls|start|tunnel)\b|"
    r"\bcloudflared(?:\.exe)?\b[^\n;|&]*?\s(?:tunnel\b(?!\s+(?:list|info|delete|cleanup|route|login|create|token|"
    r"ingress|help|version|--help|-h)\b)|--url\b|service\s+install\b)|" + _SSH +
    r"(?:\s+-(?-i:[A-QS-Za-z]*)\b(?:\s+[^\s-]\S*)?)*\s+-(?-i:[A-Za-z]*R)|" + _SSH +
    # after the host too (OpenSSH reads options there): -R with a port:host:port spec anywhere, or with a bare
    # [addr:]port while the words after the host are still options (ssh host chmod -R 755 x is a remote command)
    r"\s[^\n;|&]*?\s-(?-i:R)\s*['\"]?(?:[^\s:'\"]+:)?\d+:[^\s:'\"]+:\d+|" + _SSH +
    r"(?:\s+-(?-i:[A-Za-z]+)\b(?:\s+[^\s-]\S*)?)*\s+[^\s-]\S*" + _SSH_OPTS +
    r"\s+-(?-i:R)\s*['\"]?(?:\[[^\]\s]*\]:|[^\s:'\"/]+:)?\d+\b|"
    r"\bserveo\.net\b|\blocalhost\.run\b|\blocaltunnel\b|"
    r"\blt\s+(?:--port|-p)\b|\bbore\s+local\b|\btailscale\s+funnel\b|" + _NC +
    r"[^\n;|&]*\s(?:-[A-Za-z]*[ec]|--(?:exec|sh-exec|lua-exec))\b|\bsocat\b[^\n;|&]*\b(?:exec|system):|"
    r"/dev/(?:tcp|udp)/", re.I)
# a shell's input or output piped to or from nc: the shell is handed to whoever is at the other end. Read in the
# command with its | kept (risks.watch_marks): the step text has the pipe's parts on lines of their own
_ONE_PART = r"(?:[^\n;&|]|&\d|>&)*"           # up to the next ; && || (2>&1 stays inside)
_NC_SHELL = re.compile(_NC + _ONE_PART + r"\|\s*" + _SH + "|" + r"(?:^|[\s;&|(])" + _SH + _ONE_PART + r"\|\s*" + _NC,
                       re.I)
# ssh -o "RemoteForward 80 localhost:3000": read in the whole command, as the quoted value is blanked as prose elsewhere
_SSH_FWD_OPT = re.compile(r"(?:^|[;&|(\n\"'`]\s*)(?:[\w.:~-]*[\\/])*(?:autossh|ssh|plink)(?:\.exe)?\s[^\n;|&]*?"
                          r"-o\s*['\"]?RemoteForward\b", re.I)
# a server on every network address (0.0.0.0): others on the network can reach it. Asked, not blocked (owner
# 2026-11-24: dev servers do this on purpose, e.g. to test on a phone). gunicorn -b 0.0.0.0:8000, flask -h, next -H,
# http-server -a; vite --host with no value; HOST=0.0.0.0 npm start; jupyter --ip='*'; python -m http.server
# without --bind (it listens on every address by default)
_ALL_ADDR = r"['\"]?(?:0\.0\.0\.0|::|\[::\])(?::\d+)?(?![\w.:])"
_OPEN_SERVER = re.compile(r"(?:^|\s)(?:--bind|--host|--listen|--address|--ip|-[bhHa])(?:\s+|=)" + _ALL_ADDR + "|"
                          r"(?:^|[\s;&|(])(?:HOST|BIND|HOST_IP|BIND_ADDR)=" + _ALL_ADDR + "|"
                          r"\s--host(?=\s*(?:$|[;&|)]|-))|\s--ip(?:\s+|=)['\"]?\*|"
                          r"\b(?:python[\d.]*|py)(?:\.exe)?(?:\s+-[\w.]+)*?\s+-m\s*http\.server\b"   # py -3 -m, -mhttp.server
                          r"(?![^\n;|&]*\s(?:--bind|-b)\b)|"
                          r"\bphp\s+-S\s+(?:0\.0\.0\.0|\[::\])", re.I | re.M)
