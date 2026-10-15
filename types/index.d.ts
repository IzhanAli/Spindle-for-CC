// The values spindle's mod (hooks/spinner.tsx) keeps in `$.state`.

/** The headline the Desktop spinner shows this turn; null leaves the row's own word. */
export type Headline = string | null

/**
 * The card a click on the Desktop headline opens: the story behind it as
 * `spindle story` found it, or why there is none to show.
 */
export type StoryCard =
  | { headline: string; status: 'loading' }
  /** No cached story has that headline any more: a refresh dropped it. */
  | { headline: string; status: 'missing' }
  /** spindle's CLI could not be run (no Python 3.8+), or it failed. */
  | { headline: string; status: 'failed' }
  | {
      headline: string
      status: 'found'
      /** The feed's own title, before the headline agent cleaned it. */
      title: string
      /** The feed's excerpt; empty when the feed gave none. */
      summary: string
      url: string
      /** Where it's from and how old: `swift.org · Hacker News · 412 points · 3h ago`. */
      byline: string
    }

declare module 'claude-code' {
  interface PluginState {
    spindle: { headline: Headline; story: StoryCard | null }
  }
}
