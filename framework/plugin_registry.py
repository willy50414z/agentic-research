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
from typing import Type
from .plugin_interface import ResearchPlugin

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
