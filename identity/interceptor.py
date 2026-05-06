"""
AIDE Identity — Explicit Target Interceptor
Detects device-targeting intent in user messages BEFORE the LLM reasons.

Step 7 from the attack plan: mark commands as explicit_target_required=True
when the user mentions a device name. Then enforce strict routing.

This runs at the agent loop level — before tool calling, before LLM reasoning.
It gives the agent three pieces of information:
  1. Does this message contain an explicit device target?
  2. What is the target phrase?
  3. What is the resolved device_id (or why resolution failed)?

The agent then passes this context to the LLM so it cannot reason incorrectly.
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from loguru import logger
from typing import Optional


@dataclass
class TargetResolution:
    """Result of explicit target detection and resolution."""

    has_explicit_target: bool
    target_phrase: Optional[str]  # raw phrase from user ("Midas", "my phone")
    device_id: Optional[str]  # resolved device_id or None
    canonical_name: Optional[str]  # "Midas", "AIDE Desk" etc.
    status: str  # "resolved" | "ambiguous" | "not_found" | "none"
    clarification_msg: Optional[str]  # message to show user if not resolved


# Verbs that signal cross-device intent
ROUTING_VERBS = {
    "send",
    "ping",
    "message",
    "tell",
    "ask",
    "notify",
    "forward",
    "route",
    "delegate",
    "approval",
    "brief",
    "update",
    "inform",
    "alert",
    "push",
}

# Prepositions that indicate a target follows
TARGET_PREPOSITIONS = {"to", "on", "via", "through", "at"}


class ExplicitTargetInterceptor:
    """
    Runs before the LLM reasoning loop.
    Detects whether the message contains an explicit device target
    and resolves it to a canonical device_id.

    Usage in agent.py run() method:
        resolution = interceptor.detect_and_resolve(user_message)
        if resolution.has_explicit_target:
            # Inject resolution into system prompt context
            # Enforce strict routing — no fallback allowed
    """

    def __init__(self, alias_registry, resolution_guard, memory=None):
        self._aliases = alias_registry
        self._guard = resolution_guard
        self._memory = memory
        self._known_targets: set[str] = set()
        self._refresh_targets()

    def _refresh_targets(self):
        """Refresh the set of known device targets from the alias registry."""
        try:
            self._known_targets = self._aliases.get_explicit_targets()
        except Exception:
            self._known_targets = set()

    def detect_and_resolve(self, message: str) -> TargetResolution:
        """
        Detect and resolve an explicit device target in the user's message.
        Returns a TargetResolution describing what was found.
        """
        self._refresh_targets()

        # Step 1: extract candidate target phrases
        target_phrase = self._extract_target_phrase(message)

        if not target_phrase:
            return TargetResolution(
                has_explicit_target=False,
                target_phrase=None,
                device_id=None,
                canonical_name=None,
                status="none",
                clarification_msg=None,
            )

        # Step 2: resolve via guard (strict, no fallback)
        device_id, clarification = self._guard.resolve_or_clarify(target_phrase)

        if device_id:
            canonical = self._aliases.get_canonical_name(device_id) or target_phrase

            # Store for recovery in case LLM fails to pass the target to the tool
            if self._memory:
                self._memory.store_fact("last_explicit_target_id", device_id)
                self._memory.store_fact("last_explicit_target_name", canonical)

            logger.info(
                f"ExplicitTargetInterceptor:\n"
                f"  Raw message:     {message[:60]!r}\n"
                f"  Target phrase:   {target_phrase!r}\n"
                f"  Resolved:        {canonical} ({device_id})\n"
                f"  Status:          resolved"
            )
            return TargetResolution(
                has_explicit_target=True,
                target_phrase=target_phrase,
                device_id=device_id,
                canonical_name=canonical,
                status="resolved",
                clarification_msg=None,
            )
        else:
            logger.warning(
                f"ExplicitTargetInterceptor:\n"
                f"  Raw message:     {message[:60]!r}\n"
                f"  Target phrase:   {target_phrase!r}\n"
                f"  Status:          not_resolved\n"
                f"  Clarification:   {clarification}"
            )
            return TargetResolution(
                has_explicit_target=True,
                target_phrase=target_phrase,
                device_id=None,
                canonical_name=None,
                status="not_found",
                clarification_msg=clarification,
            )

    def _extract_target_phrase(self, message: str) -> Optional[str]:
        msg_lower = message.lower()
        words = re.findall(r"\b\w+\b", msg_lower)

        # Generic words that need a routing verb to be considered device targets
        generic_aliases = {
            "mac",
            "phone",
            "mobile",
            "android",
            "desk",
            "desktop",
            "computer",
            "aide",
        }

        # Check two-word phrases first (higher specificity)
        for i in range(len(words) - 1):
            bigram = f"{words[i]} {words[i+1]}"
            if bigram in self._known_targets:
                return bigram

        # Strategy 1: exact single-word alias match
        # Non-generic aliases (like "midas", "topd") match anywhere in message
        has_routing_verb = any(v in words for v in ROUTING_VERBS)
        for word in words:
            if word in self._known_targets:
                if word not in generic_aliases:
                    # Specific device name — always match
                    return word
                elif has_routing_verb:
                    # Generic alias — only match if routing verb present
                    return word

        # Strategy 2: routing verb + preposition + noun
        if has_routing_verb:
            for i, word in enumerate(words):
                if word in TARGET_PREPOSITIONS and i + 1 < len(words):
                    candidate = words[i + 1]
                    if candidate in self._known_targets:
                        return candidate
                    if i + 2 < len(words):
                        bigram = f"{candidate} {words[i+2]}"
                        if bigram in self._known_targets:
                            return bigram

        return None

    def build_routing_context(self, resolution: TargetResolution) -> str:
        """
        Build a context string to inject into the LLM prompt when
        an explicit target was detected. This prevents context contamination.
        """
        if not resolution.has_explicit_target:
            return ""

        if resolution.status == "resolved":
            return (
                f"\n[MESH ROUTING CONTEXT — EXPLICIT TARGET DETECTED]\n"
                f"User requested device: {resolution.target_phrase!r}\n"
                f"Resolved to: {resolution.canonical_name} "
                f"(device_id: {resolution.device_id})\n"
                f"INSTRUCTION: Call route_to_device with "
                f"target={resolution.target_phrase!r}. "
                f"Do NOT use any other device. Do NOT use AIDE Desk unless "
                f"that is the explicitly resolved target.\n"
                f"[END ROUTING CONTEXT]"
            )
        else:
            return (
                f"\n[MESH ROUTING CONTEXT — TARGET UNRESOLVED]\n"
                f"User mentioned a device: {resolution.target_phrase!r}\n"
                f"This did not resolve to a known device.\n"
                f"INSTRUCTION: Do NOT attempt to route this message. "
                f"Tell the user: {resolution.clarification_msg}\n"
                f"[END ROUTING CONTEXT]"
            )
