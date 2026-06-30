# Security Policy

AIDE handles sensitive personal data by design — your conversations,
email, financial records, and device identities. We take vulnerability
reports seriously and appreciate responsible disclosure.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Instead, report privately using one of these methods:

- **GitHub Security Advisories** (preferred): open a
  [private security advisory](https://github.com/PrinceKeldon/aide-mvp-release/security/advisories/new)
  on this repository
- **Email**: send details to the maintainer directly — see the contact
  listed on the maintainer's GitHub profile

Please include:

- A description of the vulnerability and its potential impact
- Steps to reproduce, or a proof of concept if available
- The version/commit you tested against
- Your suggested severity, if you have one

## What to expect

- **Acknowledgement** within 5 business days
- **Initial assessment** within 10 business days — confirming the issue,
  requesting more information, or explaining why it's out of scope
- **Resolution timeline** communicated once the issue is confirmed,
  depending on severity
- **Credit** in the release notes and `CONTRIBUTORS.md`, if you'd like it

We ask that you give us a reasonable window to fix a confirmed issue
before any public disclosure.

## Scope

In scope:

- The core AIDE agent, tools, and mesh protocol in this repository
- The local web UI and Telegram bridge
- The onboarding wizard and credential handling
- Memory and data storage (SQLite, ChromaDB integration)

Out of scope:

- Vulnerabilities in third-party dependencies (please report those
  upstream — though let us know too, so we can pin a patched version)
- Vulnerabilities requiring physical access to an already-compromised
  device
- Social engineering attacks against individual users

## A note on AIDE's security model

AIDE is local-first: your data lives on your device by default. The
main attack surfaces worth understanding before reporting:

- **The mesh protocol** — device-to-device coordination is signed with
  Ed25519 keys. A vulnerability here (signature bypass, replay attacks,
  unauthorized trust grants) is high severity.
- **Credential storage** — API keys and tokens are written to `~/.aide/.env`
  with restricted file permissions. A vulnerability that exposes these to
  other local processes or users is high severity.
- **The approval/safety gate** — anything that lets an action bypass its
  assigned safety tier (e.g. an `approve`-tier action executing without
  confirmation) is high severity.
- **The web UI** — runs on `localhost` by default. A vulnerability that
  allows remote access without the user's explicit configuration is high
  severity.

## Known limitations (not vulnerabilities, but worth knowing)

- Mesh transport is currently `ws://` (signed, not encrypted at the
  transport layer) — see open issues for the encryption roadmap
- Device private keys are stored unencrypted on disk, consistent with the
  local-first model but worth being aware of on a shared machine
- AIDE is pre-1.0 and under active development — treat it accordingly for
  highly sensitive use cases until a stable release is tagged

If you find something in this list more severe than described, please
still report it — context matters and we'd rather hear about it.

Thank you for helping keep AIDE and its users safe.
