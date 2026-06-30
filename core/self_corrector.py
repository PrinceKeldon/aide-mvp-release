import logging
from enum import Enum
from typing import Optional, Tuple, Any, Dict, List
from pydantic import BaseModel

logger = logging.getLogger("aide.self_corrector")


class FailureClass(Enum):
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
    TOOL_FAILED = "TOOL_FAILED"
    EMPTY_RESULT = "EMPTY_RESULT"
    LOOP_DETECTED = "LOOP_DETECTED"
    NONE = "NONE"


class RecoveryAction(BaseModel):
    should_replan: bool = False
    alternative_tool: Optional[str] = None
    suggested_input: Optional[str] = None
    failure_class: FailureClass = FailureClass.NONE
    reason: str = ""


class SelfCorrector:
    """
    Sprint 6: Self-Correction Engine.
    Classifies tool failures and determines recovery strategies to prevent AIDE from giving up.
    """

    ALT_TOOL_MAP = {
        "tavily_search": ["web_search", "read_email"],
        "web_search": ["tavily_search", "browse_url"],
        "read_email": ["search_email"],
        "browse_url": ["tavily_search"],
        "reply_email": ["send_email"],
    }

    REPLAN_SIGNAL = """
Previous approach failed: {failure_reason}
Steps completed so far: {completed_summary}
Original goal: {original_goal}
Replan: propose a different approach to reach the goal.
Do not repeat the failed approach.
"""

    def __init__(self):
        self.iteration_history = []  # Track (tool_name, tool_input) pairs

    def _detect_loop(self, tool_name: str, tool_input: str) -> bool:
        """Check if the same tool is called with the same input twice in a row."""
        if not self.iteration_history:
            return False

        last_call = self.iteration_history[-1]
        return last_call[0] == tool_name and last_call[1] == tool_input

    def evaluate_and_correct(
        self,
        tool_name: str,
        tool_input: str,
        observation: Any,
        available_tools: Dict[str, Any],
    ) -> Tuple[Any, bool]:
        """
        Evaluates the result of a tool call.
        Returns (processed_observation, was_corrected).
        """
        # 1. Loop Detection
        loop_detected = self._detect_loop(tool_name, tool_input)
        self.iteration_history.append((tool_name, tool_input))

        if loop_detected:
            logger.warning(f"Loop detected for tool {tool_name}. Triggering replan.")
            return (
                f"[SELF-CORRECTION] Loop detected: You called {tool_name} with the same input twice. This is a repetition. You must now use the previously obtained results to answer the user instead of calling the tool again.",
                True,
            )

        # 2. Tool Availability (usually caught earlier, but for safety)
        if tool_name not in available_tools:
            return self._handle_failure(
                FailureClass.TOOL_UNAVAILABLE, tool_name, tool_input, available_tools
            )

        # 3. Tool Failure (Exception caught by agent and passed as observation)
        if isinstance(observation, Exception) or (
            isinstance(observation, str) and "error" in observation.lower()
        ):
            return self._handle_failure(
                FailureClass.TOOL_FAILED, tool_name, tool_input, available_tools
            )

        # 4. Empty Result
        if (
            not observation
            or (isinstance(observation, str) and len(observation.strip()) < 20)
            or (isinstance(observation, str) and "no results" in observation.lower())
        ):
            return self._handle_failure(
                FailureClass.EMPTY_RESULT, tool_name, tool_input, available_tools
            )

        return observation, False

    def _handle_failure(
        self,
        failure_class: FailureClass,
        tool_name: str,
        tool_input: str,
        available_tools: Dict[str, Any],
    ) -> Tuple[Any, bool]:
        """Determine recovery strategy based on failure class."""
        logger.info(f"Handling {failure_class.value} for tool {tool_name}")

        alt_tools = self.ALT_TOOL_MAP.get(tool_name, [])
        best_alt = None
        for alt in alt_tools:
            if alt in available_tools:
                best_alt = alt
                break

        if best_alt:
            logger.info(f"Suggesting alternative tool: {best_alt}")
            return (
                f"[SELF-CORRECTION] {failure_class.value}: Try using {best_alt} instead.",
                True,
            )

        return (
            f"[SELF-CORRECTION] {failure_class.value}: No alternative tool available. Replanning required.",
            True,
        )

    async def handle_step_failure(
        self,
        step: Any,
        exception: Exception,
        available_tools: Dict[str, Any],
        task: Any,
    ) -> RecoveryAction:
        """
        Hook for task queue failures.
        Determines if a specific step failure requires a full task replan.
        """
        logger.error(f"Step failure detected: {exception}")

        # Check if there's an alternative tool for the step's tool
        tool_name = getattr(step, "tool", None)
        alt_tools = self.ALT_TOOL_MAP.get(tool_name, [])
        best_alt = next((alt for alt in alt_tools if alt in available_tools), None)

        if best_alt:
            return RecoveryAction(
                should_replan=False,
                alternative_tool=best_alt,
                failure_class=FailureClass.TOOL_FAILED,
                reason=str(exception),
            )

        return RecoveryAction(
            should_replan=True,
            failure_class=FailureClass.TOOL_FAILED,
            reason=str(exception),
        )

    def get_replan_prompt(
        self, failure_reason: str, completed_summary: str, original_goal: str
    ) -> str:
        """Generates the REPLAN_SIGNAL for injection into the LLM prompt."""
        return self.REPLAN_SIGNAL.format(
            failure_reason=failure_reason,
            completed_summary=completed_summary,
            original_goal=original_goal,
        )
