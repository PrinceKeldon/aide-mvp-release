"""
AIDE — system tray
The only visible sign AIDE is running.
Shows "Agent online" and provides a right-click menu.
Runs in its own thread — does not block the asyncio event loop.
"""
import threading
import asyncio
from loguru import logger
from core.settings import settings

try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False
    logger.warning("pystray/Pillow not available — system tray disabled")


def _make_icon(online: bool = True) -> "Image.Image":
    img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = "#1D9E75" if online else "#BA7517"
    draw.ellipse([4, 4, 28, 28], fill=color)
    draw.ellipse([12, 12, 20, 20], fill="white")
    return img


class SystemTray:

    def __init__(self, shutdown_callback=None) -> None:
        self._shutdown_callback = shutdown_callback
        self._icon = None
        self._thread: threading.Thread | None = None
        self._online = True

    def start(self) -> None:
        if not TRAY_AVAILABLE:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("System tray icon started")

    def _run(self) -> None:
        try:
            menu = pystray.Menu(
                pystray.MenuItem("AIDE — Agent online", None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Open AIDE", self._open_aide),
                pystray.MenuItem("Settings", self._open_settings),
                pystray.MenuItem("Open Telegram", self._open_telegram),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit AIDE", self._quit),
            )
            self._icon = pystray.Icon(
                name="AIDE",
                icon=_make_icon(online=True),
                title="AIDE — Agent online",
                menu=menu,
            )
            self._icon.run()
        except Exception as exc:
            logger.warning(f"System tray unavailable in this environment: {exc}")
            self._icon = None

    def set_status(self, online: bool, message: str = "") -> None:
        if self._icon:
            self._icon.icon = _make_icon(online=online)
            self._icon.title = (
                f"AIDE — {message or ('online' if online else 'offline')}"
            )

    def _open_telegram(self, icon, item) -> None:
        import webbrowser
        webbrowser.open("https://t.me/")

    def _open_aide(self, icon, item) -> None:
        import webbrowser
        webbrowser.open(f"http://localhost:{settings.web_port}")

    def _open_settings(self, icon, item) -> None:
        import webbrowser
        webbrowser.open(f"http://localhost:{settings.web_port}/settings")

    def _quit(self, icon, item) -> None:
        logger.info("Quit requested from system tray")
        icon.stop()
        if self._shutdown_callback:
            asyncio.run_coroutine_threadsafe(
                self._shutdown_callback(),
                asyncio.get_event_loop(),
            )

    def stop(self) -> None:
        if self._icon:
            self._icon.stop()
