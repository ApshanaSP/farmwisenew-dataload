"""Agents: tool-using workflows on top of the deterministic pipeline.

Each agent reads through typed tools (plain functions over the curated tables),
writes only drafts, review items, alerts or reports, and logs every run to
agent_runs. Nothing an agent produces changes a source record; decisions that
would (verify, merge, assign, send) go to the review queue for a person.
LLM steps are optional and always have a deterministic fallback.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class AgentRun:
    agent: str
    trigger: str
    started: float = field(default_factory=time.time)
    tools: list[str] = field(default_factory=list)
    llm_calls: int = 0
    notes: list[str] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)

    def tool(self, name: str) -> None:
        self.tools.append(name)

    def row(self, as_of: pd.Timestamp) -> dict[str, Any]:
        return {"agent": self.agent, "trigger": self.trigger, "as_of": as_of, "duration_s": round(time.time() - self.started, 2),
                "tools": "|".join(dict.fromkeys(self.tools)), "llm_calls": self.llm_calls,
                "outputs": "; ".join(f"{k}={v}" for k, v in self.outputs.items()), "notes": " ".join(self.notes)}
