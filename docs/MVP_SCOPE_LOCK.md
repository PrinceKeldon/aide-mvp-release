# AIDE MVP Scope Lock

Date: 2026-05-06

This document freezes the public MVP surface for the first non-technical user release.

## In Scope

- AIDE Core: local web chat, memory, model routing, task safety, and setup.
- Web onboarding: welcome, agent naming, model key setup, completion.
- Settings: provider keys and module toggles with plain-language data access notes.
- Your Day: daily brief, reminders, schedule-aware summaries, and approval-aware actions.
- Telegram Mobile: optional mobile interface once a bot token is configured.
- FinanceOS: PDF ingestion, categorisation, budget setup, range-aware reporting, and finance chat.
- Browser and web search tools: opt-in from Settings.
- Email and Calendar: opt-in from Settings, not required for first response.

## Out Of Scope

- Fit Genie and FitnessOS: coming soon and disabled in the MVP.
- Owner Mesh and M-Peer: Phase 2 surfaces; not part of the public MVP navigation.
- Enterprise Mesh/admin console.
- Native iOS/Android applications.
- Pastor Studio/contact-message integration.
- Benchmark scripts, debug scripts, old generated HTML captures, local calendars, logs, databases, and backups.

## Release Gate

The MVP release can ship only from a clean-history export or orphan release branch. The existing development history is not suitable for a public repository because it has contained local data paths and sensitive runtime artifacts.

Required checks before public publication:

- `git secrets --scan`
- `git secrets --scan-history`
- A custom sensitive-file allowlist scan
- Focused pytest suite for runtime, FinanceOS, onboarding/settings, and Your Day
- Browser smoke test for `/onboarding`, `/settings`, `/`, `/your-day`, and `/finance`
- Manual non-technical user test: setup to first AIDE response in under 10 minutes
