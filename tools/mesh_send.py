"""
AIDE — mesh send tool
Allows the agent to coordinate with a trusted peer agent.
No raw personal data — structured intents only.
"""
import json
from tools.base import BaseTool, SafetyTier


class MeshSendTool(BaseTool):
    """
    Send a structured intent to a trusted peer agent.
    Input format: JSON string with peer_id, intent_type, parameters.
    """

    def __init__(self, mesh_node) -> None:
        self._mesh = mesh_node

    @property
    def name(self) -> str:
        return "mesh_send"

    @property
    def description(self) -> str:
        return (
            "Send a coordination request to a trusted peer agent. "
            "Use for tasks that require another agent's help — booking, "
            "scheduling conflicts, sharing non-personal structured data. "
            "Input: JSON with peer_id, intent_type, parameters (NO personal data)."
        )

    @property
    def safety_tier(self) -> SafetyTier:
        return SafetyTier.NOTIFY

    async def execute(self, input_text: str) -> str:
        try:
            data = json.loads(input_text)
        except json.JSONDecodeError:
            return "Error: input must be valid JSON."

        peer_id     = data.get("peer_id", "")
        intent_type = data.get("intent_type", "")
        parameters  = data.get("parameters", {})

        if not all([peer_id, intent_type]):
            return "Error: peer_id and intent_type are required."

        peers = (
            self._mesh._discovery.get_peers()
            if hasattr(self._mesh, "_discovery")
            else []
        )
        peer = next((p for p in peers if p["agent_id"] == peer_id), None)
        if not peer:
            return f"Peer {peer_id!r} not found on local network."

        result = await self._mesh.send_intent(peer, intent_type, parameters)
        if result is None:
            return "Peer coordination failed — they may be offline."
        return f"Peer result: {json.dumps(result)}"