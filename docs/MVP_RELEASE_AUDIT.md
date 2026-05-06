# MVP Release Audit

Date: 2026-05-06

## Secret Scan

- `git secrets --scan`: passed.
- `git secrets --scan-history`: passed.
- Regex scan of public candidate files: passed.

## History Risk

The current development repository history has contained paths matching private runtime data:

- `.env`
- `data/`
- `data.backup.*/`
- local SQLite databases
- Google calendar token/credential JSON files
- local calendar `.ics` files
- local mesh private keys

This means the public MVP must not be published as a normal branch with inherited history. Use a clean-history release export or orphan branch.

## Shipping Tree Policy

Exclude from public MVP:

- `data/`, `data.backup*/`, local DBs, Chroma data, logs, and keys
- `.env` and all non-example env files
- `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`
- debug files such as `mesh_identity_debug.py`, `benchmark_ollama_models.py`, `current_mesh_html.txt`, `old_mesh_html.txt`
- local calendar fixtures outside tests
- FitnessOS/Fit Genie implementation files unless re-enabled in a later release
- old mesh and M-Peer prototype entry points from primary navigation

## Current Status

- MVP scope lock created.
- Fit Genie disabled as coming soon.
- AIDE naming is applied across MVP-facing setup, settings, chat, FinanceOS, and Your Day copy.
- Existing user configuration that saved a legacy agent name is normalized to `AIDE` in the MVP UI.
- Public clean-history repository published at `PrinceKeldon/aide-mvp-release`.
- Default branch verified as `mvp-release`.
- Release tag `v0.1.0-mvp` published and verified against commit `a269dcd`.
