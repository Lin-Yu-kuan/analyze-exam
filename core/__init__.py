"""Minimal plugin lifecycle and notification infrastructure."""

from .events import EventBus
from .manager import LifecycleError, PluginManager, ShutdownError

__all__ = ["EventBus", "LifecycleError", "PluginManager", "ShutdownError"]
