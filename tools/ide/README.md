## IDE Helper Toolkit

This directory contains optional helpers for contributors working from the `develop` branch. The goal is to speed up inner-loop tasks without modifying the production `Makefile` tracked on `main`.

### Files

- `Makefile.ide`: Wrapper targets for rapid linting, testing, and container operations.
- `README.md`: This document.

### Usage

- Run helper targets explicitly, e.g. `make -f tools/ide/Makefile.ide dev-test`.
- When adding new helpers, keep them scoped within this directory and document their purpose.

### Branch Policy

`develop` mirrors `main` plus the contents of:

- `.cursor/rules/`
- `.windsurf/rules/`
- `Agents.md`, `Gemini.md`
- Regression docs (`docs/resilience.md`, plus any long-form walkthroughs kept under `archived/regressions/`)
- This `tools/ide/` directory

Keep production-ready workflows in the root `Makefile` and use this toolkit for iterative IDE workflows.
