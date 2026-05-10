# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""
Output schema and prompt templates for the triage agent.

TriagePlan is the structured output contract — every response from the agent
is an instance of this model. Typed fields make downstream consumption
(API serialization, eval scoring, dashboards) straightforward.

The prompt templates are kept here rather than inline in the agent so they
can be versioned, tested, and swapped independently of the graph logic.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field


# ── Output schema ────────────────────────────────────────────────────────────


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Confidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TriagePlan(BaseModel):
    """
    Structured triage plan produced by the incident triage agent.

    Every field has a description that is passed to the LLM as part of the
    tool/function schema. Clear descriptions produce better structured output.
    """

    incident_summary: str = Field(
        description=(
            "One sentence restatement of the incident in plain language. "
            "State what is broken and what the observable impact is."
        )
    )
    severity: Severity = Field(
        description=(
            "Inferred severity. "
            "critical=service down or data loss; "
            "high=service degraded, SLO at risk; "
            "medium=elevated errors/latency, not yet SLO-impacting; "
            "low=warning threshold crossed, no immediate impact."
        )
    )
    likely_cause: str = Field(
        description=(
            "Most probable root cause given the alert text and retrieved runbooks. "
            "Be specific. If uncertain, state the two most likely causes."
        )
    )
    affected_components: list[str] = Field(
        description=(
            "Services, systems, or infrastructure components likely involved. "
            "Include both the directly affected component and any likely dependencies."
        )
    )
    diagnostic_steps: list[str] = Field(
        description=(
            "Ordered list of diagnostic steps as plain strings — do not include "
            "numbers or bullet prefixes. Step 1 should be the fastest check "
            "that confirms or rules out the most likely cause. Include specific "
            "commands, metric names, or dashboard links where the runbooks provide them."
        )
    )
    resolution_steps: list[str] = Field(
        description=(
            "Ordered list of resolution steps as plain strings — do not include "
            "numbers or bullet prefixes. Use conditional form where the cause "
            "is not yet confirmed: 'If cause is X: do Y. If cause is Z: do W.' "
            "Include the rollback path if a recent deploy is a possible cause."
        )
    )
    escalation_criteria: list[str] = Field(
        description=(
            "Specific conditions that mean this incident is beyond the scope of "
            "this runbook and requires escalating to a senior engineer or another team."
        )
    )
    runbooks_referenced: list[str] = Field(
        description=(
            "Exact names of the runbooks (without .md extension) that informed "
            "this triage plan. Only list runbooks that were actually used."
        )
    )
    confidence: Confidence = Field(
        description="Confidence level in this triage plan given the available information."
    )
    confidence_reason: str = Field(
        description=(
            "One sentence explaining the confidence level. "
            "If low/medium, state what additional information would increase confidence."
        )
    )


# ── Prompt templates ─────────────────────────────────────────────────────────


SYSTEM_PROMPT = """\
You are an expert incident triage assistant for a cloud infrastructure platform.

Your role is to analyze incident alerts and produce structured triage plans \
that on-call engineers can act on immediately. You have access to retrieved \
runbook content that describes known failure modes, diagnostic steps, and \
resolution procedures for this platform.

Guidelines:
- Be specific and actionable. Generic advice wastes critical time during an incident.
- Order diagnostic steps fastest-first: check the most likely cause with the \
  quickest command before moving to slower investigations.
- Use conditional resolution steps when the root cause is not yet confirmed.
- Reference only the runbooks provided in context. Do not invent runbook names.
- Severity definitions:
    critical — service is completely down or data loss is occurring
    high     — service is degraded, customer-facing SLO is at risk
    medium   — elevated error rate or latency, not yet SLO-impacting
    low      — warning threshold crossed, no immediate customer impact\
"""

HUMAN_TEMPLATE = """\
## Incident Alert

{query}

## Retrieved Runbook Context

{context}

---

Based on the incident alert and the runbook context above, produce a \
structured triage plan. Be specific — include commands and metric names \
from the runbooks where available.\
"""

TRIAGE_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_PROMPT),
        ("human", HUMAN_TEMPLATE),
    ]
)


# ── Context formatting ───────────────────────────────────────────────────────


def format_runbook_context(runbook_paths: list[str]) -> str:
    """
    Load and format runbook Markdown files into a single context block
    for inclusion in the LLM prompt.

    Strips the ## Tags section (metadata noise) but keeps everything else,
    including ## Ownership (relevant for escalation guidance).

    Args:
        runbook_paths: List of file paths to the matched runbook files.

    Returns:
        A formatted string with all runbooks separated by dividers.
    """
    sections = []
    for path_str in runbook_paths:
        path = Path(path_str)
        if not path.exists():
            continue

        content = path.read_text(encoding="utf-8")

        # Strip the ## Tags section — it's metadata for the retriever, not
        # useful content for the LLM generating the triage plan.
        content = re.sub(
            r"## Tags\n.*?(?=\n## |\Z)",
            "",
            content,
            flags=re.DOTALL,
        ).strip()

        sections.append(content)

    if not sections:
        return "No runbook context available."

    divider = "\n\n" + "─" * 60 + "\n\n"
    return divider.join(sections)
