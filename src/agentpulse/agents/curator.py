from __future__ import annotations

from datetime import date

from agentpulse.agents.base import Agent
from agentpulse.metrics import VenueMetric
from agentpulse.models import Paper, Plan
from agentpulse.scoring import dedupe, rank


class CuratorAgent(Agent):
    """去重 + 规则打分 + 截断。纯确定性，不调模型。"""

    name = "curator"

    def run(
        self,
        papers: list[Paper],
        plan: Plan,
        max_papers: int,
        metrics: dict[str, VenueMetric] | None = None,
    ) -> list[Paper]:
        unique = dedupe(papers)
        kept = rank(
            unique,
            plan.topic,
            plan.subthemes,
            plan.queries,
            date.today(),
            max_papers,
            metrics=metrics,
        )
        self.log(fetched=len(papers), unique=len(unique), kept=len(kept), metrics=len(metrics or {}))
        return kept
