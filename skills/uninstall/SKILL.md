---
name: uninstall
description: Remove everything spindle wrote outside the plugin (spinnerVerbs in settings.json, the news cache, pre-plugin install leftovers) before uninstalling the plugin.
disable-model-invocation: true
allowed-tools: Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/run" *)
---

Uninstalling the plugin alone leaves its headlines in `~/.claude/settings.json`, because plugins can't set `spinnerVerbs` themselves. Clean that up first.

Run with the Bash tool:

```
sh "${CLAUDE_PLUGIN_ROOT}/bin/run" uninstall
```

Relay what was removed. Then tell the user to finish with:

```
/plugin uninstall spindle@spindle
```

Until they do, the plugin's SessionStart hook puts headlines back in the next session. The spinner goes back to its default words in the next session after the plugin is removed.
