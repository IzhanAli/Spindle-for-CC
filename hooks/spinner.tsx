// The Desktop app's half of spindle: a mod that puts the headlines in the
// Code tab's spinner row, and shows the story behind one when it's clicked.
//
// The terminal needs none of this. It samples its spinner word from the
// `spinnerVerbs` key the SessionStart hook writes, and the Desktop app draws
// its own row and ignores that key. So this module reads the same list back
// through `$.settings.read()`, picks one headline per turn, and draws it as a
// button beside the row's own mark. The work happens once, as the turn
// starts; drawing the row only reads the pick.
//
// A click on the headline opens a pane with the story's summary and, at the
// bottom, a link to read it. The pane looks the story up with `spindle story`
// then, and only then: nothing runs for it while Claude works.
//
// Where the row describes a tool step (`Editing app.ts`), that step stays: a
// headline never hides what Claude is doing.

import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, RenderElement } from 'claude-code'

import type { StoryCard } from '../types'

const headline = atom({ plugin: 'spindle', key: 'headline' } as const, null)
const story = atom({ plugin: 'spindle', key: 'story' } as const, null)

// What the Desktop row says when it has no step of its own to show.
const IDLE_WORD = 'Working'
// The headline's button in the row, and the pane a click on it opens.
const HEADLINE_BUTTON = 'spindle-headline'
const STORY_PANE = 'spindle-story'

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

/**
 * Ways to start spindle's CLI, best first: `bin/run`, which finds a Python
 * 3.8+ the way the SessionStart hook does, then Python itself, for a machine
 * with no `sh` on its PATH (native Windows outside Git Bash).
 */
function launchers(root: string): string[][] {
  const run = `${root}/bin/run`
  const cli = `${root}/bin/spindle`
  return [['sh', run], ['python3', cli], ['py', '-3', cli], ['python', cli]]
}

/** The story behind `text`, as `spindle story` finds it in the cache. */
async function lookUp($: EngineInterface, text: string): Promise<StoryCard> {
  for (const argv of launchers($.plugin.root)) {
    try {
      // `--`, so a headline that starts with a dash is not read as a flag.
      const ran = await $.process.run([...argv, 'story', '--', text], { timeoutMs: 20_000 })
      if (ran.exitCode !== 0) continue
      const found: unknown = JSON.parse(ran.stdout)
      if (found === null) return { headline: text, status: 'missing' }
      const { title, summary, url, byline } = found as Record<string, unknown>
      return {
        headline: text,
        status: 'found',
        title: typeof title === 'string' ? title : '',
        summary: typeof summary === 'string' ? summary : '',
        url: typeof url === 'string' ? url : '',
        byline: typeof byline === 'string' ? byline : '',
      }
    } catch {
      // Not on this machine, or no JSON came back: try the next way.
    }
  }
  return { headline: text, status: 'failed' }
}

/** Opens the story pane on `text`, then fills it in once the lookup is back. */
async function openStory($: EngineInterface, text: string): Promise<void> {
  await update($, story, () => ({ headline: text, status: 'loading' }))
  await $.ui.open({ id: STORY_PANE, title: 'Story', focus: true, closeOnEscape: true })
  const card = await lookUp($, text)
  // A click on another headline meanwhile has the pane now.
  await update($, story, shown => (shown?.headline === text ? card : shown))
}

/**
 * The story's address as the Desktop app links it: an http(s) URL spelled as
 * `new URL` spells it, with no `@` left in its path; null for anything else.
 */
function linkable(url: string): string | null {
  try {
    const parsed = new URL(url)
    if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return null
    return parsed.origin + `${parsed.pathname}${parsed.search}${parsed.hash}`.replace(/@/g, '%40')
  } catch {
    return null
  }
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
    const { word, message, mode, suffix } = e.props
    // A state message (compacting, retrying, ...) outranks any word.
    if (message !== null) return next(e)
    const isToolStep = (mode === 'tool-use' || mode === 'tool-input') && word !== IDLE_WORD
    if (isToolStep) return next(e)
    const current = await read($, headline)
    if (current === null) return next(e)
    const { Box, Button } = $.ui.resolve(e)
    // The row as the app draws it (its mark, and no words while it has no
    // step to name), then the headline as a button to click.
    return (
      <Box flexDirection="row" alignItems="center" columnGap={1}>
        {await next(e)}
        <Button key={HEADLINE_BUTTON} plain onPress={() => openStory($, current)}>
          {current + suffix}
        </Button>
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: STORY_PANE }, async ($, e) => {
    const { Box, Text, Link } = $.ui.resolve(e)
    const card = await read($, story)
    if (card === null) return <Text dimColor>Click a headline in the spinner to see its story here.</Text>

    let body: RenderElement
    if (card.status === 'loading') {
      body = <Text dimColor>Looking up the story…</Text>
    } else if (card.status === 'missing') {
      body = (
        <Text dimColor wrap="wrap">
          spindle no longer has this story: a refresh dropped it from the cache.
        </Text>
      )
    } else if (card.status === 'failed') {
      body = (
        <Text dimColor wrap="wrap">
          Couldn't run spindle to look this story up. Run /spindle:status to see why.
        </Text>
      )
    } else {
      const href = linkable(card.url)
      body = (
        <Box flexDirection="column" gap={1}>
          {card.byline !== '' && <Text dimColor wrap="wrap">{card.byline}</Text>}
          {card.summary !== '' ? (
            <Text wrap="wrap">{card.summary}</Text>
          ) : (
            <Box flexDirection="column">
              {card.title !== card.headline && <Text italic wrap="wrap">{card.title}</Text>}
              <Text dimColor wrap="wrap">No summary came with this story in its feed.</Text>
            </Box>
          )}
          {href !== null && <Link href={href}>Read the full story ↗</Link>}
        </Box>
      )
    }

    return (
      <Box flexDirection="column" gap={1} paddingX={1}>
        <Text bold wrap="wrap">{card.headline}</Text>
        {body}
      </Box>
    )
  })
}
