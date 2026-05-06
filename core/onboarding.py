"""
AIDE — onboarding
Three plain-language questions. No jargon. No settings pages.
The answers drive everything: memory, safety tiers, persona.
"""
from enum import Enum
from loguru import logger
from memory.manager import MemoryManager


class OnboardingState(str, Enum):
    NOT_STARTED   = "not_started"
    AWAITING_NAME = "awaiting_name"
    AWAITING_LANG = "awaiting_lang"
    AWAITING_TIER = "awaiting_tier"
    COMPLETE      = "complete"


TIER_OPTIONS = {
    "1": {
        "label": "Handle it silently",
        "description": (
            "AIDE handles routine tasks and tells you about them afterwards. "
            "Good for: reminders, web searches, drafting messages."
        ),
        "tier": "notify",
    },
    "2": {
        "label": "Always ask me first",
        "description": (
            "AIDE always checks before doing anything. "
            "You stay in full control of every action."
        ),
        "tier": "approve",
    },
    "3": {
        "label": "Full autonomy",
        "description": (
            "AIDE handles everything it can and only interrupts for things "
            "that are irreversible or involve money."
        ),
        "tier": "autonomous",
    },
}


class Onboarding:
    """
    Conversational onboarding — runs inside the Telegram interface.
    Each incoming message advances the state machine.
    """

    def __init__(self, memory: MemoryManager) -> None:
        self._memory = memory

    def is_complete(self) -> bool:
        return self._memory.get_fact("onboarding_complete") == "true"

    def get_state(self) -> OnboardingState:
        return OnboardingState(
            self._memory.get_fact("onboarding_state") or OnboardingState.NOT_STARTED
        )

    def _set_state(self, state: OnboardingState) -> None:
        self._memory.store_fact("onboarding_state", state.value)

    async def start(self) -> str:
        self._set_state(OnboardingState.AWAITING_NAME)
        return (
            "Hi! I'm AIDE — your personal agent, running entirely on this device.\n\n"
            "I just need to ask you three quick things.\n\n"
            "First: what's your name?"
        )

    async def handle(self, message: str) -> tuple[str, bool]:
        state = self.get_state()
        message = message.strip()

        if state == OnboardingState.AWAITING_NAME:
            return await self._handle_name(message)
        elif state == OnboardingState.AWAITING_LANG:
            return await self._handle_language(message)
        elif state == OnboardingState.AWAITING_TIER:
            return await self._handle_tier(message)

        return ("Something went wrong. Send /start to try again.", False)

    async def _handle_name(self, name: str) -> tuple[str, bool]:
        if len(name) < 1 or len(name) > 60:
            return ("That doesn't look right — what's your name?", False)

        self._memory.store_fact("user_name", name)
        self._set_state(OnboardingState.AWAITING_LANG)
        logger.info(f"Onboarding: name set to {name!r}")

        return (
            f"Great to meet you, {name}.\n\n"
            "What language would you like me to use?\n"
            "(Just type it — English, Español, Deutsch, Français, or any other.)",
            False,
        )

    async def _handle_language(self, lang: str) -> tuple[str, bool]:
        self._memory.store_fact("user_language", lang)
        self._set_state(OnboardingState.AWAITING_TIER)
        logger.info(f"Onboarding: language set to {lang!r}")

        tier_text = "\n".join(
            f"{k}. {v['label']} — {v['description']}"
            for k, v in TIER_OPTIONS.items()
        )
        return (
            f"Perfect. Last question — and it's the important one.\n\n"
            f"What can I do without checking with you first?\n\n"
            f"{tier_text}\n\n"
            f"Reply with 1, 2, or 3.",
            False,
        )

    async def _handle_tier(self, choice: str) -> tuple[str, bool]:
        if choice not in TIER_OPTIONS:
            return ("Please reply with 1, 2, or 3.", False)

        option = TIER_OPTIONS[choice]
        tier = option["tier"]
        self._memory.store_fact("default_safety_tier", tier)
        self._memory.store_fact("onboarding_complete", "true")
        self._set_state(OnboardingState.COMPLETE)

        name = self._memory.get_fact("user_name") or "there"
        logger.info(f"Onboarding complete for {name!r}, tier={tier!r}")

        return (
            f"All set, {name}.\n\n"
            f"I'm ready. Everything runs on this device — "
            f"your conversations are private and never leave here.\n\n"
            f"Just talk to me. I'll handle the rest.\n\n"
            f"(You can change any of this later — just ask me.)",
            True,
        )