// The Desktop app's half of spindle: a mod that puts the headlines in the
// Code tab's spinner row.
//
// The terminal needs none of this. It samples its spinner word from the
// `spinnerVerbs` key the SessionStart hook writes, and the Desktop app draws
// its own row and ignores that key. So this module reads the same list back
// through `$.settings.read()`, picks one headline per turn, and swaps it into
// the Desktop row's word. The work happens once, as the turn starts; drawing
// the row only reads the pick.
//
// Where the row describes a tool step (`Editing app.ts`), that step stays: a
// headline never hides what Claude is doing.

import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

const headline = atom({ plugin: 'spindle', key: 'headline' } as const, null)

// What the Desktop row says when it has no step of its own to show.
const IDLE_WORD = 'Working'

function isHeadlineList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(v => typeof v === 'string')
}

/** The pool the SessionStart hook installed, read as the engine reads it. */
async function installedHeadlines($: EngineInterface): Promise<string[]> {
  const { spinnerVerbs } = await $.settings.read()
  if (typeof spinnerVerbs !== 'object' || spinnerVerbs === null) return []
  const { verbs } = spinnerVerbs as { verbs?: unknown }
  if (!isHeadlineList(verbs)) return []
  return [...new Set(verbs.map(v => v.trim()).filter(v => v !== ''))]
}

function shuffled(items: readonly string[]): string[] {
  const out = [...items]
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    const swap = out[i] as string
    out[i] = out[j] as string
    out[j] = swap
  }
  return out
}

export const register: Register = on => {
  // Each headline shows once before any repeats, in a fresh order per pass.
  // A refresh that lands mid-session changes the pool, which starts a new pass.
  let pool = ''
  let queue: string[] = []

  on('turn.start', async ($, e, next) => {
    try {
      const headlines = await installedHeadlines($)
      const key = headlines.join('\n')
      if (key !== pool) {
        pool = key
        queue = []
      }
      if (queue.length === 0) {
        const shown = await read($, headline)
        queue = shuffled(headlines)
        // Don't open a pass with the headline the last one closed on.
        if (queue.length > 1 && queue[0] === shown) queue.push(queue.shift() as string)
      }
      const pick = queue.shift() ?? null
      await update($, headline, () => pick)
    } catch {
      // No new pick this turn: the row keeps the last one, or its own word.
    }
    return next(e)
  })

  // Desktop only: the terminal already shows the headline through
  // `spinnerVerbs`, and its spinner redraws never reach this module.
  on('ui.render', { component: 'Spinner', surface: 'desktop' }, async ($, e, next) => {
    const { word, message, mode } = e.props
    // A state message (compacting, retrying, ...) outranks any word.
    if (message !== null) return next(e)
    const isToolStep = (mode === 'tool-use' || mode === 'tool-input') && word !== IDLE_WORD
    if (isToolStep) return next(e)
    const current = await read($, headline)
    if (current === null) return next(e)
    return next({ ...e, props: { ...e.props, word: current } })
  })
}
