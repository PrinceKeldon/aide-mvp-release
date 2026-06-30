# AIDE M-Peer Scope Templates

Last updated: April 6, 2026

These are the first safe default trust templates for M-Peer.

They are designed to be understandable to mainstream users and strict by default.

## Research Partner

Description:

Let another AIDE ask for research, analysis, and summarization help.

Allowed task classes:

- research
- analysis
- summarization

Allowed context classes:

- explicit_facts
- public_context

Approvals:

- no approval required by default for low-risk research requests

Sandbox mode:

- strict

Risk level:

- low

One-line explanation:

This peer can ask my AIDE for research help, but only with explicit information I choose to share.

## Calendar Coordination

Description:

Let another AIDE coordinate schedules and compare explicit availability.

Allowed task classes:

- calendar_coordination
- scheduling
- availability_check

Allowed context classes:

- explicit_facts
- schedule_windows

Approvals:

- approval recommended for changes that imply commitments

Sandbox mode:

- strict

Risk level:

- medium

One-line explanation:

This peer can coordinate calendars with my AIDE, but only around the availability I explicitly expose.

## Project Collaboration

Description:

Let another AIDE help with work on a shared project under scoped rules.

Allowed task classes:

- project_coordination
- summarization
- analysis
- drafting

Allowed context classes:

- explicit_facts
- project_context

Approvals:

- approval recommended for outward-facing actions or sensitive task transitions

Sandbox mode:

- limited

Risk level:

- medium

One-line explanation:

This peer can collaborate with my AIDE on a shared project, but only inside a limited project scope.

## Assistant Introduction

Description:

Allow two AIDEs to know each other and exchange minimal identity and capability information.

Allowed task classes:

- introduction
- capability_discovery

Allowed context classes:

- explicit_facts

Approvals:

- none by default

Sandbox mode:

- strict

Risk level:

- low

One-line explanation:

This peer can introduce itself and discover basic capabilities, but cannot request broader work yet.
