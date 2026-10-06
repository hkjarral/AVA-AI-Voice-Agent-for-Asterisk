# Repository instructions

These instructions apply to the entire repository.

## Project context and working workflow

Before answering project questions, implementing changes, or troubleshooting a
deployment, read [AVA.mdc](AVA.mdc). It is the shared entry point for architecture,
research, development, deployment, call analysis, and contribution guidance.
Read the linked guides relevant to the task, and any additional applicable
instructions in the current project folder. Preserve existing local instructions
such as `CLAUDE.md`; do not replace them with a second copy of the project context.

Work against the user's actual deployment, whether development or production;
establish the target and access first. Diagnostic logging and audio capture are
user-controlled: explain the exact enable/restore steps and let the user apply
them. Keep private raw evidence separate from sanitized public reports.

Use the current repository policy below for reviews; older chats and optional
local skills are supporting context and may contain superseded commands.

## Review priorities

Prefer a small number of high-confidence, actionable findings over speculative or stylistic feedback. Review the current head commit and avoid repeating resolved or stale comments.

For changes that affect calls or operator state, trace all callers and the complete lifecycle, including setup, success, failure, timeout, retry, cleanup, and restart recovery. Give particular attention to:

- telephony ownership and fail-closed routing;
- DNC, callback, disposition, transfer, and other compliance-sensitive state;
- credentials, authorization boundaries, and redaction;
- async/concurrent state transitions and idempotency;
- migrations, rollback, backward compatibility, and deleted-resource behavior;
- whether tests and documentation support the claims made by the change.

Report a finding only when it is introduced or exposed by the PR, has a concrete impact, and can be located precisely. Distinguish blocking correctness or security issues from optional follow-up improvements.

## Pull-request review cycle

1. Open a draft only after a coherent vertical slice is ready to evaluate.
2. Continue implementation without triggering bot reviews on every push.
3. At final freeze, mark the draft ready. The transition runs final CI; request `@coderabbitai full review` once. If Codex review is useful, add the `codex-review` label or run the **Codex Targeted Review** workflow manually for the PR number. Use `full-ci` only when final validation must run without leaving draft state.
4. Do not push while final reviews are pending.
5. Triage final reviews together. If fixes are required, make one cohesive batch and rerun the final gate before merge.

See [the pull-request workflow](docs/contributing/PULL_REQUEST_WORKFLOW.md) for commands, CI behavior, and merge requirements.
