---
name: reset
description: Restart spindle's headline rotation so every cached story is eligible again. Pass "all" to also clear the story cache.
argument-hint: "[all]"
disable-model-invocation: true
allowed-tools: Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/run" *)
---

If `$ARGUMENTS` is `all`, run with the Bash tool:

```
sh "${CLAUDE_PLUGIN_ROOT}/bin/run" reset --all
```

Otherwise run:

```
sh "${CLAUDE_PLUGIN_ROOT}/bin/run" reset
```

Relay the output in one line. If the cache was cleared, add that the next session (or `/spindle:refresh`) refetches the news.
