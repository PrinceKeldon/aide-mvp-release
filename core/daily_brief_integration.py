"""
Live runtime integration for the Your Day daily brief.
"""
from __future__ import annotations

from datetime import date
from loguru import logger

from daily_brief.formatter import format_for_telegram
from daily_brief.generator import DailyBriefGenerator
from daily_brief.schema import DailyBrief
from daily_brief.storage import (
    load_daily_brief,
    save_daily_brief,
    sync_daily_brief_approval_state,
    upsert_daily_brief_approval_item,
)


class AIDEDailyBriefService:
    """
    Generate the brief once, persist it once, then distribute it through
    the canonical owner-mesh routing path.
    """

    def __init__(
        self,
        llm,
        registry,
        router,
        local_device_id: str | None = None,
        fallback_notify=None,
    ) -> None:
        self._registry = registry
        self._router = router
        self._local_device_id = local_device_id
        self._fallback_notify = fallback_notify
        self._generator = DailyBriefGenerator(llm)

    async def generate_and_deliver(self, target_date: date | None = None) -> DailyBrief:
        brief = await self._generator.generate(target_date=target_date)
        save_daily_brief(brief)
        await self.deliver_brief(brief)
        return brief

    async def generate_and_share(self, target_date: date | None = None) -> DailyBrief:
        return await self.generate_and_deliver(target_date=target_date)

    async def reshare_saved_brief(self) -> None:
        await self._reshare_current_brief()

    async def deliver_brief(self, brief: DailyBrief) -> dict:
        payload = brief.to_dict()
        result = await self._deliver_brief_payload(payload)
        delivered = result["delivered"]
        failed = result["failed"]

        fallback_used = False
        if not delivered and self._fallback_notify:
            fallback_used = True
            await self._fallback_notify(format_for_telegram(brief))
            logger.info("Your Day brief delivered via fallback owner notify path")

        logger.info(
            "Your Day delivery complete: delivered={} failed={} fallback={}",
            delivered,
            failed,
            fallback_used,
        )
        return {
            "delivered": delivered,
            "failed": failed,
            "fallback_used": fallback_used,
        }

    def _eligible_devices(self) -> list[dict]:
        devices = self._registry.list_active_devices("can_receive_brief")
        unique = {}

        for device in devices:
            device_id = device.get("device_id")
            if not device_id or device_id == self._local_device_id:
                continue
            unique.setdefault(device_id, device)

        return list(unique.values())

    async def sync_approval_status(
        self,
        *,
        action_id: str,
        status: str,
        description: str,
        tool_name: str = "",
        created_at: str = "",
        expires_at: str = "",
        resolved_by: str | None = None,
    ) -> None:
        if status == "pending":
            changed = upsert_daily_brief_approval_item(
                action_id=action_id,
                description=description,
                tool_name=tool_name,
                created_at=created_at,
                expires_at=expires_at,
            )
        else:
            changed = sync_daily_brief_approval_state(
                action_id,
                status=status,
                resolved_by=resolved_by,
            )

        if not changed:
            return

        await self._broadcast_approval_sync(
            action_id=action_id,
            status=status,
            description=description,
            resolved_by=resolved_by,
        )

    async def _broadcast_approval_sync(
        self,
        *,
        action_id: str,
        status: str,
        description: str,
        resolved_by: str | None = None,
    ) -> None:
        for device in self._eligible_devices():
            await self._router.send(
                to_device_id=device["device_id"],
                message_type="EXECUTION_STATE",
                payload={
                    "state": "completed" if status in {"approved", "denied", "expired"} else "queued",
                    "current_step": self._approval_sync_text(
                        action_id=action_id,
                        status=status,
                        description=description,
                        resolved_by=resolved_by,
                    ),
                },
                from_device_id=self._local_device_id or "mac_primary",
            )

    def _approval_sync_text(
        self,
        *,
        action_id: str,
        status: str,
        description: str,
        resolved_by: str | None = None,
    ) -> str:
        if status == "pending":
            return f"Approval pending: {description} ({action_id})"
        if status == "approved":
            suffix = f" by {resolved_by}" if resolved_by else ""
            return f"Approval approved{suffix}: {description}"
        if status == "denied":
            suffix = f" by {resolved_by}" if resolved_by else ""
            return f"Approval denied{suffix}: {description}"
        if status == "expired":
            return f"Approval expired: {description}"
        return f"Approval update: {description}"

    async def _reshare_current_brief(self) -> None:
        brief_payload = load_daily_brief()
        if not brief_payload:
            return
        await self._deliver_brief_payload(brief_payload)

    async def _deliver_brief_payload(self, payload: dict) -> dict:
        recipients = self._eligible_devices()
        delivered = []
        failed = []

        for device in recipients:
            result = await self._router.send_with_response(
                to_device_id=device["device_id"],
                message_type="BRIEF_SHARE",
                payload=payload,
                from_device_id=self._local_device_id or "mac_primary",
            )
            if result:
                delivered.append(device["device_name"])
            else:
                failed.append(device["device_name"])

        return {
            "delivered": delivered,
            "failed": failed,
        }
