# Contributing to AIDE

Thank you for considering a contribution. AIDE is a young, bootstrapped
project — every contribution genuinely moves it forward.

## Before you start

Read [`ARCHITECTURE.md`](docs/AIDE_PRODUCT_DOCUMENT.md) (or the relevant
document in `docs/`) for the area you're touching. AIDE has three
non-negotiable design principles that every change is checked against:

1. **Privacy first.** User data never leaves the device unless explicitly
   approved.
2. **No terminal for end users.** Anything a non-technical person needs
   to do must be reachable without the command line.
3. **Graceful degradation.** Every feature keeps working, or fails
   helpfully, when a dependency (model, network, service) is unavailable.

A pull request that violates one of these — however well written — will
be declined or asked to change, regardless of how clever the code is.

## Setting up your development environment

```bash
git clone https://github.com/PrinceKeldon/aide-mvp-release.git
cd aide-mvp-release
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in at minimum a Groq key and a Telegram bot token in `.env` (see the
main [README](README.md) for where to get them), then:

```bash
python3 main.py
```

## Running tests

```bash
pytest tests/
```

New functionality should come with tests. If you're fixing a bug, a
regression test that fails before your fix and passes after it is the
gold standard.

## Coding standards

- Match the existing style in the file you're editing rather than
  introducing a new one
- Type hints on new functions where practical
- No bare `except:` — catch specific exceptions
- Log with `loguru`, consistent with the rest of the codebase
- New tools inherit from `tools/base.py`'s `BaseTool` and declare a
  `safety_tier`

## Submitting a pull request

1. Fork the repo, create a branch from `main`
2. Make your change, with tests
3. Run `pytest tests/` and confirm everything passes
4. Open a PR using the template — it will ask which first principle your
   change supports, what you tested, and whether documentation needs
   updating
5. A maintainer will review — usually within a few days

Small, focused PRs are much easier to review and merge than large ones.
If you're planning something substantial, open a Discussion first so we
can align before you invest the time.

## Good first issues

Look for issues labelled
[`good first issue`](https://github.com/PrinceKeldon/aide-mvp-release/labels/good%20first%20issue).
These are real, useful tasks scoped to be approachable without deep
context on the whole system. Typical examples:

- Adding a new LLM provider to the model fallback chain
- Building a small new tool (unit converter, timezone lookup, etc.)
- Writing tests for an existing module that lacks coverage
- Improving error messages so they're clearer to non-technical users
- Translating the onboarding wizard into another language

## Proposing a larger feature (the sprint process)

AIDE's development happens in scoped "sprints" — see `docs/` for examples
of past sprint specs. If you want to propose a substantial new feature:

1. Open a GitHub Discussion describing the feature and which first
   principle it serves
2. Sketch the design — data model, new files, integration points
3. Wait for a maintainer to review and approve the direction before
   writing code
4. Open a draft PR early so feedback can happen incrementally rather than
   all at once at the end

## Reporting bugs

Use the bug report issue template. Include: what you expected, what
happened instead, your OS and Python version, and the relevant section of
your logs (`~/.aide/data/` — please redact API keys before pasting).

## Reporting security issues

**Do not open a public issue for security vulnerabilities.** See
[`SECURITY.md`](SECURITY.md) for how to report privately.

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).
Be kind. Assume good faith. We're building something that's meant to
help people — that should show in how we treat each other too.

## Questions

Open a [Discussion](https://github.com/PrinceKeldon/aide-mvp-release/discussions)
rather than an issue for anything that isn't a bug report or a concrete
feature proposal. Issues are for actionable, scoped items; Discussions
are for everything else.
