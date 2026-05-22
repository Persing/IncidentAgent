# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""
PluginRegistry — central registry mapping plugin names to their classes.

Plugins self-register on module import via the decorator methods:

    @registry.register_source
    class MySource(SourcePlugin):
        name = "my_source"
        ...

The registry stores classes, not instances — plugins are instantiated
at the call site with a per-plugin config dict.

Import the module-level singleton:

    from src.plugins.registry import registry
"""

from __future__ import annotations

from typing import Type

from src.plugins.base import AlertPlugin, OutputPlugin, SourcePlugin


class PluginRegistry:
    def __init__(self) -> None:
        self._sources: dict[str, Type[SourcePlugin]] = {}
        self._alerts: dict[str, Type[AlertPlugin]] = {}
        self._outputs: dict[str, Type[OutputPlugin]] = {}

    # ── Registration (usable as class decorators) ─────────────────────────────

    def register_source(self, cls: Type[SourcePlugin]) -> Type[SourcePlugin]:
        self._sources[cls.name] = cls
        return cls

    def register_alert(self, cls: Type[AlertPlugin]) -> Type[AlertPlugin]:
        self._alerts[cls.name] = cls
        return cls

    def register_output(self, cls: Type[OutputPlugin]) -> Type[OutputPlugin]:
        self._outputs[cls.name] = cls
        return cls

    # ── Lookup ────────────────────────────────────────────────────────────────

    def get_source(self, name: str) -> Type[SourcePlugin]:
        if name not in self._sources:
            raise ValueError(
                f"Unknown source plugin '{name}'. Registered: {sorted(self._sources)}"
            )
        return self._sources[name]

    def get_alert(self, name: str) -> Type[AlertPlugin]:
        if name not in self._alerts:
            raise ValueError(
                f"Unknown alert plugin '{name}'. Registered: {sorted(self._alerts)}"
            )
        return self._alerts[name]

    def get_output(self, name: str) -> Type[OutputPlugin]:
        if name not in self._outputs:
            raise ValueError(
                f"Unknown output plugin '{name}'. Registered: {sorted(self._outputs)}"
            )
        return self._outputs[name]


# Module-level singleton — import this everywhere
registry = PluginRegistry()
