# Hook Templates

Templates for building call lifecycle hooks.

## Available Templates

| File | Phase | When It Runs |
|------|-------|-------------|
| `template_pre_call_hook.py` | Pre-Call | After answer, before AI speaks |
| `template_in_call_hook.py` | In-Call | AI invokes during conversation |
| `template_post_call_hook.py` | Post-Call | After call ends (fire-and-forget) |

## How to Use

Open any template in Windsurf and tell AVA what you want to build:

> "Help me build a pre-call hook that looks up callers in my CRM"

> "Help me build an in-call tool that checks appointment availability"

> "Help me build a post-call webhook that sends call summaries to Slack"

AVA knows the architecture and will customize the template for your use case.

## Guides

- [Pre-Call Hooks](../../docs/contributing/pre-call-hooks-development.md)
- [In-Call Hooks](../../docs/contributing/in-call-hooks-development.md)
- [Post-Call Hooks](../../docs/contributing/post-call-hooks-development.md)

## YAML-Only Hooks (No Code)

Many hooks can be configured entirely in YAML without writing any code. Check the bottom of each template file for YAML examples, or see the guides above.
