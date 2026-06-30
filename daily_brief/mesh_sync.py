"""
AIDE Daily Brief -- Mesh Synchronization
Routes brief content to devices based on trust scope.

Policy:
  OWNER_DEVICE with can_receive_brief=True  → full brief
  Sovereign terminal (can_receive_approvals=True only) → approval cards only
  SOVEREIGN_PEER → nothing by default
"""
from datetime import date
from loguru import logger

from mesh.trust import TrustGraph, MessageClass
from mesh.messages.daily_brief import (
    create_brief_share,
    create_approval_request,
)


class MeshDailyBriefSync:
    """
    Distributes the daily brief to the right devices.
    Called after Mac generates the brief.
    """

    def __init__(self, mesh_node, trust_graph: TrustGraph):
        self.mesh       = mesh_node
        self.trust      = trust_graph

    async def distribute_brief(self, brief_data: dict) -> dict:
        """
        Send brief to all permitted devices.
        Returns summary of what was sent where.
        """
        results = {"brief_sent_to": [], "approvals_sent_to": [], "blocked": []}

        # Separate brief items from approval items
        items      = brief_data.get("items", [])
        brief_items    = [i for i in items if i.get("type") != "approval_request"]
        approval_items = [i for i in items if i.get("type") == "approval_request"]

        # Full brief → owner devices with can_receive_brief
        for peer in self.trust.get_brief_recipients():
            allowed = self.trust.is_message_allowed(
                peer["peer_id"], MessageClass.BRIEF
            )
            if allowed:
                try:
                    msg = create_brief_share(
                        from_device=self.mesh.device_id,
                        to_device=peer["peer_id"],
                        brief_data={**brief_data, "items": brief_items},
                    )
                    await self.mesh.send(peer["peer_id"], msg)
                    results["brief_sent_to"].append(peer["device_name"])
                    logger.info(f"Brief sent to {peer['device_name']}")
                except Exception as e:
                    logger.warning(f"Brief delivery failed to {peer['device_name']}: {e}")
            else:
                results["blocked"].append(peer["device_name"])

        # Approval cards only → terminals with can_receive_approvals
        for item in approval_items:
            for peer in self.trust.get_approval_terminals():
                allowed = self.trust.is_message_allowed(
                    peer["peer_id"], MessageClass.APPROVAL
                )
                if allowed:
                    try:
                        msg = create_approval_request(
                            from_device=self.mesh.device_id,
                            to_device=peer["peer_id"],
                            approval_item=item,
                        )
                        await self.mesh.send(peer["peer_id"], msg)
                        results["approvals_sent_to"].append(peer["device_name"])
                        logger.info(f"Approval request sent to {peer['device_name']}")
                    except Exception as e:
                        logger.warning(f"Approval delivery failed to {peer['device_name']}: {e}")

        logger.info(
            f"Brief distributed — "
            f"full: {results['brief_sent_to']}, "
            f"approvals: {results['approvals_sent_to']}, "
            f"blocked: {results['blocked']}"
        )
        return results

    async def handle_incoming(self, message: dict):
        """
        Handle incoming mesh messages related to daily brief.
        Called from mesh_node message handler.
        """
        msg_type  = message.get("type")
        from_peer = message.get("from")

        if msg_type == "DAILY_BRIEF_REQUEST":
            await self._handle_brief_request(from_peer, message)
        elif msg_type == "DAILY_BRIEF_ACTION":
            await self._handle_action_sync(from_peer, message)
        elif msg_type == "APPROVAL_RESPONSE":
            await self._handle_approval_response(from_peer, message)

    async def _handle_brief_request(self, from_peer: str, message: dict):
        """Android requested today's brief from Mac."""
        if not self.trust.is_message_allowed(from_peer, MessageClass.BRIEF):
            logger.warning(f"Brief request blocked from {from_peer}")
            return

        from daily_brief.storage import load_daily_brief
        target_date = message.get("payload", {}).get("requested_date", str(date.today()))

        try:
            brief = load_daily_brief(target_date)
            if brief:
                msg = create_brief_share(
                    from_device=self.mesh.device_id,
                    to_device=from_peer,
                    brief_data=brief,
                )
                await self.mesh.send(from_peer, msg)
                logger.info(f"Brief sent on request to {from_peer}")
        except Exception as e:
            logger.error(f"Brief request handler failed: {e}")

    async def _handle_action_sync(self, from_peer: str, message: dict):
        """Another device took action on a brief item — sync locally."""
        if not self.trust.is_message_allowed(from_peer, MessageClass.TASK):
            return

        payload = message.get("payload", {})
        item_id = payload.get("item_id")
        action  = payload.get("action")
        logger.info(f"Action sync from {from_peer}: {action} on {item_id}")
        # Update local brief storage to mark item as acted on
        # Implementation depends on daily_brief.storage API

    async def _handle_approval_response(self, from_peer: str, message: dict):
        """Terminal or device responded to approval request."""
        if not self.trust.is_message_allowed(from_peer, MessageClass.APPROVAL):
            return

        payload     = message.get("payload", {})
        approval_id = payload.get("approval_id")
        decision    = payload.get("decision")
        logger.info(f"Approval response from {from_peer}: {decision} for {approval_id}")
        # Route to safety gate for execution
