---
name: status
description: Show spindle's cache, rotation state, and the headlines currently in the thinking spinner.
disable-model-invocation: true
allowed-tools: Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/run" *)
---

!`sh "${CLAUDE_PLUGIN_ROOT}/bin/run" status`

Show the spindle status above to the user verbatim in a code block. Then, only if something needs attention, add one short line about it:

- `refreshing now` → a background refresh is running; the spinner updates by itself when it finishes.
- `stories cached 0` or `last refresh never` (and not refreshing) → suggest `/spindle:refresh`.
- `claude not found` → nothing new can be cleaned, so the spinner won't update (raw titles are never shown). Setting `[ai].claude_bin` in `~/.config/spindle/config.toml` fixes it.
- `stories cached N (M cleaned)` with M well below N and not refreshing → the headline agent is failing. spindle retries on its own at the next session start; running `sh "${CLAUDE_PLUGIN_ROOT}/bin/run" clean -v` shows why.
- `legacy hook yes` → the old curl install is still registered and can re-add itself on every session start. Suggest running `spindle uninstall` once from that old install in a terminal (it removes its hook, launcher and checkout), then starting a new session.
