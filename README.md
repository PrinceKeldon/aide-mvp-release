# AIDE

AIDE is a private personal intelligence layer that runs from your desktop browser. It helps you talk to your local agent, prepare your day, and use opt-in modules such as FinanceOS without requiring terminal setup for everyday use.

## MVP Status

This public MVP focuses on:

- local web chat
- first-run onboarding
- settings and module controls
- Your Day daily brief
- optional Telegram mobile access
- FinanceOS PDF ingestion and reporting

Coming later:

- Fit Genie
- Owner Mesh and M-Peer
- native mobile apps
- enterprise controls

## Run Locally

```bash
python -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env
venv/bin/python main.py
```

Open `http://localhost:3000` and complete setup in the browser. To run a second local instance, set a different port before launch, for example `WEB_PORT=3010 MESH_PORT=7433 venv/bin/python main.py`.

## Privacy

AIDE stores runtime data locally under `./data` and `~/.aide`. Do not commit those directories. FinanceOS transaction analysis is designed for local processing.

## Release Scope

See [docs/MVP_SCOPE_LOCK.md](docs/MVP_SCOPE_LOCK.md) and [docs/MVP_RELEASE_AUDIT.md](docs/MVP_RELEASE_AUDIT.md).
