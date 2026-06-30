# AIDE X Post Drafts

Last updated: April 6, 2026

These are draft-ready posts and short threads for build in public.

## 1. Why AIDE exists

Most AI products still feel like isolated chat tabs.

Your memory is in one place.
Your approvals are somewhere else.
Your inbox and calendar live elsewhere.
Your devices don’t share one operational layer.

We’re building AIDE to change that:

- one agent
- many trusted devices
- one memory
- one approval fabric
- one workspace for the day

## 2. Owner Mesh

I think “my AI across my devices” is a more important product problem than “AI that browses the web for me.”

We built AIDE’s Owner Mesh around one simple truth:

my phone, laptop, and terminal should feel like one trusted agent system, not disconnected surfaces.

## 3. Approvals

Hard lesson:

approvals are not a modal popup feature.

They are a core product primitive.

If an agent will ever touch email, files, calendar, or collaboration, approval has to be designed into the system from the start.

## 4. Your Day

“Your Day” started as a morning brief.

It became much more interesting once we stopped treating it like a summary and started treating it like an operating cockpit:

- inbox triage
- schedule visibility
- approval actions
- editable drafts
- live task state

## 5. Why peers are not devices

We just made a strong architecture choice in AIDE:

another person’s AIDE is not modeled as one of your devices.

That sounds obvious.
It isn’t.

A lot of systems would reuse the nearest pairing abstraction.
We didn’t, because it breaks the sovereignty model.

## 6. M-Peer

Plain-language version of what we’re building with M-Peer:

“My AIDE can ask your AIDE for help, but neither of us gives up control.”

That means:

- explicit trust
- explicit context
- revocable access
- visible approvals

## 7. Build lesson

One of the recurring lessons from building AIDE:

if a trust model is invisible to normal people, it is not finished.

The protocol can be elegant.
The database can be correct.
The runtime can be clean.

If the human can’t answer “what can this peer do?” then the product still isn’t ready.

## 8. Model portability

We’re building AIDE so the agent can survive model changes.

That matters more than people think.

The useful thing is not “this week’s best model.”
The useful thing is a system whose memory, trust, routing, approvals, and workflows keep working as models change.

## 9. Real productivity AI

Real productivity AI is not:

- one chat box
- vague summaries
- fake autonomy

It’s:

- visible state
- safe actions
- clear approvals
- real inbox/calendar integration
- continuity across devices

That’s the direction we’re pushing AIDE.

## 10. What we got wrong

Build in public means showing the misses too.

Some of the hardest AIDE problems were not “AI reasoning” problems.

They were:

- duplicate responses
- stale brief state
- raw HTML emails
- local runtime crashes
- disk pressure breaking memory/logging

Reliability is product design.

## Bonus thread: Why “chat-first” is too small

I’m increasingly convinced that the right interface for a useful personal agent is not only chat.

Chat is good for:
- asking
- clarifying
- deciding

But the actual workday also needs:
- a daily operating view
- pending approvals
- inbox state
- schedule conflicts
- editable drafts
- visible task progress

That’s why we built `Your Day` in AIDE as a panel, not just another prompt box.

## Thread draft: Why Your Day is an operating cockpit

1.

One of the strongest lessons from building AIDE:

useful productivity AI cannot be chat-only.

Chat is great for asking and clarifying.

But the workday also has:

- pending approvals
- inbox state
- drafts
- calendar conflicts
- things that need action now

2.

That’s why we built `Your Day`.

Not as a prettier summary.
Not as a “good morning” gimmick.

But as an operating cockpit.

3.

The difference between a summary and a cockpit is action.

A summary tells you what happened.

A cockpit tells you:

- what matters
- what is blocked
- what needs approval
- what you can do next

4.

The minute you add:

- edit draft
- approve and send
- dismiss
- snooze
- ask follow-up on an item

you stop building “AI content” and start building workflow.

5.

Another hard lesson:

if suggestions feel generic, people lose trust fast.

So schedule suggestions, email drafts, and approval items have to be grounded in real source state, not decorative AI prose.

6.

I think the future of productivity AI looks less like:

“one giant conversation”

and more like:

“a conversational system with strong operational surfaces.”

That’s the direction we’re pushing AIDE.

## Thread draft: Why approvals are a product primitive

1.

One of the biggest mistakes in AI product design is treating approvals like a small safety feature you can bolt on later.

If the system can take meaningful action, approvals are not a side feature.

They are part of the product model.

2.

As soon as an AI can:

- send email
- write to calendar
- share context
- route work
- collaborate with another agent

approval stops being “just a confirmation dialog.”

3.

The real question is not:

“Should there be approvals?”

It’s:

“How does approval shape the operating model without making the system unusable?”

4.

Too little approval:

- unsafe
- unpredictable
- hard to trust

Too much approval:

- exhausting
- slow
- not worth using

So the real design target is calibrated agency.

5.

That means:

- act silently when stakes are low
- notify when visibility matters
- require approval when consequences matter

This is much better than treating all actions the same.

6.

In AIDE, this showed up everywhere:

- Owner Mesh approvals
- email sending
- Your Day action cards
- M-Peer bilateral approvals

Same principle, different surfaces.

7.

For mainstream users, approvals are where trust becomes real.

People are not asking:

“What policy enum fired?”

They’re asking:

- what is this trying to do?
- what happens if I approve it?
- can I trust this tomorrow too?

That’s why approvals are a product primitive, not a popup.

## Thread draft: Why peers are not devices

1.

One of the most important design decisions in AIDE:

another person’s AIDE is not modeled as one of your devices.

That sounds obvious.

It is actually a hard and important product choice.

2.

Inside Owner Mesh:

- same human owner
- shared trust domain
- multiple trusted devices

Inside M-Peer:

- different humans
- different trust domains
- scoped collaboration only

Those are not the same relationship.

3.

If you model peers as devices, the system starts lying about trust.

It makes sovereignty blurry.
It makes defaults too permissive.
It makes the UI more confusing.

4.

The questions are different.

For a device:

- can it approve?
- can it execute?
- can it receive the brief?

For a peer:

- who owns this agent?
- what can it ask mine to do?
- what can it receive?
- what happens if I revoke it?

5.

That’s why we gave M-Peer its own:

- invitation flow
- trust scopes
- task logs
- UI surface
- policy model

The abstraction should match the human meaning of the relationship.

6.

This is one of those cases where code reuse would have been the wrong move.

The nearest abstraction was “device.”

The correct abstraction was “sovereign peer.”

That difference matters a lot.
