import pytest

from mesh.device_registry import DeviceRegistry
from mesh.message_router import MessageRouter


class RichTelegram:
    def __init__(self):
        self.messages = []
        self.keyboard_messages = []

    async def send_message(self, chat_id, text, parse_mode="Markdown"):
        self.messages.append(
            {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        )

    async def send_message_with_keyboard(self, text, keyboard, *, chat_id=None, parse_mode="Markdown"):
        self.keyboard_messages.append(
            {
                "chat_id": chat_id,
                "text": text,
                "keyboard": keyboard,
                "parse_mode": parse_mode,
            }
        )


class MeshRecorder:
    def __init__(self):
        self.calls = []

    async def send_intent(self, peer, message_type, parameters):
        self.calls.append(
            {
                "peer": peer,
                "message_type": message_type,
                "parameters": parameters,
            }
        )
        return {"status": "completed", "response": "mesh reply"}


@pytest.mark.asyncio
async def test_brief_share_sends_rich_approval_cards_to_telegram_devices(tmp_path):
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    device_id = registry.create_device("Owner Phone", device_id="owner_phone")
    registry.bind_telegram(device_id, 111)

    telegram = RichTelegram()
    router = MessageRouter(registry=registry, telegram_bot=telegram)

    ok = await router.send_with_response(
        to_device_id=device_id,
        message_type="BRIEF_SHARE",
        payload={
            "summary": {"headline": "Your day is prepared.", "counts": {"approval_requests": 1}},
            "items": [
                {
                    "type": "approval_request",
                    "title": "Approval: Send email to client",
                    "content": {
                        "approval_id": "approve123",
                        "action_description": "Send email to client",
                        "action_type": "send_email",
                        "consequence": "This action is paused until an approved owner device confirms it.",
                        "expires_at": "2026-04-02T07:30:00+00:00",
                    },
                }
            ],
        },
        from_device_id="local_mac",
    )

    assert ok == {"status": "sent"}
    assert len(telegram.messages) == 1
    assert len(telegram.keyboard_messages) == 1
    assert telegram.keyboard_messages[0]["chat_id"] == 111
    assert "Approval Needed" in telegram.keyboard_messages[0]["text"]
    assert telegram.keyboard_messages[0]["keyboard"][0][0]["callback_data"] == "approve:approve123"


@pytest.mark.asyncio
async def test_peer_task_request_is_blocked_for_telegram_terminal(tmp_path):
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    device_id = registry.create_device("Owner Phone", device_id="owner_phone")
    registry.mark_paired(device_id)
    registry.update_capabilities(device_id, can_execute=0)
    registry.bind_telegram(device_id, 111)

    telegram = RichTelegram()
    router = MessageRouter(registry=registry, telegram_bot=telegram)

    ok = await router.send_with_response(
        to_device_id=device_id,
        message_type="PEER_TASK_REQUEST",
        payload={"request_id": "peer_req_1", "title": "Research this"},
        from_device_id="local_mac",
    )

    assert ok is False
    assert telegram.messages == []
    assert telegram.keyboard_messages == []


@pytest.mark.asyncio
async def test_ping_prefers_telegram_for_proxied_terminal_even_with_mesh_binding(tmp_path):
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    device_id = registry.create_device("Midas", device_id="midas")
    registry.bind_telegram(device_id, 111)
    registry.bind_mesh(device_id, "10.0.0.8", 7432)

    telegram = RichTelegram()
    mesh_node = MeshRecorder()
    router = MessageRouter(registry=registry, telegram_bot=telegram, mesh_node=mesh_node)

    result = await router.send_with_response(
        to_device_id=device_id,
        message_type="PING",
        payload={},
        from_device_id="local_mac",
    )

    assert result == {"status": "sent"}
    assert len(telegram.messages) == 1
    assert mesh_node.calls == []


@pytest.mark.asyncio
async def test_ask_peer_prefers_mesh_when_device_has_mesh_and_telegram(tmp_path):
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    device_id = registry.create_device("Owner Phone", device_type="full_node", device_id="owner_phone")
    registry.mark_paired(device_id)
    registry.bind_telegram(device_id, 111)
    registry.bind_mesh(device_id, "10.0.0.8", 7432)

    telegram = RichTelegram()
    mesh_node = MeshRecorder()
    router = MessageRouter(registry=registry, telegram_bot=telegram, mesh_node=mesh_node)

    result = await router.send_with_response(
        to_device_id=device_id,
        message_type="ASK_PEER",
        payload={"prompt": "What do you remember?"},
        from_device_id="local_mac",
    )

    assert result == {"status": "completed", "response": "mesh reply"}
    assert telegram.messages == []
    assert telegram.keyboard_messages == []
    assert mesh_node.calls[0]["message_type"] == "ASK_PEER"
    assert mesh_node.calls[0]["peer"] == {
        "agent_id": device_id,
        "address": "10.0.0.8",
        "port": 7432,
    }


@pytest.mark.asyncio
async def test_ask_peer_can_mirror_to_telegram_when_requested(tmp_path):
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    device_id = registry.create_device("Owner Phone", device_type="full_node", device_id="owner_phone")
    registry.mark_paired(device_id)
    registry.bind_telegram(device_id, 111)
    registry.bind_mesh(device_id, "10.0.0.8", 7432)

    telegram = RichTelegram()
    mesh_node = MeshRecorder()
    router = MessageRouter(registry=registry, telegram_bot=telegram, mesh_node=mesh_node)

    result = await router.send_with_response(
        to_device_id=device_id,
        message_type="ASK_PEER",
        payload={"prompt": "What do you remember?", "mirror_to_telegram": True},
        from_device_id="local_mac",
    )

    assert result == {"status": "completed", "response": "mesh reply"}
    assert len(telegram.messages) == 1
    assert "Peer thread mirror" in telegram.messages[0]["text"]
    assert "You asked: What do you remember?" in telegram.messages[0]["text"]
    assert "Owner Phone replied: mesh reply" in telegram.messages[0]["text"]
