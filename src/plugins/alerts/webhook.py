# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""
WebhookAlert — normalizes the existing plain-text HTTP POST body.

The current API already accepts pre-normalized incident text, so this
plugin is an identity transformation. Future alert plugins (PagerDuty,
Alertmanager, CloudWatch) will do real field extraction here.

Expected payload shape (matches TriageRequest):
    { "query": str, "return_k": int }
"""

from __future__ import annotations

from src.plugins.base import AlertPlugin, AlertSchema
from src.plugins.registry import registry


@registry.register_alert
class WebhookAlert(AlertPlugin):
    name = "webhook"

    def normalize(self, payload: dict) -> AlertSchema:
        return AlertSchema(
            raw_text=payload.get("query", ""),
            source="webhook",
            return_k=int(payload.get("return_k", 3)),
            metadata={},
        )
