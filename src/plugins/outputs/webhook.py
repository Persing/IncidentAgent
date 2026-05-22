# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""
WebhookOutput — models the current JSON response behavior as a no-op.

The HTTP response is assembled by the API endpoint from TriageResult
fields and returned directly — there is no external delivery step.
This plugin makes the output slot in the pipeline explicit and testable.

Future output plugins (SlackOutput, PagerDutyOutput) will make HTTP
calls in deliver(). WebhookOutput simply returns.
"""

from __future__ import annotations

from src.plugins.base import OutputPlugin, TriageResult
from src.plugins.registry import registry


@registry.register_output
class WebhookOutput(OutputPlugin):
    name = "webhook"

    async def deliver(self, result: TriageResult, config: dict) -> None:
        return
