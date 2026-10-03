import { describe, expect, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On, RenderPropsOf, RenderSurface } from 'claude-code'

const POOL = ['Apple ships Swift 6.2', 'Rust 1.90 lands', 'Kotlin 2.3 is out']

type Spinner = RenderPropsOf['Spinner']

const THINKING: Spinner = { word: 'Working', message: null, suffix: '…', mode: 'thinking' }

/**
 * Stands in for the engine beneath the mod: settings holding `verbs` as the
 * SessionStart hook writes them, and a Spinner site that records the word it
 * was handed.
 */
function engine(on: On, verbs: () => unknown) {
  const words: string[] = []
  on('settings.read', () => {
    const list = verbs()
    return { value: list === undefined ? {} : { spinnerVerbs: { mode: 'replace', verbs: list } } }
  })
  on('turn.start', ($, e) => ({ turnId: e.turnId }))
  on('ui.render', { component: 'Spinner' }, ($, e) => {
    words.push(e.props.word)
    return { type: 'engine', ref: 0 }
  })
  return words
}

let turns = 0

async function turn($: Engine) {
  turns += 1
  await $.turn.start({ text: 'hi', turnId: `turn-${turns}` })
}

async function draw($: Engine, surface: RenderSurface, props: Partial<Spinner> = {}) {
  await $.ui.render({
    component: 'Spinner',
    surface,
    requestId: 'main',
    props: { ...THINKING, ...props },
  })
}

describe('desktop spinner', () => {
  test('shows a headline from the installed pool', async ($, on) => {
    const words = engine(on, () => POOL)
    await turn($)
    await draw($, 'desktop')
    expect(POOL).toContain(words[0])
  })

  test('keeps the headline through the turn', async ($, on) => {
    const words = engine(on, () => POOL)
    await turn($)
    await draw($, 'desktop')
    await draw($, 'desktop', { mode: 'requesting' })
    await draw($, 'desktop', { mode: 'responding' })
    expect(new Set(words).size).toBe(1)
  })

  test('shows every headline once before repeating', async ($, on) => {
    const words = engine(on, () => POOL)
    for (let i = 0; i < POOL.length; i++) {
      await turn($)
      await draw($, 'desktop')
    }
    expect([...words].sort()).toEqual([...POOL].sort())
  })

  test('moves to a new pool when a refresh lands mid-session', async ($, on) => {
    let pool = POOL
    const words = engine(on, () => pool)
    await turn($)
    pool = ['Go 1.26 ships']
    await turn($)
    await draw($, 'desktop')
    expect(words).toEqual(['Go 1.26 ships'])
  })

  test('leaves a tool step in view', async ($, on) => {
    const words = engine(on, () => POOL)
    await turn($)
    await draw($, 'desktop', { mode: 'tool-use', word: 'Editing app.ts' })
    expect(words).toEqual(['Editing app.ts'])
  })

  test('fills a tool step that shows no words', async ($, on) => {
    const words = engine(on, () => POOL)
    await turn($)
    await draw($, 'desktop', { mode: 'tool-use', word: 'Working' })
    expect(POOL).toContain(words[0])
  })

  test('leaves a state message alone', async ($, on) => {
    const words = engine(on, () => POOL)
    await turn($)
    await draw($, 'desktop', { message: 'Compacting conversation' })
    expect(words).toEqual(['Working'])
  })

  test('keeps the row as it is with no headlines installed', async ($, on) => {
    const words = engine(on, () => undefined)
    await turn($)
    await draw($, 'desktop')
    expect(words).toEqual(['Working'])
  })

  test('ignores a malformed verbs list', async ($, on) => {
    const words = engine(on, () => 'not a list')
    await turn($)
    await draw($, 'desktop')
    expect(words).toEqual(['Working'])
  })
})

describe('terminal spinner', () => {
  test('is left to spinnerVerbs', async ($, on) => {
    const words = engine(on, () => POOL)
    await turn($)
    await draw($, 'terminal', { word: 'Sauteing' })
    expect(words).toEqual(['Sauteing'])
  })
})
