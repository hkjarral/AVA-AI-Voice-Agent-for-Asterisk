# Pull Request and CI Workflow

This workflow keeps feedback early while avoiding a full review and Docker build for every intermediate commit.

## Lifecycle

1. Create a focused branch from the latest `main` and build a coherent vertical slice.
2. Open a **draft** pull request. Fast tests, lint, compilation, secret checks, CLI builds, and CodeQL run on every push. Superseded runs for the same PR are canceled.
3. At the first useful checkpoint, comment `@codex review`. CodeRabbit reviews drafts automatically and pauses after its first reviewed commit. Wait for both reviewers, triage their comments together, and push one cohesive fix batch.
4. Continue implementation and local testing. Do not request a new bot review for every push.
5. When the implementation and documentation are complete, freeze the PR head and apply the `full-ci` label. This runs the Docker image-size validation even if the PR remains a draft.
6. On that frozen head, comment `@codex review` and `@coderabbitai full review` once. Do not push while either final review is pending.
7. Review both results together. Address only current, actionable findings. If a final fix is required, push one cohesive batch, rerun `full-ci`, and request a final review only when the change materially invalidates the prior result.
8. Mark the PR ready after the final checks pass and all actionable conversations are resolved. Squash-merge it into `main`.

## CI stages

| Stage | Trigger | Required work |
| --- | --- | --- |
| Draft / fast | Every PR push | Core Python tests and coverage, Admin backend tests, Admin frontend lint/tests/build, secret scan, source compilation, CLI cross-compile/tests, CodeQL |
| Final | Explicit `full-ci` label, non-draft PR open/update, manual run, or protected-branch push | Everything above plus Docker image builds and size budgets |

The stable `PR gate` check aggregates the CI jobs. It accepts an intentionally skipped Docker job on an ordinary draft, but requires that job to pass for a non-draft or `full-ci` PR. A canceled or failed dependency fails the gate. Applying `full-ci` before marking a draft ready is mandatory; the ready transition itself is not a workflow trigger, avoiding a duplicate full run on the same commit.

Path-scoped security and image workflows continue to run when their files change. All PR workflows use per-PR concurrency so a newer commit cancels a superseded run instead of consuming runner time in parallel.

## Review commands

- `@codex review` — request a Codex review at a coherent checkpoint.
- `@coderabbitai full review` — request a fresh CodeRabbit review of the frozen final diff.
- `@coderabbitai pause` / `@coderabbitai resume` — override automatic CodeRabbit review behavior when needed.

CodeRabbit and Codex reviews are initially advisory. The protected branch requires the consolidated CI gate, CodeQL, a pull request, and resolved conversations. Maintainers retain an emergency administrator bypass; force pushes and branch deletion remain blocked.

## Maintainer checklist

- Confirm the PR is linked to its issue and the changelog is current.
- Confirm test evidence matches the risk, including call IDs for telephony changes.
- Confirm the final reviews and final CI refer to the current head SHA.
- Resolve or explicitly defer every actionable conversation before merge.
- Use squash merge so the protected branch receives one coherent commit.
