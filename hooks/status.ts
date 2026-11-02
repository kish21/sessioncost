import type { EngineInterface as Engine, Register } from 'claude-code'

// The guard's one switch is ~/.sessioncost/guard/config.json "on" (SESSIONCOST_GUARD_DIR moves the folder).
// This module only shows it; switching stays with /livesession and `sessioncost guard on|off`. In the terminal it is
// a label beside the model; elsewhere (VS Code, the desktop app), which draw no footer labels for plugins, it is a
// short status line under the message box.
const WHERE = 'import os, pathlib; print(os.environ.get("SESSIONCOST_GUARD_DIR") or pathlib.Path.home() / ".sessioncost" / "guard")'

let config: string | undefined         // the config file's path, found once per load
let stop: (() => void) | undefined
let label: string | undefined          // what to show: undefined while the guard is off
let footer = false                     // this surface draws the footer's mode labels

async function where($: Engine): Promise<string | undefined> {
  for (const py of ['python', 'py', 'python3']) {
    try {
      const dir = (await $.process.run([py, '-c', WHERE], { timeoutMs: 20000 })).stdout.trim()
      if (dir) return `${dir}/config.json`
    } catch {
      // not on the PATH: the next name
    }
  }
  return undefined
}

async function show($: Engine): Promise<void> {
  let next: string | undefined
  try {
    config ??= await where($)
    const c = config ? JSON.parse(await $.fs.read(config)) : {}
    next = c.on ? (c.strict ? '🛡 Guard on (strict)' : '🛡 Guard on') : undefined
  } catch {
    next = undefined                   // no config yet: the guard is off
  }
  if (next !== label) {
    label = next
    $.ui.invalidate('ui.render')       // the footer label, where it is drawn
  }
  $.ui.status(footer ? undefined : label)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    await show($)
    stop?.()
    stop = $.clock.every(5000, () => void show($))       // switched in a terminal: shown within 5 s
    return started
  })

  // /livesession on or off: shown as soon as the guard's own prompt hook has switched it
  on('prompt.submit', async ($, e, next) => {
    const sent = await next(e)
    await show($)
    return sent
  }).catch(($, e, next) => next(e))     // a failure here never holds up your message

  // terminal: a label beside the model name (red shield, yellow words, so it is seen), and no status line. The desktop
  // app raises this too but draws nothing for it, so there the status line stays.
  on('ui.render', { component: 'SessionMode' }, async ($, e, next) => {
    if (e.surface !== 'terminal') return next(e)
    if (!footer) {
      footer = true
      $.ui.status(undefined)
    }
    if (!label) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    const rest = e.props.modes.length ? await next(e) : null      // the engine's own labels first, as it draws them
    return h(Box, { flexDirection: 'row' }, rest, rest ? h(Text, { dimColor: true }, ' & ') : null,
      h(Text, { color: 'error', bold: true }, '🛡 '), h(Text, { color: 'warning', bold: true }, label.replace('🛡 ', '')))
  })
}
