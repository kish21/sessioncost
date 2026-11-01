#!/usr/bin/env node
// `npx sessioncost …`: runs SessionCost, a Python tool, for people who start things with npm.
//
// The first run makes a private Python environment in ~/.sessioncost/npm/venv and installs this exact version of
// SessionCost there (from PyPI; from the folder around this one when it sits in a SessionCost source tree, as in a
// test build). Later runs only start it. With uv on the PATH, uv makes the environment and fetches Python itself
// when none is installed; without it, a Python 3.10 or newer on the PATH is used. The environment stays: `setup` and
// the guard write the path of the Python they run with into each agent's settings.
// Nothing here reads or sends your sessions; the install only fetches SessionCost itself.
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const VERSION = require("../package.json").version;
const WINDOWS = process.platform === "win32";
const PROBE_MS = 20000;        // starting a Python (Windows Store Python takes ~2 s)
const INSTALL_MS = 600000;     // making the environment and installing

function home(env = process.env) {
  return env.SESSIONCOST_NPM_HOME || path.join(os.homedir(), ".sessioncost", "npm");
}

function venvPython(venv) {
  return WINDOWS ? path.join(venv, "Scripts", "python.exe") : path.join(venv, "bin", "python");
}

// What to install: this version from PyPI, or the source tree this package sits in (a test build), or what
// SESSIONCOST_PIP_SPEC says.
function spec(env = process.env, here = __dirname) {
  if (env.SESSIONCOST_PIP_SPEC) return env.SESSIONCOST_PIP_SPEC;
  const tree = path.resolve(here, "..", "..");
  try {
    if (/^name = "sessioncost"$/m.test(fs.readFileSync(path.join(tree, "pyproject.toml"), "utf8"))) return tree;
  } catch (e) { /* not in a source tree */ }
  return `sessioncost==${VERSION}`;
}

function run(cmd, args, timeout, quiet = true) {
  const r = spawnSync(cmd, args, { stdio: quiet ? "pipe" : "inherit", timeout, encoding: "utf8", windowsHide: true });
  return { ok: !r.error && r.status === 0, out: (r.stdout || "").trim(), err: r.error ? String(r.error.message) : (r.stderr || "").trim() };
}

function onPath(name, env = process.env) {
  const exts = WINDOWS ? [".exe", ".cmd", ".bat", ""] : [""];
  for (const dir of (env.PATH || env.Path || "").split(path.delimiter)) {
    for (const ext of exts) {
      const f = path.join(dir, name + ext);
      if (!dir) continue;
      try { if (fs.statSync(f).isFile()) return f; } catch (e) {
        // Windows Store Python's python.exe is an app link that cannot be opened for stat: accept the link itself
        // (whether it runs is checked next)
        try { if (fs.lstatSync(f).isSymbolicLink()) return f; } catch (e2) { /* next */ }
      }
    }
  }
  return null;
}

// A Python 3.10 or newer on the PATH: [command, ...its first arguments], or null.
function findPython(which = onPath, runner = run) {
  const tries = WINDOWS ? [["py", "-3"], ["python"], ["python3"]] : [["python3"], ["python"]];
  for (const [name, ...first] of tries) {
    const exe = which(name);
    if (!exe) continue;
    const r = runner(exe, [...first, "-c", "import sys; print(int(sys.version_info >= (3, 10)))"], PROBE_MS);
    if (r.ok && r.out === "1") return [exe, ...first];
  }
  return null;
}

// The steps that make the environment and install SessionCost into it: [[command, args], ...], or null.
function installSteps(venv, what, which = onPath, runner = run) {
  const py = venvPython(venv);
  const uv = which("uv");
  if (uv) {
    return [[uv, ["venv", "--quiet", "--python", ">=3.10", venv]],
            [uv, ["pip", "install", "--quiet", "--python", py, "--reinstall-package", "sessioncost", what]]];
  }
  const python = findPython(which, runner);
  if (!python) return null;
  return [[python[0], [...python.slice(1), "-m", "venv", venv]],
          [py, ["-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--force-reinstall", "--no-deps", what]]];
}

function main(argv = process.argv.slice(2), env = process.env) {
  const dir = home(env);
  const venv = path.join(dir, "venv");
  const stamp = path.join(dir, "installed.txt");     // what is in the environment: changes when this package does
  const what = spec(env);
  const want = `${VERSION} ${what}`;
  const py = venvPython(venv);
  let have = "";
  try { have = fs.readFileSync(stamp, "utf8").trim(); } catch (e) { /* first run */ }
  if (have !== want || !fs.existsSync(py)) {
    const steps = installSteps(venv, what);
    if (!steps) {
      console.error("SessionCost runs on Python. Install uv (https://docs.astral.sh/uv/getting-started/installation/), " +
                    "which fetches Python by itself, or Python 3.10 or newer (https://www.python.org/downloads/), " +
                    "then run npx sessioncost again.");
      return 1;
    }
    console.error(`SessionCost ${VERSION}: installing once into ${venv} …`);
    fs.rmSync(venv, { recursive: true, force: true });
    fs.mkdirSync(dir, { recursive: true });
    fs.rmSync(stamp, { force: true });
    for (const [cmd, args] of steps) {
      const r = run(cmd, args, INSTALL_MS);
      if (!r.ok) {
        console.error(`SessionCost could not be installed: ${path.basename(cmd)} ${args.join(" ")}\n${r.err}`.trim());
        console.error("Check your internet connection, then run npx sessioncost again.");
        fs.rmSync(venv, { recursive: true, force: true });
        return 1;
      }
    }
    fs.writeFileSync(stamp, want + "\n");
  }
  const r = spawnSync(py, ["-m", "sessioncost", ...argv], { stdio: "inherit" });
  if (r.error) {
    console.error(`SessionCost could not start: ${r.error.message}`);
    return 1;
  }
  return r.status === null ? 1 : r.status;
}

module.exports = { spec, findPython, installSteps, venvPython, home, main, VERSION };

if (require.main === module) process.exitCode = main();
