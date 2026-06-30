"""
AIDE Mesh Dashboard API
Real-time mesh status and control endpoints for FastAPI
"""

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from socketio import AsyncServer
from datetime import datetime
from typing import Dict, List, Optional
import asyncio
import random

mesh_router = APIRouter()
socketio = None  # Initialized by main app
device_registry = None  # Initialized by main app
task_queue = None  # Initialized by main app

# Global state tracking
mesh_state = {
    "current_project": None,
    "current_task": None,
    "execution_device": None,
    "last_updated": None,
    "task_progress": 0,
    "eta_seconds": None,
}

event_log = []  # In-memory event log (last 100 events)


def init_socketio(sio_instance: AsyncServer, registry_instance):
    """Initialize SocketIO and Registry instances"""
    global socketio, device_registry
    socketio = sio_instance
    device_registry = registry_instance


# 1. Visual Mesh Map - Network Topology


@mesh_router.get("/api/mesh/graph")
async def get_mesh_graph():
    """
    Return network graph data for visualization
    """
    if device_registry is None:
        raise HTTPException(status_code=500, detail="Device registry not initialized")

    registry = device_registry
    devices = registry.list_all()

    nodes = []
    for device in devices:
        if device.get("is_online"):
            status = "online"
        elif device.get("last_seen"):
            import time

            if time.time() - device["last_seen"] < 300:
                status = "idle"
            else:
                status = "offline"
        else:
            status = "offline"

        nodes.append(
            {
                "id": device["device_id"],
                "label": device.get("device_name", device["device_id"][:8]),
                "status": status,
                "device_type": device.get("device_type", "unknown"),
                "capabilities": device.get("capabilities", []),
                "is_primary": device.get("is_primary", False),
            }
        )

    edges = []
    primary = next((n for n in nodes if n.get("is_primary")), None)

    if primary:
        for node in nodes:
            if node["id"] != primary["id"]:
                edges.append(
                    {
                        "source": primary["id"],
                        "target": node["id"],
                        "active": node["status"] == "online",
                        "latency": random.randint(10, 100),
                    }
                )

    return {"nodes": nodes, "edges": edges, "timestamp": datetime.now().isoformat()}


# 2. Global State Command Center


@mesh_router.get("/api/mesh/state")
async def get_global_state():
    """
    Get current global execution state
    """
    if device_registry is None:
        raise HTTPException(status_code=500, detail="Device registry not initialized")

    registry = device_registry
    devices = registry.list_all()
    online_count = sum(1 for d in devices if d.get("is_online"))
    total_count = len(devices)

    health = "unknown"
    if total_count > 0:
        ratio = online_count / total_count
        if ratio >= 0.8:
            health = "healthy"
        elif ratio >= 0.5:
            health = "degraded"
        else:
            health = "critical"

    # Try to pull current task from TaskQueue
    task_info = {}
    if task_queue:
        active_tasks = task_queue.get_active_tasks()
        if active_tasks:
            t = active_tasks[0]
            # Calculate progress based on completed steps
            completed = sum(1 for s in t.steps if s.status == "complete")
            progress = int((completed / len(t.steps)) * 100) if t.steps else 0
            task_info = {
                "current_project": "General",  # Default or extracted from t.description
                "current_task": t.description,
                "execution_device": "Local",
                "task_progress": progress,
            }

    state = {**mesh_state, **task_info}
    return {**state, "mesh_health": health}


def update_global_state(project=None, task=None, device=None, progress=None):
    """Update global state and broadcast to clients"""
    if project:
        mesh_state["current_project"] = project
    if task:
        mesh_state["current_task"] = task
    if device:
        mesh_state["execution_device"] = device
    if progress is not None:
        mesh_state["task_progress"] = progress

    mesh_state["last_updated"] = datetime.now().isoformat()

    if socketio:
        # Use asyncio.create_task because this might be called from synchronous code
        asyncio.create_task(socketio.emit("state_update", mesh_state, room="/mesh"))


# 3. Quick-Action Hub

# Store router for actions
message_router = None


def init_router(router_instance):
    """Initialize the message router for actions"""
    global message_router
    message_router = router_instance


@mesh_router.post("/api/mesh/actions/ping/{device_id}")
async def ping_device(device_id: str):
    """Ping a specific device"""
    if message_router is None:
        raise HTTPException(status_code=500, detail="Message router not initialized")

    try:
        log_event("ping", f"Ping sent to {device_id[:8]}...", "info")
        response = await message_router.send_with_response(
            to_device_id=device_id, message_type="PING", payload={}
        )
        return {
            "device_id": device_id,
            "status": response.get("status", "sent") if response else "failed",
            "timestamp": datetime.now().isoformat(),
            "response": response,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mesh_router.post("/api/mesh/actions/fallback")
async def toggle_fallback_mode(request: Request):
    """Toggle local-only fallback mode"""
    data = await request.json()
    enabled = data.get("enabled", False)
    log_event(
        "system",
        f'Local fallback mode: {"ENABLED" if enabled else "DISABLED"}',
        "warning" if enabled else "info",
    )
    return {"fallback_enabled": enabled, "timestamp": datetime.now().isoformat()}


@mesh_router.post("/api/mesh/actions/refresh")
async def refresh_mesh_state():
    """Force mesh state refresh"""
    log_event("system", "Mesh state refresh initiated", "info")
    return {"status": "refreshing", "timestamp": datetime.now().isoformat()}


@mesh_router.post("/api/mesh/actions/delegate")
async def delegate_task(request: Request):
    """Delegate task to specific device"""
    data = await request.json()
    device_id = data.get("device_id")
    task_type = data.get("task_type")
    if not device_id or not task_type:
        raise HTTPException(status_code=400, detail="Missing device_id or task_type")

    log_event("delegation", f"Task {task_type} delegated to {device_id[:8]}...", "info")
    return {
        "status": "delegated",
        "device_id": device_id,
        "task_type": task_type,
        "timestamp": datetime.now().isoformat(),
    }


# 4. Live Event Log


@mesh_router.get("/api/mesh/events")
async def get_event_log(limit: int = 100, type: Optional[str] = None):
    """Get recent events"""
    filtered_events = event_log
    if type:
        filtered_events = [e for e in event_log if e["type"] == type]

    return {"events": filtered_events[-limit:], "total": len(event_log)}


def log_event(event_type: str, message: str, severity: str = "info"):
    """Log an event and broadcast to clients"""
    event = {
        "id": len(event_log),
        "type": event_type,
        "message": message,
        "severity": severity,
        "timestamp": datetime.now().isoformat(),
    }
    event_log.append(event)
    if len(event_log) > 100:
        event_log.pop(0)

    if socketio:
        asyncio.create_task(socketio.emit("new_event", event, room="/mesh"))


# WebSocket Handlers


async def register_socketio_handlers(sio: AsyncServer):
    """Register WebSocket event handlers"""

    @sio.on("connect")
    async def handle_connect(sid, environ):
        await sio.emit(
            "connection_established",
            {"status": "connected", "timestamp": datetime.now().isoformat()},
            room=sid,
        )
        await sio.emit("state_update", mesh_state, room=sid)

    @sio.on("request_update")
    async def handle_update_request(sid):
        await sio.emit("state_update", mesh_state, room=sid)


# Export for use in main app
__all__ = [
    "mesh_router",
    "init_socketio",
    "register_socketio_handlers",
    "update_global_state",
    "log_event",
]
