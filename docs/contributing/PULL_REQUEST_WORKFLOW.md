# Pull Request and CI Workflow

This workflow keeps feedback early while avoiding a full review and Docker build for every intermediate commit.

## Lifecycle

1. Create a focused branch from the latest `main` and build a coherent vertical slice.
2. Open a **draft** pull request. Fast tests, lint, compilation, secret checks, CLI builds, and CodeQL run on every push. Superseded runs for the same PR are canceled.
3. Continue implementation and local testing. Do not request a bot review for every push.
4. When the implementation and documentation are complete, freeze the PR head and mark the draft ready. The ready transition runs final CI. Apply `full-ci` instead only when final validation must run while the PR remains a draft.
5. On that frozen ready head, comment `@coderabbitai full review` once. If a Codex review is useful, add the `codex-review` label or run the **Codex Targeted Review** workflow manually for the PR number. Do not push while either final review is pending.
6. Review both results together. Address only current, actionable findings. If a final fix is required, push one cohesive batch; the ready PR reruns final CI automatically. Request another final review only when the change materially invalidates the prior result.
7. Mark the PR ready after the final checks pass and all actionable conversations are resolved. Squash-merge it into `main`.

## CI stages

| Stage | Trigger | Required work |
| --- | --- | --- |
| Draft / fast | Every PR push | Core Python tests and coverage, Admin backend tests, Admin frontend lint/tests/build, secret scan, source compilation, CLI cross-compile/tests, CodeQL |
| Final | Ready-for-review transition, optional draft `full-ci` label, non-draft PR open/update, manual run, or protected-branch push | Everything above plus Docker image builds and size budgets |

The stable `PR gate` check aggregates the CI jobs. It accepts an intentionally skipped Docker job on an ordinary draft, but requires that job to pass for a non-draft or `full-ci` PR. A canceled or failed dependency fails the gate. The normal final path is the ready transition, which enforces final validation without depending on a label convention. `full-ci` is an explicit override for the less common case where maintainers need the same validation while keeping the PR in draft.

Path-scoped security and image workflows continue to run when their files change. All PR workflows use per-PR concurrency so a newer commit cancels a superseded run instead of consuming runner time in parallel.

## Review commands

- `codex-review` label — run the opt-in Codex Targeted Review workflow. Use this only on a frozen head when a targeted AI review is useful.
- **Codex Targeted Review** manual workflow — run the same opt-in Codex review by PR number from the Actions tab.
- `@coderabbitai full review` — request a fresh CodeRabbit review of the frozen final diff.
- `@coderabbitai pause` / `@coderabbitai resume` — override automatic CodeRabbit review behavior when needed.

The optional targeted Codex workflow requires the `OPENAI_API_KEY` repository secret and a `CODEX_REVIEW_MODEL` repository variable naming a model available to that key's API project. The action and CLI versions are pinned so model defaults cannot change between review runs.

CodeRabbit and Codex reviews are advisory. The protected branch requires the consolidated CI gate, CodeQL, a pull request, and resolved conversations. Maintainers retain an emergency administrator bypass; force pushes and branch deletion remain blocked.

## Maintainer checklist

- Confirm the PR is linked to its issue and the changelog is current.
- Confirm test evidence matches the risk, including call IDs for telephony changes.
- Confirm the final reviews, if requested, and final CI refer to the current head SHA.
- Resolve or explicitly defer every actionable conversation before merge.
- Use squash merge so the protected branch receives one coherent commit.
