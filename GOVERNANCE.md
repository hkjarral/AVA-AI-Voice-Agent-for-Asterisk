# Governance

This document describes the decision-making process and project structure for Asterisk AI Voice Agent.

## Project Structure

AAVA is maintained by a single primary maintainer ([@hkjarral](https://github.com/hkjarral)) with community contributors. As the project grows, this structure may evolve.

### Roles

- **Maintainer**: Final authority on architecture decisions, releases, and roadmap. Reviews and merges PRs.
- **Contributors**: Anyone who submits code, documentation, bug reports, or ideas. All contributions are valued.

## Decision-Making

### Day-to-day decisions

The maintainer makes routine decisions about bug fixes, minor features, and release timing. Contributors are encouraged to discuss approaches in PR comments before investing significant effort.

### Feature proposals

For new features or significant changes:

1. **Start a discussion**: Open a [GitHub Discussion](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/discussions) in the "Ideas" category, or file a Feature Request issue
2. **Get feedback**: The maintainer and community provide input on scope, approach, and priority
3. **Write a milestone spec**: If accepted, create a spec using the [milestone template](docs/contributing/milestones/TEMPLATE.md) and submit as a Draft PR
4. **Implement**: Once the spec is approved, the feature moves to the [Roadmap](docs/ROADMAP.md) and implementation can begin

### Architecture decisions

Large architectural changes (new providers, transport changes, config schema changes) require a milestone spec before implementation. The maintainer has final say, but community input is actively sought and valued.

## Release Process

1. Feature work happens on branches off `develop`
2. PRs target `staging` (preferred) or `develop`
3. Releases are promoted from `staging` to `main` after golden baseline validation
4. See [RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md) for the full release gate

## Conflict Resolution

Technical disagreements are resolved through discussion on GitHub Issues or PRs. If consensus cannot be reached, the maintainer makes the final call.

For interpersonal conflicts, refer to our [Code of Conduct](CODE_OF_CONDUCT.md).

## Evolution

This governance model is intentionally lightweight. As the contributor community grows, we may adopt additional structures (e.g., core team, working groups). Changes to governance will be proposed via PR and discussed openly.
