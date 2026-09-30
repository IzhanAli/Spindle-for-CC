---
name: refresh
description: Pull fresh developer news in the background; the headline-writer agent cleans it and the spinner updates on its own.
disable-model-invocation: true
allowed-tools: Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/run" *)
---

!`sh "${CLAUDE_PLUGIN_ROOT}/bin/run" refresh --background`

Relay the line above to the user in one sentence. Don't wait for the refresh or poll for it — the spinner picks up the new headlines by itself, usually within about 20 seconds. They can run `/spindle:status` any time to see how it went.
