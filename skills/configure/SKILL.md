---
name: configure
description: Tailor spindle's news to your stack — topics, feeds, subreddits, pool size, API mode, or the headline agent's model — by editing ~/.config/spindle/config.toml.
argument-hint: "[what to change, e.g. \"focus on Rust and Go\"]"
disable-model-invocation: true
allowed-tools: Read, Edit, Write, Bash(mkdir -p *), Bash(cp *), Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/run" *)
---

Help the user configure spindle. The config lives at `~/.config/spindle/config.toml` (or `$SPINDLE_CONFIG` if set). Every key is optional; anything missing falls back to the built-in default.

1. If the config file doesn't exist, create it from the fully commented template:
   `mkdir -p ~/.config/spindle && cp "${CLAUDE_PLUGIN_ROOT}/config.example.toml" ~/.config/spindle/config.toml`
2. Read the config file, and `${CLAUDE_PLUGIN_ROOT}/config.example.toml` for what each key means.
3. Apply what the user asked for: $ARGUMENTS
   If they didn't say, briefly show the current `mode`, `topics`, and feed list and ask what they'd like to change.
   - `topics` rank stories. `[[rss]]` entries and `[reddit].subreddits` are the scraper-mode sources. `mode = "api"` switches to NewsAPI + GNews + DEV.to + Hacker News.
   - Keep API keys out of the config file: suggest `NEWSAPI_KEY` / `GNEWS_KEY` in `~/.config/spindle/.env` instead.
   - `[ai]` tunes the headline-writer agent: `model` (empty means haiku), `max_words`, `batch_size`, `timeout_secs`, `claude_bin`. Cleaning can't be turned off — only cleaned headlines are ever shown.
   - Only add feed URLs you're confident are real RSS/Atom feeds; spindle skips dead feeds silently, so a wrong URL won't warn.
4. Keep the TOML valid and keep the template's comments. Then run
   `sh "${CLAUDE_PLUGIN_ROOT}/bin/run" refresh --background` — it returns immediately — and summarize in a line or two what changed. Don't wait for the refresh: new headlines reach the spinner on their own in about 20 seconds.
