"""
Integrate Mesh Dashboard into FastAPI app
"""

from fastapi import FastAPI, Request
from socketio import AsyncServer
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from core.runtime_paths import resource_path
from web.mesh_api import (
    mesh_router,
    init_socketio,
    register_socketio_handlers,
    update_global_state,
    log_event,
)

templates = Jinja2Templates(directory=resource_path("web", "templates"))


async def create_mesh_app(
    app: FastAPI, socketio: AsyncServer, device_registry, message_router, task_queue
):
    """
    Add mesh dashboard to existing FastAPI app
    """
    # Register router
    app.include_router(mesh_router)

    # Initialize WebSocket, Registry, Router and TaskQueue
    init_socketio(socketio, device_registry)
    from web.mesh_api import init_router

    init_router(message_router)

    import web.mesh_api as mesh_api

    mesh_api.task_queue = task_queue

    await register_socketio_handlers(socketio)

    # Add route for dashboard page
    @app.get("/mesh/dashboard", response_class=HTMLResponse)
    async def mesh_dashboard(request: Request):
        return templates.TemplateResponse("mesh_dashboard.html", {"request": request})

    print("✅ Mesh Dashboard integrated at /mesh/dashboard")


# Export utilities for use in main app
__all__ = ["create_mesh_app", "update_global_state", "log_event"]
