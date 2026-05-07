"""
AIDE -- main entry point (v0.1)
"""

import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["CHROMA_TELEMETRY"] = "False"

import asyncio
import atexit
import signal
import sys
from pathlib import Path
from loguru import logger

from core.settings import settings
from core.llm import LLMClient
from core.agent import AideAgent
from core.safety import SafetyGate
from core.onboarding import Onboarding
from core.task_queue import TaskQueue
from core.scheduler import ProactiveScheduler
from core.monitor import DeviceMonitor
from core.offline import OfflineManager
from core.project_manager import ProjectManager
from core.daily_brief_integration import AIDEDailyBriefService
from memory.manager import MemoryManager
from tools.web_search import WebSearchTool
from tools.tavily_search import TavilySearchTool
from tools.mesh_send import MeshSendTool
from tools.reminder import ReminderTool, MonitorTopicTool
from tools.browser import BrowserTool, PageClickTool, PriceMonitorTool
from tools.project import ProjectTool
from interface.telegram_bot import TelegramInterface
from interface.tray import SystemTray
from interface.web import app as web_app, set_runtime
from socketio import AsyncServer
from web.mesh_integration import create_mesh_app, update_global_state, log_event
from mesh.node import MeshNode, MeshDiscovery, resolve_mesh_ipv4_addresses
from mesh.device_registry import DeviceRegistry
from identity.alias_registry import AliasRegistry
from identity.resolution_guard import ResolutionGuard
from mesh.message_router import MessageRouter
from mesh.orchestrator import OwnerMeshOrchestrator
from mesh.task_engine import OwnerMeshTaskEngine
from bridges.telegram_bridge import build_bridge
from tools.email_tool import (
    ReadEmailTool,
    SendEmailTool,
    SearchEmailTool,
    ReplyEmailTool,
)
import uvicorn
from identity.device_manifest import DeviceManifest
from identity.interceptor import ExplicitTargetInterceptor
from identity.bootstrap import ensure_local_device_registered
from tools.device_router_tool import DeviceRouterTool, ListDevicesTool
from tools.google_calendar_tool import GoogleCalendarTool
from tools.owner_mesh import DelegateMeshTaskTool
from tools.finance_ingest import IngestBankStatementTool
from tools.finance_budget import (
    CategoriseTransactionTool,
    CreateFinanceGoalTool,
    SetBudgetTargetTool,
    SetupFinanceBudgetTool,
)
from tools.finance_report import GetMonthlyOverviewTool, GetProjectFinanceTool
from core.mvp_config import agent_name, load_module_settings


INSTANCE_LOCK_PATH = Path("./data/aide.lock")


def acquire_instance_lock() -> None:
    INSTANCE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if INSTANCE_LOCK_PATH.exists():
        try:
            pid = int(INSTANCE_LOCK_PATH.read_text(encoding="utf-8").strip())
            os.kill(pid, 0)
            raise RuntimeError(
                f"AIDE is already running. Open http://localhost:{settings.web_port} or use the tray icon."
            )
        except ProcessLookupError:
            pass
        except ValueError:
            pass
    INSTANCE_LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(release_instance_lock)


def release_instance_lock() -> None:
    try:
        if INSTANCE_LOCK_PATH.read_text(encoding="utf-8").strip() == str(os.getpid()):
            INSTANCE_LOCK_PATH.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        logger.debug("Could not release AIDE instance lock")


def configure_logging() -> None:
    logger.remove()

    def console_filter(record):
        # Filter out operational noise from the console
        if record["extra"].get("operational") and record["level"].name not in ("ERROR", "CRITICAL"):
            return False
        return True

    logger.add(
        sys.stderr,
        level=settings.log_level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        filter=console_filter,
    )
    logger.add(
        settings.log_file,
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        compression="zip",
    )


async def bootstrap_discovered_peer(
    peer: dict, *, device_registry: DeviceRegistry, mesh_node: MeshNode
) -> None:
    peer_id = peer.get("agent_id")
    if not peer_id or not device_registry.device_exists(peer_id):
        return
    if not device_registry.is_trusted(peer_id):
        logger.info(
            f"Discovered peer {peer_id} is known but not trusted; transport bootstrap skipped"
        )
        mesh_node.revoke_peer(peer_id)
        return
    endpoint = device_registry.get_mesh_endpoint(peer_id)
    candidate_endpoint = (peer.get("address"), peer.get("port"))
    if (
        candidate_endpoint[0]
        and not candidate_endpoint[0].startswith("127.")
        and endpoint != candidate_endpoint
    ):
        device_registry.bind_mesh(peer_id, peer.get("address"), peer.get("port"))
    peer_record = device_registry.get_by_device_id(peer_id) or {}
    public_key = peer_record.get("public_key")
    if public_key:
        try:
            mesh_node.trust_peer(peer_id, bytes.fromhex(public_key))
        except Exception as e:
            logger.warning(f"Could not trust discovered peer {peer_id}: {e}")
    if hasattr(mesh_node, "ensure_outbound_session"):
        await mesh_node.ensure_outbound_session(peer)


async def main() -> None:
    configure_logging()
    acquire_instance_lock()
    logger.info("AIDE v0.1 starting...")

    # ── Core ─────────────────────────────────────────────────────
    llm = LLMClient()
    memory = MemoryManager()
    safety = SafetyGate(memory=memory)
    offline_manager = OfflineManager(memory=memory)

    # ── Mesh ─────────────────────────────────────────────────────
    mesh_node = MeshNode(device_name=agent_name())
    identity = mesh_node.identity
    discovery = MeshDiscovery(identity=identity)
    mesh_node._discovery = discovery

    # ── Device registry + message router ─────────────────────────
    device_graph_path = settings.memory_db_path.parent / "device_registry.db"
    device_registry = DeviceRegistry(db_path=device_graph_path)
    device_registry.seed_default_peer_scope_templates()
    message_router = MessageRouter(registry=device_registry)
    alias_registry = AliasRegistry(device_graph_path)
    resolution_guard = ResolutionGuard(alias_registry)
    device_manifest = DeviceManifest(alias_registry, device_registry)
    target_interceptor = ExplicitTargetInterceptor(
        alias_registry, resolution_guard, memory
    )

    project_manager = ProjectManager(db_path=str(settings.memory_db_path))
    ensure_local_device_registered(identity, device_registry, alias_registry)
    owner_mesh_tasks = OwnerMeshTaskEngine(memory)
    owner_mesh = OwnerMeshOrchestrator(
        local_device_id=identity.device_id,
        registry=device_registry,
        router=message_router,
        task_engine=owner_mesh_tasks,
    )

    scheduler = ProactiveScheduler(
        memory=memory,
        project_manager=project_manager,
    )

    from tools.obsidian_tool import ObsidianTool
    from tools.awareness_tool import UserAwarenessTool
 
    module_settings = load_module_settings()
    tools = [
        ObsidianTool(),
        UserAwarenessTool(),
        ReminderTool(scheduler),
        MonitorTopicTool(scheduler),
        ListDevicesTool(alias_registry, device_registry),
    ]
    if module_settings.get("fitness"):
        try:
            from tools.fitness_os import FitnessOS

            tools.append(FitnessOS(memory))
        except ImportError:
            logger.warning("Fit Genie is disabled in this build.")
    if module_settings.get("browser"):
        tools.extend([WebSearchTool(), TavilySearchTool(), BrowserTool(), PageClickTool(), PriceMonitorTool()])
    if module_settings.get("peer_mesh"):
        tools.extend([
            MeshSendTool(mesh_node),
            DeviceRouterTool(
                alias_registry, resolution_guard, message_router, device_registry, memory
            ),
            DelegateMeshTaskTool(owner_mesh),
        ])
    if module_settings.get("projects"):
        tools.append(ProjectTool(project_manager))
    if module_settings.get("email"):
        tools.extend([ReadEmailTool(), SendEmailTool(), SearchEmailTool(), ReplyEmailTool()])
    if module_settings.get("finance"):
        tools.extend([
            IngestBankStatementTool(),
            GetMonthlyOverviewTool(),
            SetBudgetTargetTool(),
            SetupFinanceBudgetTool(),
            CategoriseTransactionTool(),
            CreateFinanceGoalTool(),
            GetProjectFinanceTool(),
        ])
    if module_settings.get("calendar"):
        tools.append(GoogleCalendarTool(default_calendar_id=settings.google_calendar_id))

    # ── Task queue ───────────────────────────────────────────────
    task_queue = TaskQueue(
        memory=memory,
        llm=llm,
        tools={t.name: t for t in tools},
    )

    # ── Agent ────────────────────────────────────────────────────
    agent = AideAgent(
        llm=llm,
        tools=tools,
        memory=memory,
        task_queue=task_queue,
        offline_manager=offline_manager,
        device_manifest=device_manifest,
        target_interceptor=target_interceptor,
        safety_gate=safety,
    )
    agent.device_id = identity.device_id
    safety.register_executor_builder(agent.execute_pending_action)

    from mesh.coordinator import MeshCoordinator

    coordinator = MeshCoordinator(
        mesh_node,
        agent,
        memory,
        message_router,
        task_engine=owner_mesh_tasks,
        safety_gate=safety,
        registry=device_registry,
    )
    coordinator.register_all_handlers()
    await coordinator.start_health_monitor(interval=60)
    task_queue._coordinator = coordinator

    # ── Mesh bridge ──────────────────────────────────────────────
    mesh_bridge = build_bridge(
        registry=device_registry,
        alias_registry=alias_registry,
        safety_gate=safety,
        message_router=message_router,
        memory=memory,
    )

    # ── Interfaces ───────────────────────────────────────────────
    onboarding = Onboarding(memory=memory)
    telegram = TelegramInterface(
        agent=agent,
        onboarding=onboarding,
        safety=safety,
        bridge=mesh_bridge,
    )
    agent._notify = telegram._notify

    # Wire Telegram into router and bridge
    message_router.set_telegram(telegram)
    message_router.set_mesh_node(mesh_node)
    owner_mesh.set_notify_callback(telegram._notify)
    safety.register_approval_request_handler(owner_mesh.route_approval_request)
    safety.activate_pending_approvals()
    mesh_bridge._router = message_router
    logger.info("Proxied device mesh ready")

    your_day = AIDEDailyBriefService(
        llm=llm,
        registry=device_registry,
        router=message_router,
        local_device_id=identity.device_id,
        fallback_notify=telegram._notify,
    )
    safety.register_approval_state_handler(your_day.sync_approval_status)
    set_runtime(
        agent=agent,
        safety=safety,
        your_day_service=your_day,
        registry=device_registry,
        alias_registry=alias_registry,
        mesh_node=mesh_node,
        mesh_discovery=discovery,
        owner_mesh=owner_mesh,
        mesh_identity=identity,
        router=message_router,
    )

    # ── Complete scheduler ───────────────────────────────────────
    scheduler = ProactiveScheduler(
        memory=memory,
        project_manager=project_manager,
    )

    # ── System Monitoring ──────────────────────────────────────────
    monitor = DeviceMonitor(
        device_id=identity.device_id,
        on_state_change=scheduler._handle_system_state_change,
    )

    scheduler.attach(
        agent=agent,
        notify_callback=telegram._notify,
        telegram_interface=telegram,
        daily_brief_service=your_day,
    )
    await scheduler.start()
    await monitor.start()
    logger.info("Proactive scheduler and Device Monitor online")

    # ── Complete offline manager ──────────────────────────────────
    offline_manager._notify = telegram._notify
    offline_manager._agent = agent

    # ── Health check ─────────────────────────────────────────────
    available_providers = await llm.available_providers()
    if not available_providers:
        logger.warning(
            "No LLM available. AIDE will stay online so setup can be completed in the web UI."
        )
    if "ollama" in available_providers:
        logger.info(f"Ollama online — {settings.ollama_model}")
    other_providers = [
        provider.title() for provider in available_providers if provider != "ollama"
    ]
    if other_providers:
        logger.info(f"Cloud LLM providers available — {', '.join(other_providers)}")

    # ── Start mesh ───────────────────────────────────────────────
    await mesh_node.start_server()
    local_addresses = resolve_mesh_ipv4_addresses()
    local_mesh_host = local_addresses[0] if local_addresses else "127.0.0.1"
    device_registry.bind_mesh(identity.device_id, local_mesh_host, settings.mesh_port)
    for peer in device_registry.list_trusted_peers():
        public_key = peer.get("public_key")
        if not public_key or peer["device_id"] == identity.device_id:
            continue
        try:
            mesh_node.trust_peer(peer["device_id"], bytes.fromhex(public_key))
        except Exception as e:
            logger.warning(f"Could not bootstrap trusted peer {peer['device_id']}: {e}")
            continue
        endpoint = device_registry.get_mesh_endpoint(peer["device_id"])
        if endpoint:
            if endpoint[0].startswith("127."):
                logger.info(
                    f"Skipping loopback mesh endpoint for peer {peer['device_id']} until discovery/manual bind updates it"
                )
                continue
            if hasattr(mesh_node, "ensure_outbound_session"):
                await mesh_node.ensure_outbound_session(
                    {
                        "agent_id": peer["device_id"],
                        "address": endpoint[0],
                        "port": endpoint[1],
                    }
                )

    async def _register_discovered_peer(peer: dict) -> None:
        await bootstrap_discovered_peer(
            peer,
            device_registry=device_registry,
            mesh_node=mesh_node,
        )

    discovery.set_peer_callback(_register_discovered_peer)
    await discovery.start_async()
    logger.info(f"Mesh online — agent ID: {identity.device_id}")

    # ── Start Telegram ───────────────────────────────────────────
    await telegram.start()
    await safety.reannounce_pending_approvals()
    logger.info("Open Telegram and send /start to AIDE")

    # ── Start web interface ───────────────────────────────────────
    # ── Start web interface ───────────────────────────────────────
    socketio = AsyncServer(async_mode="asgi", cors_allowed_origins="*")
    asyncio.create_task(
        create_mesh_app(web_app, socketio, device_registry, message_router, task_queue)
    )
    agent._update_mesh_state = update_global_state
    agent._log_mesh_event = log_event

    web_config = uvicorn.Config(
        web_app,
        host=settings.web_host,
        port=settings.web_port,
        log_level="warning",
    )
    web_server = uvicorn.Server(web_config)
    web_task = asyncio.create_task(web_server.serve())
    logger.info(f"Web interface online — http://localhost:{settings.web_port}")
    tray = SystemTray()
    tray.start()

    # ── Start offline monitor ─────────────────────────────────────
    await offline_manager.start_monitor()
    logger.info("Offline monitor online")

    # ── Graceful shutdown ─────────────────────────────────────────
    stop_event = asyncio.Event()

    def _signal() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal)
        except NotImplementedError:
            pass

    await stop_event.wait()

    logger.info("Shutting down...")
    safety.emergency_stop()
    offline_manager.stop()
    await scheduler.stop()
    await monitor.stop()
    web_server.should_exit = True

    try:
        await asyncio.wait_for(telegram.stop(), timeout=5)
    except asyncio.TimeoutError:
        logger.warning("Telegram stop timed out")

    try:
        await asyncio.wait_for(mesh_node.stop(), timeout=5)
    except asyncio.TimeoutError:
        logger.warning("Mesh node stop timed out")

    try:
        await asyncio.wait_for(discovery.stop_async(), timeout=5)
    except asyncio.TimeoutError:
        logger.warning("Mesh discovery stop timed out")
    tray.stop()

    if not web_task.done():
        try:
            await asyncio.wait_for(web_task, timeout=5)
        except asyncio.TimeoutError:
            web_task.cancel()
            logger.warning("Web server stop timed out")
    logger.info("Done.")
    release_instance_lock()


if __name__ == "__main__":
    asyncio.run(main())
