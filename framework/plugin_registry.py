"""
framework/plugin_registry.py

Simple plugin registry: maps plugin names to plugin classes.

Usage:
    # In plugin file:
    from framework.plugin_registry import register

    @register
    class MyPlugin(ResearchPlugin):
        name = "my_plugin"
        ...

    # In graph builder / CLI:
    from framework.plugin_registry import resolve
    plugin = resolve("my_plugin")
"""

from __future__ import annotations
import importlib
import logging
import sys
from pathlib import Path
from typing import Type

from .plugin_interface import ResearchPlugin

logger = logging.getLogger(__name__)

_registry: dict[str, Type[ResearchPlugin]] = {}


def register(cls: Type[ResearchPlugin]) -> Type[ResearchPlugin]:
    """Class decorator that registers a plugin by its `name` property."""
    # Support both @property and plain class attribute for `name`
    name = cls.name if isinstance(cls.name, str) else cls().name
    if name in _registry:
        raise ValueError(f"Plugin '{name}' is already registered.")
    _registry[name] = cls
    return cls


def resolve(name: str) -> ResearchPlugin:
    """Return an instantiated plugin by name. Raises KeyError if not found."""
    if name not in _registry:
        available = ", ".join(_registry.keys()) or "(none)"
        raise KeyError(f"Plugin '{name}' not found. Available: {available}")
    return _registry[name]()


def list_plugins() -> list[str]:
    """Return all registered plugin names."""
    return list(_registry.keys())


def discover_plugins(base_dir: str | None = None) -> list[str]:
    """
    Scan projects/*/plugin.py and import each module to trigger @register.

    Replaces manual 'import projects.xxx.plugin' lines in cli/main.py and main.py.
    Idempotent — already-imported modules are skipped.

    Returns a list of module names that were newly imported.
    """
    root = Path(base_dir or Path(__file__).parent.parent)
    projects_dir = root / "projects"
    if not projects_dir.is_dir():
        logger.warning("discover_plugins: 'projects/' directory not found at %s", root)
        return []

    discovered = []
    for plugin_file in sorted(projects_dir.glob("*/plugin.py")):
        module_name = f"projects.{plugin_file.parent.name}.plugin"
        if module_name in sys.modules:
            continue
        try:
            importlib.import_module(module_name)
            discovered.append(module_name)
            logger.debug("discover_plugins: loaded %s", module_name)
        except Exception as e:
            logger.warning("discover_plugins: failed to load %s — %s", module_name, e)

    if discovered:
        logger.info("discover_plugins: loaded %d plugin(s): %s", len(discovered), discovered)
    return discovered
