# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""
Plugin system for IncidentAgent.

Public surface:

    SourcePlugin   — ABC for document source plugins
    AlertPlugin    — ABC for alert normalization plugins
    OutputPlugin   — ABC for result delivery plugins
    AlertSchema    — normalized inbound alert model
    TriageResult   — completed triage result model
    registry       — module-level PluginRegistry singleton

Concrete plugin modules are NOT imported here — they self-register when
imported explicitly at their call sites (loader.py, api/main.py lifespan).
This keeps the import graph predictable and testable.
"""

from src.plugins.base import AlertPlugin, AlertSchema, OutputPlugin, SourcePlugin, TriageResult
from src.plugins.registry import registry

__all__ = [
    "AlertPlugin",
    "AlertSchema",
    "OutputPlugin",
    "SourcePlugin",
    "TriageResult",
    "registry",
]
