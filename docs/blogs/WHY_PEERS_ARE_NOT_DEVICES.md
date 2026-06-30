# Why Peers Are Not Devices

Subtitle: The design decision that makes sovereign agent collaboration understandable and safe

Suggested publication title:

Why Peers Are Not Devices: The Product Decision Behind Sovereign Agent Collaboration

Alternate title options:

- Why Another Person’s AIDE Cannot Be One of Your Devices
- The M-Peer Design Choice That Protects Sovereignty

One-line hook:

If another person’s agent looks like one of your devices in the product model, the system starts lying about trust.

## The short version

One of the most important architecture decisions in AIDE is also one of the easiest to underestimate:

another person’s AIDE is not one of your devices.

That may sound obvious when stated plainly.

In practice, many systems would blur this line.

If you already have:

- pairing
- trust
- routing
- device records
- capability flags

then it is very tempting to take the next external thing and model it as “just another peer in the same device graph.”

We did not do that.

That choice matters a lot.

It matters for:

- safety
- onboarding
- revocation
- trust scope design
- approval logic
- product clarity

And most of all, it matters because M-Peer only makes sense if sovereignty stays real.

## The product truth underneath

Owner Mesh and M-Peer are not the same relationship.

That is the core truth.

Inside Owner Mesh:

- the human owner is the same
- the trust domain is shared
- the system spans the owner’s devices

Inside M-Peer:

- the humans are different
- the trust domains are different
- memory is not shared by default
- context must be scoped
- either side can revoke the relationship
- either side may require approval

Those are different worlds.

If you model them as the same entity class, the product starts to lie.

And once the product lies about trust, it stops being trustworthy.

## Why the shortcut is so tempting

From a coding point of view, the shortcut is attractive.

You already have:

- identity
- trust
- transport
- message routing
- capability checks

So why not just treat another person’s AIDE as one more node in the same graph?

Because technical reuse is not always product truth.

This is one of the hardest discipline problems in systems design.

The nearest abstraction is often not the right abstraction.

In this case, reusing the owner-device model would have created several problems immediately.

## Problem 1: it would blur sovereignty

If another person’s AIDE looks like one of my own devices in the model, then the system starts to imply sameness where there is none.

But another person’s AIDE is not:

- my phone
- my laptop
- my terminal

It has:

- a different owner
- a different memory
- a different approval domain
- a different trust boundary

The whole point of M-Peer is that these boundaries remain visible and respected.

If the model hides that, the product breaks at its conceptual core.

## Problem 2: it would make context sharing dangerously easy

Inside Owner Mesh, it can make sense for different devices to receive different levels of context because they all belong to one owner.

That assumption does not carry over to M-Peer.

For M-Peer, the safe default is:

- explicit context only
- no automatic memory sharing
- scoped task classes
- scoped context classes

If peers were modeled as devices, the system would constantly be fighting the wrong defaults.

That is a sign that the abstraction is wrong.

Good abstractions should make the safe behavior natural, not awkward.

## Problem 3: it would confuse users

This matters just as much as the technical side.

Imagine a person opening the dashboard and seeing:

- their phone
- their laptop
- their terminal
- another person’s AIDE

all presented as the same kind of thing.

That would be a product failure.

Because the user’s real questions are different:

For a device:

- can this device approve?
- can it receive my brief?
- can it execute work?

For a sovereign peer:

- who owns this agent?
- what can it ask mine to do?
- what can it receive?
- what happens if I revoke it?
- does it need approvals?

Those are not the same questions.

So the UI should not pretend they are.

## Problem 4: it would break the approval model

One of the most important differences between Owner Mesh and M-Peer is where approval lives.

Owner Mesh approvals happen inside one owner’s trust domain.

M-Peer can require approval on both sides:

- my AIDE may need my approval before sending a request
- your AIDE may need your approval before acting on it

That is a bilateral relationship.

A device model does not express that well.

A sovereign peer model does.

This is exactly why the M-Peer task log needed bilateral approval state.

That would have been much harder to reason about cleanly if peers had been squeezed into the owner-device schema.

## Problem 5: it would weaken revocation

Revocation for a device means:

- this device is no longer part of my active owner mesh

Revocation for a sovereign peer means:

- this external agent no longer has this scoped collaboration relationship with my AIDE

Those may sound similar, but the meaning is different.

One is intra-owner trust management.

The other is inter-owner sovereignty management.

If the system uses one generic concept for both, revocation starts to lose explanatory power.

And revocation only builds trust when people understand exactly what is being revoked.

## The better model

The better model is the one we chose:

- owner devices remain owner devices
- sovereign peers become their own canonical entity class

This makes several good things possible at once.

### 1. The trust model stays honest

Devices and peers can have different rules without hacks.

### 2. The UI stays understandable

The dashboard can explain:

- these are your trusted devices
- these are external sovereign peers

### 3. Safe defaults become natural

The peer layer can default to:

- explicit context only
- no memory sharing
- stricter scope rules
- bilateral approvals when needed

### 4. Future growth becomes cleaner

Once M-Peer becomes richer, it may need:

- peer-specific invitation flows
- scope templates
- bilateral audit trails
- pause and revoke semantics
- peer-facing capability discovery

All of this is easier when the product model already matches the product truth.

## This is really a product design lesson

At one level, this is a database and runtime decision.

But at a deeper level, it is a product design lesson:

the system model should match the human meaning of the relationship.

That sounds simple.

It is one of the hardest things to preserve as a product grows.

Because engineering pressure often pushes toward reuse.

Reuse is good when it preserves truth.

Reuse is dangerous when it erases distinctions the product depends on.

In AIDE, “peer” and “device” are such a distinction.

They must stay separate if the product is going to stay clear.

## Why this matters for mainstream onboarding

If we care about the other 5.2 billion people understanding and using systems like this, then this distinction matters even more.

Most people do not think in terms of:

- canonical registries
- transport layers
- entity classes
- trust scope serialization

They think in terms of:

- my devices
- your system
- what can it do
- what can it see
- can I stop it later

That means the product has to preserve those categories cleanly.

When the product categories are clear, onboarding gets easier.

When they are blurred, the system starts to feel like infrastructure instead of something a normal person can trust.

## What this decision made possible

Separating peers from devices made several later moves much cleaner:

- separate M-Peer invitation flow
- plain-language scope templates
- peer-specific policy validation
- bilateral peer task lifecycle
- dedicated `/m-peer` UI surface
- peer task logs that reflect sovereign collaboration rather than owner-device coordination

This is the kind of payoff good abstractions create.

The right separation early can make the later product feel obvious.

## The bigger principle

The bigger principle is this:

not every connection in a system is the same kind of relationship.

And if the system cannot express those relationship differences clearly, it will struggle to become trustworthy.

Owner Mesh and M-Peer needed different models because they are different human realities.

That is why peers are not devices.

## Closing line

Another person’s AIDE is not one more node in my device graph.

It is a separate sovereign agent entering a scoped, revocable relationship with mine.

Keeping that distinction clear is what makes M-Peer both understandable and safe.

## Suggested CTA

For the full series reading order, use the landing page: [Building AIDE In Public](/Users/frankkoine/aide/docs/blogs/SERIES_INDEX.md).
