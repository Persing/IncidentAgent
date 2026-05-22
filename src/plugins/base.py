# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""
Plugin interface contracts for IncidentAgent.

Three plugin types define the extensible integration surface:

    SourcePlugin   — fetch documents from any runbook source
    AlertPlugin    — normalize inbound alert payloads
    OutputPlugin   — deliver completed triage results

The core retrieval pipeline (BM25 + semantic + RRF) never imports
concrete plugin implementations; it depends only on these ABCs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict


# ── Shared models ─────────────────────────────────────────────────────────────


class AlertSchema(BaseModel):
    """Normalized representation of an inbound incident alert."""

    raw_text: str
    source: str
    return_k: int = 3
    metadata: dict[str, Any] = {}


class TriageResult(BaseModel):
    """Completed triage result passed to output plugins."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    alert: AlertSchema
    plan: Any          # TriagePlan — Any avoids a circular import at ABC level
    matches: list[Any] # list[RunbookMatch]
    latency_ms: float
    request_id: str


# ── Plugin ABCs ───────────────────────────────────────────────────────────────


class SourcePlugin(ABC):
    """Produces RunbookChunk objects from a document source."""

    name: str

    @abstractmethod
    def fetch_documents(self, config: dict) -> list:
        """
        Fetch and parse documents into RunbookChunk objects.

        Args:
            config: Plugin-specific configuration dict from Settings.

        Returns:
            list[RunbookChunk] ready for embedding and BM25 indexing.
        """
        raise NotImplementedError


class AlertPlugin(ABC):
    """Normalizes an inbound alert payload into AlertSchema."""

    name: str

    @abstractmethod
    def normalize(self, payload: dict) -> AlertSchema:
        """
        Translate a raw inbound payload into a normalized AlertSchema.

        Args:
            payload: Raw request body as a dict. Shape is plugin-specific.

        Returns:
            AlertSchema with raw_text set to the incident description.
        """
        raise NotImplementedError


class OutputPlugin(ABC):
    """Delivers a completed triage result to an external system."""

    name: str

    @abstractmethod
    async def deliver(self, result: TriageResult, config: dict) -> None:
        """
        Deliver the triage result.

        Args:
            result: Completed triage result including plan and matches.
            config: Plugin-specific configuration dict from Settings.
        """
        raise NotImplementedError
