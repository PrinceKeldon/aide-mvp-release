"""
AIDE Identity — Resolution Guard
Enforces strict routing rules before any cross-device action.

The non-negotiable rule:
  If a user explicitly names a target device and resolution is ambiguous
  or fails — do NOT fallback to the default node.
  Ask or refuse. Never substitute silently.

Every cross-device routing call must pass through this guard.
The guard either returns a confirmed device_id or raises ResolutionError.
"""
from loguru import logger
from typing import Optional

from identity.alias_registry import AliasRegistry


class ResolutionError(Exception):
    """Raised when device resolution cannot produce a safe routing target."""
    pass


class ResolutionGuard:
    """
    Enforces strict routing discipline.

    Usage:
        guard = ResolutionGuard(alias_registry)

        # Returns device_id or raises ResolutionError
        device_id = guard.resolve_or_raise("Midas")

        # Returns (device_id, user_message) or (None, clarification_prompt)
        device_id, msg = guard.resolve_or_clarify("Midas")
    """

    def __init__(self, alias_registry: AliasRegistry):
        self._registry = alias_registry

    def resolve_or_raise(self, phrase: str, context: str = "") -> str:
        """
        Resolve phrase to device_id. Raise ResolutionError if not clean.
        Use this in code paths that must never proceed without a valid target.
        """
        result = self._registry.resolve_with_confidence(phrase)
        self._log_resolution(phrase, result)

        if result["status"] == "resolved":
            return result["device_id"]

        elif result["status"] == "ambiguous":
            names = [c["canonical_name"] for c in result["candidates"]]
            raise ResolutionError(
                f"Phrase {phrase!r} matched multiple devices: {names}. "
                f"Cannot route safely."
            )

        elif result["status"] in ("fuzzy", "not_found"):
            raise ResolutionError(
                f"Phrase {phrase!r} did not match any known device. "
                f"Known devices: {self._known_names()}"
            )

        raise ResolutionError(f"Unexpected resolution status: {result['status']}")

    def resolve_or_clarify(self, phrase: str) -> tuple[Optional[str], Optional[str]]:
        """
        Resolve phrase to device_id. Return a clarification message if needed.
        Returns (device_id, None) on success.
        Returns (None, clarification_message) on failure.

        Use this in conversational contexts where asking the user is appropriate.
        """
        result = self._registry.resolve_with_confidence(phrase)
        self._log_resolution(phrase, result)

        if result["status"] == "resolved":
            name = result["canonical"]
            return result["device_id"], None

        elif result["status"] == "ambiguous":
            names = " or ".join(
                f'"{c["canonical_name"]}"' for c in result["candidates"]
            )
            return None, (
                f'I found multiple devices matching "{phrase}": {names}. '
                f"Which one did you mean?"
            )

        elif result["status"] == "fuzzy":
            suggestions = ", ".join(
                f'"{c["canonical_name"]}"' for c in result["candidates"]
            )
            return None, (
                f'I don\'t have an exact match for "{phrase}". '
                f"Did you mean: {suggestions}?"
            )

        else:  # not_found
            known = self._known_names()
            return None, (
                f'I don\'t recognise "{phrase}" as a device in your mesh. '
                f"Known devices: {known}. "
                f"You can also send /pair from a device to add it."
            )

    def assert_not_default_substitution(
        self,
        requested_phrase: str,
        resolved_device_id: str,
    ):
        """
        Guardrail: if the user explicitly named a non-default device,
        assert we did NOT silently resolve to the default node.
        Logs a warning and raises if substitution is detected.
        """
        default_id = self._registry.get_default()
        if default_id is None:
            return  # no default set, nothing to check

        result = self._registry.resolve_with_confidence(requested_phrase)
        if result["status"] == "resolved":
            # User named something that resolves cleanly — check it's not default
            if resolved_device_id == default_id:
                requested_canonical = result.get("canonical", requested_phrase)
                if requested_canonical.lower() not in ("mac", "desk", "aide desk", "topd"):
                    logger.error(
                        f"ROUTING GUARD: user requested {requested_phrase!r} "
                        f"but resolved to default node {default_id}. ABORTING."
                    )
                    raise ResolutionError(
                        f"Routing guard blocked: {requested_phrase!r} resolved to "
                        f"default node. This indicates a resolution error."
                    )

    @staticmethod
    def _log_resolution(phrase: str, result: dict):
        if result["status"] == "resolved":
            logger.info(
                f"Identity resolution: {phrase!r} → "
                f"{result['device_id']} ({result['canonical']}) "
                f"[confidence: {result['confidence']}]"
            )
        else:
            logger.warning(
                f"Identity resolution failed: {phrase!r} → "
                f"status={result['status']}"
            )

    def _known_names(self) -> str:
        entries = self._registry.list_all()
        if not entries:
            return "none registered yet"
        return ", ".join(f'"{e["canonical_name"]}"' for e in entries)