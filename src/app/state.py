import threading
from app.app_logging import logging


logger = logging.getLogger(__name__)


state = "idle"  # "idle" | "pause" | "timer" | "recording"

chat_channel: str | None = None
