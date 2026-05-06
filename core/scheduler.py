"""
AIDE -- Proactive scheduler (Sprint 2 Option C)
AIDE acts without being asked.

Scheduled triggers:
  morning_brief   -- daily at 07:00, weather + news + reminders
  evening_summary -- daily at 19:00, day recap
  topic_monitor   -- checks user-defined topics for updates
  reminder_check  -- surfaces things mentioned in conversation

All times respect the user's timezone stored in memory.
Notifications go through Telegram.
"""

import asyncio
from datetime import datetime
from loguru import logger
from typing import Any
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from core.settings import settings
from memory.manager import MemoryManager
from core.voice import NativeVoiceListener


class ProactiveScheduler:
    def __init__(self, memory: MemoryManager, project_manager=None, agent=None):
        self._agent = agent
        self._memory = memory
        self._project_manager = project_manager

        # Use MemoryJobStore (in-memory) instead of SQLAlchemyJobStore (persistent)
        # to avoid pickle errors with complex agent objects.
        self._scheduler = AsyncIOScheduler()

        self._notify = None
        self._telegram_interface = None
        self._daily_brief_service = None

        # Initialize Native Voice Listener
        self._voice = NativeVoiceListener(
            on_transcript_callback=self._handle_voice_transcript
        )
        self._background_tasks: list[asyncio.Task] = []
        self._setup_jobs()

    def attach(
        self,
        agent=None,
        notify_callback=None,
        telegram_interface=None,
        daily_brief_service=None,
    ) -> None:
        if agent is not None:
            self._agent = agent
            # Ensure the agent has a reference back to the scheduler for API access
            setattr(agent, "_scheduler", self)
        if notify_callback is not None:
            self._notify = notify_callback
        if telegram_interface is not None:
            self._telegram_interface = telegram_interface
        if daily_brief_service is not None:
            self._daily_brief_service = daily_brief_service

    def _setup_jobs(self) -> None:
        # We use lambda or wrapper functions for jobs that require self._agent
        # so that the job store only stores the function reference and not the
        # full object state which might contain unpickleable references.

        self._scheduler.add_job(
            self._run_project_advance,
            CronTrigger(hour="*/6"),
            id="project_advance",
            replace_existing=True,
            misfire_grace_time=600,
        )
        # ... (rest of the jobs)

        self._scheduler.add_job(
            self._morning_brief,
            CronTrigger(hour=7, minute=0),
            id="morning_brief",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        self._scheduler.add_job(
            self._evening_summary,
            CronTrigger(hour=19, minute=0),
            id="evening_summary",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        self._scheduler.add_job(
            self._reminder_check,
            CronTrigger(minute="*/30"),
            id="reminder_check",
            replace_existing=True,
            misfire_grace_time=300,
        )

        self._scheduler.add_job(
            self._topic_monitor,
            CronTrigger(hour="*/4"),
            id="topic_monitor",
            replace_existing=True,
            misfire_grace_time=600,
        )

        self._scheduler.add_job(
            self._sync_obsidian,
            "interval",
            seconds=settings.obsidian_sync_interval_seconds,
            id="obsidian_sync",
            replace_existing=True,
        )

        logger.info("Proactive scheduler configured")

    async def start(self):
        """Start scheduler and the native voice listener."""
        self._scheduler.start()

        # Start voice listening in the background
        self._background_tasks.append(asyncio.create_task(self._voice.run_loop()))
        self._background_tasks.append(
            asyncio.create_task(self._voice.start_listening())
        )
        self._background_tasks.append(asyncio.create_task(self._check_missed_jobs()))

        logger.info("Scheduler and Native Voice Listener started")

    async def stop(self) -> None:
        """Shut down scheduler and cancel all background tasks."""
        self._scheduler.shutdown()
        for task in self._background_tasks:
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()
        logger.info("Proactive scheduler stopped")

    async def _run_project_advance(self) -> None:
        if self._agent is None:
            logger.debug("Skipping project advance — agent not attached")
            return
        if self._telegram_interface is None:
            logger.debug("Skipping project advance — Telegram interface not attached")
            return

        from core.project_advancer import ProjectAdvancer

        advancer = ProjectAdvancer(
            project_manager=self._project_manager,
            agent=self._agent,
            telegram_interface=self._telegram_interface,
        )
        await advancer.run_advance_cycle()

    async def _check_missed_jobs(self) -> None:
        logger.debug("Missed job check started; waiting 10 seconds")
        from datetime import datetime

        await asyncio.sleep(10)
        logger.debug("Missed job check running now")

        now = datetime.now()
        last_brief = self._memory.get_fact("last_morning_brief")
        today = now.strftime("%Y-%m-%d")
        logger.debug(f"Hour: {now.hour}, last_brief: {last_brief}, today: {today}")

        if now.hour >= 7 and last_brief != today:
            logger.info("Morning brief was missed — running now")
            await self._morning_brief()

    async def _handle_system_state_change(self, key: str, value: Any):
        """Handle a system state change and proactively trigger actions."""
        logger.debug(f"Scheduler handling state change: {key}={value}")

        if key == "battery_level" and value < 20:
            await self._notify(
                "Battery is low (under 20%). I'll minimize background tasks to save power."
            )
        elif key == "connectivity" and value == "none":
            await self._notify(
                "I've lost internet connectivity. Some features are now limited."
            )
        elif key == "memory_pressure" and value == "high":
            logger.warning("High memory pressure detected on device.")
            if self._agent and self._agent._task_queue:
                # Proactively initiate a complex task to optimize the system
                await self.trigger_complex_task(
                    "Optimize system memory and close unused heavy processes"
                )

    async def trigger_complex_task(self, request: str):
        """Triggers a complex task via the TaskQueue."""
        if not self._agent or not self._agent._task_queue:
            logger.error("Agent or TaskQueue not attached; cannot trigger complex task")
            return

        logger.info(f"Proactively triggering complex task: {request}")
        task = await self._agent._task_queue.plan(request)
        asyncio.create_task(
            self._agent._task_queue.execute(task, notify_callback=self._notify)
        )

    async def _morning_brief(self) -> None:
        logger.info("Running morning brief")

        from datetime import datetime

        self._memory.store_fact(
            "last_morning_brief", datetime.now().strftime("%Y-%m-%d")
        )

        try:
            if self._daily_brief_service is not None:
                await self._daily_brief_service.generate_and_deliver()
                logger.info("Morning brief delivered through Your Day service")
                return

            await self._legacy_morning_brief()

        except Exception as e:
            logger.error(f"Morning brief failed: {e}")
            # Fallback to simple brief
            name = self._memory.get_fact("user_name") or "there"
            if self._notify:
                await self._notify(f"Good morning, {name}. AIDE is ready.")

    async def _legacy_morning_brief(self) -> None:
        from daily_brief.formatter import format_for_telegram
        from daily_brief.generator import DailyBriefGenerator
        from daily_brief.storage import save_daily_brief

        generator = DailyBriefGenerator(self._agent._llm)
        brief = await generator.generate()
        save_daily_brief(brief)

        message = format_for_telegram(brief)
        if message and self._notify:
            await self._notify(message)
            logger.info("Morning brief sent")

    async def _evening_summary(self) -> None:
        logger.info("Running evening summary")
        name = self._memory.get_fact("user_name") or "there"

        try:
            # Load today's conversations
            recent = self._memory.load_recent_conversations(n=20)
            today = datetime.utcnow().date().isoformat()
            todays = [c for c in recent if c["timestamp"].startswith(today)]

            if not todays:
                return  # Nothing to summarise today

            convo_text = "\n".join(
                f"You: {c['user'][:80]}\nVERA: {c['assistant'][:80]}"
                for c in todays[-10:]
            )

            from core.router import TaskType

            result = await self._agent._llm.chat(
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Summarise this day's conversations in 3-4 sentences. "
                            f"Note any tasks completed, decisions made, or things to follow up on. "
                            f"Plain text only.\n\n{convo_text}"
                        ),
                    }
                ],
                tools=None,
                temperature=0.3,
                task_type=TaskType.REASONING,
            )

            summary = result.get("content", "").strip()
            if summary:
                await self._notify(f"Evening summary, {name}:\n\n{summary}")
                logger.info("Evening summary sent")

        except Exception as e:
            logger.error(f"Evening summary failed: {e}")

    async def _reminder_check(self) -> None:
        """Surface reminders that are due."""
        try:
            reminders_raw = self._memory.get_fact("reminders")
            if not reminders_raw:
                return

            import json

            reminders = json.loads(reminders_raw)
            now = datetime.utcnow()
            due = []
            remaining = []

            for r in reminders:
                due_time = datetime.fromisoformat(r.get("due", ""))
                if due_time <= now:
                    due.append(r)
                else:
                    remaining.append(r)

            for r in due:
                await self._notify(f"Reminder: {r['text']}")
                logger.info(f"Reminder fired: {r['text']}")

            if due:
                # Save remaining reminders
                self._memory.store_fact("reminders", json.dumps(remaining))

        except Exception as e:
            logger.debug(f"Reminder check: {e}")

    async def _topic_monitor(self) -> None:
        """Check user-defined topics for new developments."""
        try:
            topics_raw = self._memory.get_fact("monitor_topics")
            if not topics_raw:
                return

            import json

            topics = json.loads(topics_raw)
            if not topics:
                return

            logger.info(f"Monitoring {len(topics)} topics")

            for topic in topics[:3]:  # Max 3 per check
                try:
                    from tools.tavily_search import TavilySearchTool

                    tool = TavilySearchTool()
                    results = await tool.execute(f"{topic} latest news today")

                    if "No results" not in results and results:
                        from core.router import TaskType

                        summary = await self._agent._llm.chat(
                            messages=[
                                {
                                    "role": "user",
                                    "content": (
                                        f"Is there anything significantly new or important "
                                        f"about '{topic}' in these results? "
                                        f"If yes, summarise in 2 sentences. "
                                        f"If nothing new, just say 'nothing new'.\n\n{results}"
                                    ),
                                }
                            ],
                            tools=None,
                            temperature=0.1,
                            task_type=TaskType.RESEARCH,
                        )
                        content = summary.get("content", "").lower()
                        if "nothing new" not in content and content:
                            await self._notify(
                                f"Update on '{topic}':\n{summary.get('content', '')}"
                            )
                            logger.info(f"Topic alert sent: {topic}")

                    await asyncio.sleep(2)  # Rate limit between searches

                except Exception as e:
                    logger.warning(f"Topic monitor failed for {topic}: {e}")

        except Exception as e:
            logger.debug(f"Topic monitor: {e}")

    async def _sync_obsidian(self) -> None:
        """Trigger scheduled bidirectional sync with Obsidian."""
        logger.debug("Running scheduled Obsidian sync...")
        try:
            await self._memory.sync_with_obsidian()
        except Exception as e:
            logger.error(f"Scheduled Obsidian sync failed: {e}")

    async def _handle_voice_transcript(self, text: str) -> None:
        """Handle transcription results from the Native Voice Listener."""
        logger.info(f"Voice transcript received: {text}")

        # Store the transcript so the chat UI can pick it up and send it as a message
        self._memory.store_fact("last_voice_chat_input", text)

        # We no longer call self._agent.run(prompt) here to avoid duplicate
        # responses when the frontend also sends the message.

    # ── User-facing controls ─────────────────────────────────────

    def set_morning_brief_time(self, hour: int, minute: int = 0) -> None:
        """Change morning brief time. Called from conversation."""
        self._scheduler.reschedule_job(
            "morning_brief",
            trigger=CronTrigger(hour=hour, minute=minute),
        )
        self._memory.store_fact("morning_brief_time", f"{hour:02d}:{minute:02d}")
        logger.info(f"Morning brief rescheduled to {hour:02d}:{minute:02d}")

    def add_reminder(self, text: str, due_iso: str) -> None:
        """Add a reminder. Called from agent when user says 'remind me'."""
        import json

        existing_raw = self._memory.get_fact("reminders") or "[]"
        try:
            reminders = json.loads(existing_raw)
        except Exception:
            reminders = []
        reminders.append({"text": text, "due": due_iso})
        self._memory.store_fact("reminders", json.dumps(reminders))
        logger.info(f"Reminder added: {text} at {due_iso}")

    def add_monitor_topic(self, topic: str) -> None:
        """Add a topic to monitor. Called from agent."""
        import json

        existing_raw = self._memory.get_fact("monitor_topics") or "[]"
        try:
            topics = json.loads(existing_raw)
        except Exception:
            topics = []
        if topic not in topics:
            topics.append(topic)
            self._memory.store_fact("monitor_topics", json.dumps(topics))
            logger.info(f"Topic added to monitor: {topic}")
