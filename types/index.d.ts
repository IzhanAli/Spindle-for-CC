// The values spindle's mod (hooks/spinner.ts) keeps in `$.state`.

/** The headline the Desktop spinner shows this turn; null leaves the row's own word. */
export type Headline = string | null

declare module 'claude-code' {
  interface PluginState {
    spindle: { headline: Headline }
  }
}
