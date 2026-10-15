import { describe, expect, test } from 'claude-code/testing'
import type { Engine, Mounted } from 'claude-code/testing'
import type { On, RenderPropsOf, RenderSurface } from 'claude-code'

const POOL = ['Apple ships Swift 6.2', 'Rust 1.90 lands', 'Kotlin 2.3 is out']

type Spinner = RenderPropsOf['Spinner']

const THINKING: Spinner = { word: 'Working', message: null, suffix: '…', mode: 'thinking' }

const PANE: RenderPropsOf['Pane'] = {
  title: 'Story',
  isFocused: true,
  bodyColumns: 60,
  placement: 'dock',
  scroll: { offset: 0, bodyRows: 20 },
  view: {},
}

const SWIFT = {
  headline: 'Apple ships Swift 6.2',
  title: 'Swift 6.2 Released',
  summary: 'Swift 6.2 makes concurrency more approachable and adds InlineArray.',
  url: 'https://www.swift.org/blog/swift-6.2-released/',
  byline: 'swift.org · Hacker News · 412 points · 3h ago',
}

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

/**
 * Answers `$.process.run` as the machine would: `answer` gets each argv and
 * returns spindle's stdout, or undefined when that launcher isn't installed.
 * Records each argv run, and each pane opened.
 */
function machine(on: On, answer: (argv: readonly string[]) => string | undefined) {
  const runs: (readonly string[])[] = []
  const opened: string[] = []
  on('process.run', ($, e) => {
    runs.push(e.argv)
    const stdout = answer(e.argv)
    if (stdout === undefined) throw new Error(`${e.argv[0]}: not found`)
    return {
      value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
    }
  })
  on('ui.open', ($, e) => {
    opened.push(e.id)
    return { value: { isPlaced: true } }
  })
  return { runs, opened }
}

let turns = 0
let rows = 0

async function turn($: Engine) {
  turns += 1
  await $.turn.start({ text: 'hi', turnId: `turn-${turns}` })
}

/** The headline the row's button shows, if it shows one. */
async function shownIn(row: Mounted<RenderSurface, 'Spinner'>) {
  return (await row.find({ type: 'Button', key: 'spindle-headline' }))?.text
}

/** Draws a spinner row; resolves the row and the headline its button shows. */
async function draw($: Engine, surface: RenderSurface, props: Partial<Spinner> = {}) {
  rows += 1
  const row = await $.ui.mount({
    plugin: 'spindle',
    surface,
    component: 'Spinner',
    requestId: `agent-${rows}`,
    props: { ...THINKING, ...props },
  })
  return { row, headline: await shownIn(row) }
}

async function storyPane($: Engine) {
  return $.ui.mount({
    plugin: 'spindle',
    surface: 'desktop',
    component: 'Pane',
    requestId: 'spindle-story',
    props: PANE,
  })
}

describe('desktop spinner', () => {
  test('shows a headline from the installed pool', async ($, on) => {
    engine(on, () => POOL)
    await turn($)
    const { headline } = await draw($, 'desktop')
    expect(POOL.map(h => h + '…')).toContain(headline)
  })

  test("keeps the row's own mark beside the headline", async ($, on) => {
    const words = engine(on, () => POOL)
    await turn($)
    await draw($, 'desktop')
    expect(words).toEqual(['Working'])
  })

  test('keeps the headline through the turn', async ($, on) => {
    engine(on, () => POOL)
    await turn($)
    const { row, headline } = await draw($, 'desktop')
    const shown = [headline]
    for (const mode of ['requesting', 'responding'] as const) {
      await row.redraw({ ...THINKING, mode })
      shown.push(await shownIn(row))
    }
    expect(headline).toBeDefined()
    expect(new Set(shown).size).toBe(1)
  })

  test('shows every headline once before repeating', async ($, on) => {
    engine(on, () => POOL)
    const shown: (string | undefined)[] = []
    for (let i = 0; i < POOL.length; i++) {
      await turn($)
      shown.push((await draw($, 'desktop', { suffix: '' })).headline)
    }
    expect([...shown].sort()).toEqual([...POOL].sort())
  })

  test('moves to a new pool when a refresh lands mid-session', async ($, on) => {
    let pool = POOL
    engine(on, () => pool)
    await turn($)
    pool = ['Go 1.26 ships']
    await turn($)
    const { headline } = await draw($, 'desktop')
    expect(headline).toBe('Go 1.26 ships…')
  })

  test('leaves a tool step in view', async ($, on) => {
    const words = engine(on, () => POOL)
    await turn($)
    const { headline } = await draw($, 'desktop', { mode: 'tool-use', word: 'Editing app.ts' })
    expect(words).toEqual(['Editing app.ts'])
    expect(headline).toBeUndefined()
  })

  test('fills a tool step that shows no words', async ($, on) => {
    engine(on, () => POOL)
    await turn($)
    const { headline } = await draw($, 'desktop', { mode: 'tool-use', word: 'Working' })
    expect(POOL.map(h => h + '…')).toContain(headline)
  })

  test('leaves a state message alone', async ($, on) => {
    engine(on, () => POOL)
    await turn($)
    const { headline } = await draw($, 'desktop', { message: 'Compacting conversation' })
    expect(headline).toBeUndefined()
  })

  test('keeps the row as it is with no headlines installed', async ($, on) => {
    const words = engine(on, () => undefined)
    await turn($)
    const { headline } = await draw($, 'desktop')
    expect(words).toEqual(['Working'])
    expect(headline).toBeUndefined()
  })

  test('ignores a malformed verbs list', async ($, on) => {
    engine(on, () => 'not a list')
    await turn($)
    const { headline } = await draw($, 'desktop')
    expect(headline).toBeUndefined()
  })
})

describe('clicking the headline', () => {
  test('opens the story with its summary and a link to read it', async ($, on) => {
    engine(on, () => [SWIFT.headline])
    const { runs, opened } = machine(on, () => JSON.stringify(SWIFT))
    await turn($)
    const { row } = await draw($, 'desktop')
    await row.press({ key: 'spindle-headline' })

    expect(opened).toEqual(['spindle-story'])
    expect(runs[0]?.slice(-3)).toEqual(['story', '--', SWIFT.headline])
    const pane = await storyPane($)
    expect(await pane.find({ type: 'Text', text: SWIFT.headline })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: SWIFT.byline })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: SWIFT.summary })).toBeDefined()
    const link = await pane.find({ type: 'Link' })
    expect(link?.props.href).toBe('https://www.swift.org/blog/swift-6.2-released/')
    expect(link?.text).toContain('Read the full story')
  })

  test('puts the link last, under the summary', async ($, on) => {
    engine(on, () => [SWIFT.headline])
    machine(on, () => JSON.stringify(SWIFT))
    await turn($)
    await (await draw($, 'desktop')).row.press({ key: 'spindle-headline' })
    const pane = await storyPane($)
    const shown = await pane.findAll({})
    const types = shown.map(el => el.type).filter(t => t === 'Text' || t === 'Link')
    expect(types[types.length - 1]).toBe('Link')
  })

  test('shows the feed title when the feed gave no summary', async ($, on) => {
    engine(on, () => [SWIFT.headline])
    machine(on, () => JSON.stringify({ ...SWIFT, summary: '' }))
    await turn($)
    await (await draw($, 'desktop')).row.press({ key: 'spindle-headline' })
    const pane = await storyPane($)
    expect(await pane.find({ type: 'Text', text: SWIFT.title })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: /No summary/ })).toBeDefined()
    expect(await pane.find({ type: 'Link' })).toBeDefined()
  })

  test('escapes an @ in the link, as the Desktop app requires', async ($, on) => {
    engine(on, () => [SWIFT.headline])
    machine(on, () => JSON.stringify({ ...SWIFT, url: 'https://medium.com/@flutter/whats-new' }))
    await turn($)
    await (await draw($, 'desktop')).row.press({ key: 'spindle-headline' })
    const link = await (await storyPane($)).find({ type: 'Link' })
    expect(link?.props.href).toBe('https://medium.com/%40flutter/whats-new')
  })

  test('draws no link for a story without a web address', async ($, on) => {
    engine(on, () => [SWIFT.headline])
    machine(on, () => JSON.stringify({ ...SWIFT, url: '' }))
    await turn($)
    await (await draw($, 'desktop')).row.press({ key: 'spindle-headline' })
    const pane = await storyPane($)
    expect(await pane.find({ type: 'Text', text: SWIFT.summary })).toBeDefined()
    expect(await pane.find({ type: 'Link' })).toBeUndefined()
  })

  test('says so when the story has left the cache', async ($, on) => {
    engine(on, () => [SWIFT.headline])
    machine(on, () => 'null')
    await turn($)
    await (await draw($, 'desktop')).row.press({ key: 'spindle-headline' })
    const pane = await storyPane($)
    expect(await pane.find({ type: 'Text', text: /no longer has this story/ })).toBeDefined()
  })

  test('falls back to Python when there is no sh', async ($, on) => {
    engine(on, () => [SWIFT.headline])
    const { runs } = machine(on, argv => (argv[0] === 'py' ? JSON.stringify(SWIFT) : undefined))
    await turn($)
    await (await draw($, 'desktop')).row.press({ key: 'spindle-headline' })
    expect(runs.map(argv => argv[0])).toEqual(['sh', 'python3', 'py'])
    expect(await (await storyPane($)).find({ type: 'Text', text: SWIFT.summary })).toBeDefined()
  })

  test('says so when spindle cannot run at all', async ($, on) => {
    engine(on, () => [SWIFT.headline])
    const { runs } = machine(on, () => undefined)
    await turn($)
    await (await draw($, 'desktop')).row.press({ key: 'spindle-headline' })
    expect(runs).toHaveLength(4)
    const pane = await storyPane($)
    expect(await pane.find({ type: 'Text', text: /Couldn't run spindle/ })).toBeDefined()
  })
})

describe('terminal spinner', () => {
  test('is left to spinnerVerbs', async ($, on) => {
    const words = engine(on, () => POOL)
    await turn($)
    const { headline } = await draw($, 'terminal', { word: 'Sauteing' })
    expect(words).toEqual(['Sauteing'])
    expect(headline).toBeUndefined()
  })
})
