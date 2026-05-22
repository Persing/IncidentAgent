# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""
Unit tests for the plugin system: ABCs, registry, and all three first plugins.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.plugins.base import AlertPlugin, AlertSchema, OutputPlugin, SourcePlugin, TriageResult
from src.plugins.registry import PluginRegistry


# ── AlertSchema ───────────────────────────────────────────────────────────────


class TestAlertSchema:
    def test_required_fields(self):
        schema = AlertSchema(raw_text="pod crashloop", source="webhook")
        assert schema.raw_text == "pod crashloop"
        assert schema.source == "webhook"

    def test_return_k_defaults_to_3(self):
        schema = AlertSchema(raw_text="x", source="y")
        assert schema.return_k == 3

    def test_metadata_defaults_empty(self):
        schema = AlertSchema(raw_text="x", source="y")
        assert schema.metadata == {}

    def test_custom_return_k(self):
        schema = AlertSchema(raw_text="x", source="y", return_k=5)
        assert schema.return_k == 5


# ── TriageResult ──────────────────────────────────────────────────────────────


class TestTriageResult:
    def test_construct_with_dataclass_matches(self, sample_triage_plan, sample_matches):
        alert = AlertSchema(raw_text="test", source="webhook")
        result = TriageResult(
            alert=alert,
            plan=sample_triage_plan,
            matches=sample_matches,
            latency_ms=42.0,
            request_id="abc-123",
        )
        assert result.request_id == "abc-123"
        assert result.latency_ms == 42.0
        assert result.alert.source == "webhook"


# ── PluginRegistry ────────────────────────────────────────────────────────────


class TestPluginRegistry:
    def setup_method(self):
        self.reg = PluginRegistry()

    def test_register_source_stores_class(self):
        class MySource(SourcePlugin):
            name = "my_source"
            def fetch_documents(self, config): return []

        self.reg.register_source(MySource)
        assert self.reg.get_source("my_source") is MySource

    def test_register_alert_stores_class(self):
        class MyAlert(AlertPlugin):
            name = "my_alert"
            def normalize(self, payload): return AlertSchema(raw_text="x", source="my_alert")

        self.reg.register_alert(MyAlert)
        assert self.reg.get_alert("my_alert") is MyAlert

    def test_register_output_stores_class(self):
        class MyOutput(OutputPlugin):
            name = "my_output"
            async def deliver(self, result, config): return None

        self.reg.register_output(MyOutput)
        assert self.reg.get_output("my_output") is MyOutput

    def test_register_returns_class_for_decorator_use(self):
        class MySource(SourcePlugin):
            name = "decorator_test"
            def fetch_documents(self, config): return []

        returned = self.reg.register_source(MySource)
        assert returned is MySource

    def test_get_source_unknown_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown source plugin"):
            self.reg.get_source("nonexistent")

    def test_get_alert_unknown_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown alert plugin"):
            self.reg.get_alert("nonexistent")

    def test_get_output_unknown_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown output plugin"):
            self.reg.get_output("nonexistent")

    def test_error_message_lists_registered_names(self):
        class MySource(SourcePlugin):
            name = "listed_source"
            def fetch_documents(self, config): return []

        self.reg.register_source(MySource)
        with pytest.raises(ValueError, match="listed_source"):
            self.reg.get_source("wrong_name")


# ── WebhookAlert ──────────────────────────────────────────────────────────────


class TestWebhookAlert:
    def setup_method(self):
        import src.plugins.alerts.webhook  # noqa: F401 — trigger registration
        from src.plugins.alerts.webhook import WebhookAlert
        self.plugin = WebhookAlert()

    def test_normalize_basic_payload(self):
        result = self.plugin.normalize({"query": "pod crashloop", "return_k": 3})
        assert result.raw_text == "pod crashloop"
        assert result.source == "webhook"
        assert result.return_k == 3

    def test_normalize_preserves_return_k(self):
        result = self.plugin.normalize({"query": "disk full alert", "return_k": 5})
        assert result.return_k == 5

    def test_normalize_defaults_return_k_when_missing(self):
        result = self.plugin.normalize({"query": "some alert text here"})
        assert result.return_k == 3

    def test_normalize_source_is_webhook(self):
        result = self.plugin.normalize({"query": "test alert"})
        assert result.source == "webhook"

    def test_normalize_metadata_is_empty(self):
        result = self.plugin.normalize({"query": "test alert"})
        assert result.metadata == {}


# ── LocalFileSource ───────────────────────────────────────────────────────────


class TestLocalFileSource:
    def setup_method(self):
        import src.plugins.sources.local_file  # noqa: F401 — trigger registration
        from src.plugins.sources.local_file import LocalFileSource
        self.plugin = LocalFileSource()

    def test_fetch_documents_returns_runbook_chunks(self, runbooks_dir):
        from src.ingestion.loader import RunbookChunk
        chunks = self.plugin.fetch_documents({"runbooks_dir": str(runbooks_dir)})
        assert len(chunks) > 0
        assert all(isinstance(c, RunbookChunk) for c in chunks)

    def test_fetch_documents_chunks_have_family(self, runbooks_dir):
        chunks = self.plugin.fetch_documents({"runbooks_dir": str(runbooks_dir)})
        families = {c.family for c in chunks}
        assert "compute" in families
        assert "networking" in families

    def test_fetch_documents_chunks_have_source_path(self, runbooks_dir):
        chunks = self.plugin.fetch_documents({"runbooks_dir": str(runbooks_dir)})
        assert all(c.source_path for c in chunks)

    def test_fetch_documents_empty_dir_returns_empty_list(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        chunks = self.plugin.fetch_documents({"runbooks_dir": str(empty_dir)})
        assert chunks == []

    def test_fetch_documents_missing_dir_raises_file_not_found(self, tmp_path):
        missing = tmp_path / "does_not_exist"
        with pytest.raises(FileNotFoundError):
            self.plugin.fetch_documents({"runbooks_dir": str(missing)})

    def test_fetch_documents_uses_default_dir_key(self, runbooks_dir, monkeypatch):
        monkeypatch.chdir(runbooks_dir.parent)
        from src.plugins.sources.local_file import LocalFileSource
        plugin = LocalFileSource()
        chunks = plugin.fetch_documents({"runbooks_dir": str(runbooks_dir)})
        assert len(chunks) > 0


# ── WebhookOutput ─────────────────────────────────────────────────────────────


class TestWebhookOutput:
    def setup_method(self):
        import src.plugins.outputs.webhook  # noqa: F401 — trigger registration
        from src.plugins.outputs.webhook import WebhookOutput
        self.plugin = WebhookOutput()

    @pytest.mark.asyncio
    async def test_deliver_is_noop(self, sample_triage_plan, sample_matches):
        alert = AlertSchema(raw_text="test", source="webhook")
        result = TriageResult(
            alert=alert,
            plan=sample_triage_plan,
            matches=sample_matches,
            latency_ms=10.0,
            request_id="test-id",
        )
        returned = await self.plugin.deliver(result, {})
        assert returned is None

    @pytest.mark.asyncio
    async def test_deliver_accepts_arbitrary_config(self, sample_triage_plan, sample_matches):
        alert = AlertSchema(raw_text="test", source="webhook")
        result = TriageResult(
            alert=alert,
            plan=sample_triage_plan,
            matches=sample_matches,
            latency_ms=5.0,
            request_id="x",
        )
        await self.plugin.deliver(result, {"some_key": "some_value"})


# ── Singleton registry has all three plugins ──────────────────────────────────


class TestPluginsRegisteredInSingleton:
    def test_local_file_source_in_registry(self):
        import src.plugins.sources.local_file  # noqa: F401
        from src.plugins.registry import registry
        assert registry.get_source("local_file") is not None

    def test_webhook_alert_in_registry(self):
        import src.plugins.alerts.webhook  # noqa: F401
        from src.plugins.registry import registry
        assert registry.get_alert("webhook") is not None

    def test_webhook_output_in_registry(self):
        import src.plugins.outputs.webhook  # noqa: F401
        from src.plugins.registry import registry
        assert registry.get_output("webhook") is not None
