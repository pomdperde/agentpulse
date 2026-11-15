from __future__ import annotations

from agentpulse.config import Settings
from agentpulse.llm import LLMClient
from agentpulse.trace import Trace


class Agent:
    """所有 Agent 的基类：只持有无状态的依赖，不保存跨调用状态。"""

    name = "agent"

    def __init__(self, llm: LLMClient, settings: Settings, trace: Trace):
        self.llm = llm
        self.settings = settings
        self.trace = trace

    def log(self, **fields: object) -> None:
        self.trace.event(f"agent.{self.name}", **fields)
