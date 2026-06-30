# AIDE

**A personal AI agent that runs on your own device — not someone else's server.**

AIDE installs locally, remembers you across sessions, and works proactively
on your behalf: a morning brief before you wake up, email handled with your
approval, projects advanced while you're away, and — when you choose to pair
a second device — agents that coordinate directly with each other over an
encrypted local mesh.

Your data stays on your machine unless you explicitly approve it leaving.
There is no required cloud account and no telemetry.

---

## What AIDE does today

- **Daily brief** — a Telegram message each morning with your calendar,
  unread email, pending approvals, and prepared tasks for the day
- **Email** — read, search, draft, and send, with approval gates on
  anything that leaves your inbox
- **Projects** — long-horizon task tracking with autonomous subtask
  advancement and check-ins
- **Finance** — turn a bank statement PDF into a categorised monthly
  overview, processed locally
- **Web** — search and browse on your behalf
- **Mesh** — pair a second device (e.g. your phone) so your agents can
  ping, delegate tasks, and route approvals to whichever device is
  convenient, signed and verified end to end

AIDE runs primarily on a local model (Ollama) and can route specific tasks
to Groq, Gemini, or OpenAI if you provide API keys — you choose what goes
where.

## Getting started

```bash
git clone https://github.com/PrinceKeldon/aide-mvp-release.git
cd aide-mvp-release
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then add your keys — see below
python3 main.py
```

On first run, AIDE opens a setup wizard in your browser at
`http://localhost:3000/onboarding`. You'll need:

- A free [Groq API key](https://console.groq.com/keys) (required)
- A [Telegram bot token](https://t.me/BotFather) (required — this is how
  AIDE reaches you)
- Optionally, Gemini and/or OpenAI keys for tasks that benefit from them

No terminal interaction is needed after that — the wizard writes your
configuration for you.

### Running a local model

AIDE prefers a local model for anything you've marked private. Install
[Ollama](https://ollama.ai) and pull a model:

```bash
ollama pull llama3.1
```

If Ollama isn't running, AIDE falls back to your configured cloud
providers automatically.

## Architecture

See [`ARCHITECTURE.md`](docs/AIDE_PRODUCT_DOCUMENT.md) and the documents in
[`docs/`](docs/) for the full system design, including the mesh protocol,
the trust and approval model, and the long-term memory architecture.

At a glance:

```
main.py
├── core/        agent loop, model routing, safety gate, scheduler
├── memory/      SQLite facts + ChromaDB semantic recall
├── mesh/        device identity, trust graph, peer coordination
├── tools/       email, browser, finance, project, search
├── interface/   web UI, Telegram bridge, system tray
└── daily_brief/ the morning brief pipeline
```

## Safety model

Every action AIDE can take is classified into one of three tiers:

| Tier | Behaviour |
|---|---|
| `autonomous` | Acts silently, logs it — reviewable any time |
| `notify` | Acts, then tells you immediately |
| `approve` | Waits for your one-tap approval before acting |

Defaults are conservative. You can adjust the tier for any tool in
conversation, e.g. *"always send email without asking."*

## Contributing

Contributions are very welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md)
for how to get set up, the coding standards, and how the sprint-based
development process works. Good first issues are labelled
[`good first issue`](https://github.com/PrinceKeldon/aide-mvp-release/labels/good%20first%20issue).

## Security

Found a vulnerability? Please see [`SECURITY.md`](SECURITY.md) for how to
report it privately rather than opening a public issue.

## License

AIDE is licensed under [AGPL-3.0](LICENSE). In short: you're free to use,
modify, and self-host AIDE for any purpose, including commercially. If you
run a modified version of AIDE as a network service for others, you must
make your modified source available under the same license.

## Project values

- **Privacy first.** Your data never leaves your device unless you approve it.
- **No terminal required.** Every feature usable by a non-technical person
  is reachable without the command line.
- **Graceful degradation.** AIDE keeps working when a model, a network, or
  a service is unavailable.

These aren't aspirations — they're the gate every pull request is checked
against.
