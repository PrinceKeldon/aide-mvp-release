# AIDE Build In Public Plan

Last updated: April 6, 2026

## Purpose

This document turns AIDE's current product and architecture progress into a practical build-in-public publishing system.

The goal is not to "do marketing."

The goal is to:

- explain what AIDE is in plain language
- share the technical lessons honestly
- create a public learning trail others can follow
- make the product understandable to mainstream users
- make the architecture legible to technical builders

## Core Thesis

AIDE is not just another chatbot.

It is a personal operating layer:

- one agent
- one memory
- many trusted devices
- one approval and action fabric
- one day-to-day workspace across inbox, calendar, and execution

And now it is growing into:

- sovereign agent-to-agent collaboration through M-Peer

The public story should reflect that progression.

## Audience

### Primary audience

- technical builders interested in agents, memory, trust, and workflow systems
- early adopters who want something more useful than a chat UI
- operators, founders, and product thinkers who care about trustworthy AI UX

### Secondary audience

- non-technical users who want to understand what a personal AI system could actually do for their day
- future collaborators, supporters, and potential design partners

## Public Narrative

### One-line version

"AIDE is a personal operating layer for your day: one agent, many trusted devices, one memory, and explicit approvals for real work."

### Extended version

"We are building AIDE as a personal agent OS that can run across your devices, remember context, help manage inbox and calendar, request approval for sensitive actions, and eventually collaborate with other sovereign AIDEs without either side giving up control."

## Editorial Principles

1. Human meaning before protocol detail

- explain what the system means to a person first
- explain implementation second

2. Show the tradeoffs

- what worked
- what broke
- what needed redesign

3. Avoid hype language

- no AGI claims
- no fake autonomy claims
- no "it does everything" framing

4. Stay plain-language by default

- technical depth can follow after the first clear explanation

5. Teach through the build

- every public post should include at least one reusable lesson

## Content Layers

### Layer 1: X

Purpose:

- momentum
- visible progress
- fast insights
- small architecture lessons

Formats:

- short build logs
- mini threads
- screenshots
- before/after posts
- lessons learned

Cadence:

- 3 to 5 posts per week

### Layer 2: Medium or long-form blog

Purpose:

- explain major product phases
- unpack decisions
- teach architecture and UX lessons

Formats:

- one essay per major milestone
- one post per important product principle

Cadence:

- 2 to 4 posts per month

### Layer 3: Repo docs

Purpose:

- canonical source of truth
- internal clarity
- raw material for public writing

Formats:

- build logs
- architecture docs
- milestone notes
- screenshots and demo references

## Publishing Roadmap

### Arc 1: Why AIDE exists

Key idea:

- current AI products are fragmented across chat, memory, tools, devices, and approvals
- AIDE is being built to unify those into one operating layer

Outputs:

- first Medium article
- 2 to 3 X posts

### Arc 2: Owner Mesh

Key idea:

- the real starting point for personal AI is not web autonomy
- it is continuity across your own trusted devices

Outputs:

- long-form post on owner mesh
- screenshots of `/mesh`
- X thread on trust, revoke, and approvals

### Arc 3: Your Day

Key idea:

- the right UI for a useful personal agent is an operational cockpit, not only chat

Outputs:

- long-form post on inbox, calendar, approvals, and interaction
- screenshots of `/your-day`
- X post series on why "chat-only" is not enough

### Arc 4: Model portability

Key idea:

- an agent should survive model changes
- runtime portability is part of product resilience

Outputs:

- post on local vs cloud vs fallback
- X post on Gemma, Gemini, and Ollama lessons

### Arc 5: M-Peer

Key idea:

- your AI should be able to work with my AI without either of us giving up control

Outputs:

- long-form post on sovereign peer collaboration
- X thread on why peers are not devices

## Public Series Outline

1. Why AIDE exists
2. Building a personal agent across trusted devices
3. Why approvals are a product primitive
4. Why "Your Day" is an operating cockpit, not a chatbot
5. The hard parts of email and calendar integration
6. What model portability means in a real agent system
7. Why sovereign agent-to-agent collaboration needs trust scopes
8. What we got wrong while building AIDE

## Artifact Plan

### Repo files

- [docs/BUILD_IN_PUBLIC_PLAN.md](/Users/frankkoine/aide/docs/BUILD_IN_PUBLIC_PLAN.md)
- [docs/BUILD_LOG.md](/Users/frankkoine/aide/docs/BUILD_LOG.md)
- [docs/X_POST_DRAFTS.md](/Users/frankkoine/aide/docs/X_POST_DRAFTS.md)
- [docs/blogs/WHY_AIDE_EXISTS.md](/Users/frankkoine/aide/docs/blogs/WHY_AIDE_EXISTS.md)

### Optional future files

- `docs/blogs/OWNER_MESH.md`
- `docs/blogs/YOUR_DAY.md`
- `docs/blogs/M_PEER.md`
- `docs/assets/screenshots/`
- `docs/assets/demos/`

## Demo Assets To Capture

### Already worth capturing

- `/mesh` trust dashboard
- `/your-day` overview
- `/your-day` approval actions
- `/m-peer` sovereign peer view
- `/m-peer` peer task lifecycle

### Best demo clips

1. Owner Mesh revoke and restore
2. Your Day tasking and approval flow
3. M-Peer scoped task request

## Suggested Publishing Order

### Week 1

- publish "Why AIDE exists"
- post 3 X updates from the draft bank

### Week 2

- publish Owner Mesh post
- share mesh screenshots and revoke/restore lessons

### Week 3

- publish Your Day post
- share inbox/calendar/action lessons

### Week 4

- publish M-Peer framing post
- share why sovereign peers are not devices

## Success Criteria

Build in public is working if:

- a non-technical reader can understand what AIDE is for
- a technical reader can learn from the architecture
- the story feels coherent across product, code, and demos
- the public trail reflects real progress instead of vague aspiration

## Working Rule

If a post cannot be understood by a smart non-engineer in its opening section, rewrite it before publishing.
