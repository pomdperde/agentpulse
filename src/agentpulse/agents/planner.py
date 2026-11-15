from __future__ import annotations

import re

from agentpulse.agents.base import Agent
from agentpulse.models import Plan
from agentpulse.taxonomy import hotspot_terms

_SYSTEM = """你是论文检索规划专家。给定一个研究主题（可能是中文），产出结构化的检索计划。
要求：
1. 检索式必须使用英文，因为 arXiv / Semantic Scholar 只接受英文查询；
2. subthemes：3-6 个子主题，格式为“中文标签 (english phrase)”；
3. queries：4-8 条英文检索式，每条 2-6 个词；
4. 只输出 JSON：{"subthemes": [...], "queries": [...], "notes": "一句话说明覆盖策略"}。
禁止输出解释性文字。"""


def _is_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


# 中文主题词 → 英文检索词。命中即用英文查询，避免把中文直接丢给 arXiv。
_ZH_TERMS: dict[str, list[str]] = {
    # —— Agent / 通用 AI ——
    "多智能体": ["multi-agent system", "multi-agent collaboration", "agent orchestration"],
    "多agent": ["multi-agent system", "agent orchestration"],
    "工具调用": ["tool use", "function calling", "tool learning"],
    "函数调用": ["function calling", "tool use"],
    "记忆": ["memory", "long-term memory", "context management"],
    "上下文": ["long context", "context management"],
    "规划": ["planning", "task decomposition"],
    "推理": ["reasoning", "chain of thought"],
    "评测": ["evaluation", "benchmark"],
    "基准": ["benchmark", "evaluation suite"],
    "检索": ["retrieval augmented generation", "information retrieval"],
    "rag": ["retrieval augmented generation", "agentic RAG"],
    "代码": ["code generation", "software engineering"],
    "网页": ["web agent", "browser automation"],
    "gui": ["GUI agent", "computer use"],
    "安全": ["safety", "adversarial robustness", "security"],
    "具身": ["embodied agent", "embodied AI"],
    "协作": ["agent collaboration", "multi-agent coordination"],
    "反思": ["self-reflection", "self-correction"],
    "自主": ["autonomous system", "autonomous agent"],
    "工作流": ["workflow automation", "agentic workflow"],
    "智能体": ["LLM agent", "autonomous agent"],
    "大模型": ["large language model", "foundation model"],
    "多模态": ["multimodal large language model", "vision language model"],
    "扩散模型": ["diffusion model", "text-to-image generation"],
    "强化学习": ["reinforcement learning", "RLHF"],
    "联邦学习": ["federated learning"],
    "图神经网络": ["graph neural network"],
    "ai 安全": ["AI safety", "adversarial robustness"],
    "ai安全": ["AI safety", "adversarial robustness"],
    "生成式": ["generative AI"],
    "就业": ["labor market", "employment"],
    "社会影响": ["societal impact", "AI and society"],
    # —— 数学 ——
    "定理证明": ["theorem proving", "automated theorem prover", "LLM theorem prover"],
    "形式化": ["formal verification", "formal methods", "proof assistant"],
    "证明助手": ["proof assistant", "Lean theorem prover"],
    "数学推理": ["mathematical reasoning", "math word problem"],
    "符号计算": ["symbolic computation", "symbolic reasoning"],
    "几何": ["geometry problem solving"],
    "随机优化": ["stochastic optimization"],
    "高维统计": ["high-dimensional statistics"],
    "图论": ["graph theory"],
    "数值计算": ["numerical analysis"],
    # —— 工程学 ——
    "机器人": ["robot learning", "robotics"],
    "数字孪生": ["digital twin"],
    "智能制造": ["smart manufacturing"],
    "自动驾驶": ["autonomous driving"],
    "储能": ["energy storage"],
    "柔性电子": ["flexible electronics"],
    # —— 物理与天文学 ——
    "量子计算": ["quantum computing"],
    "超导": ["superconductivity"],
    "聚变": ["fusion energy"],
    "拓扑": ["topological materials"],
    "天体": ["astrophysics"],
    "光子": ["photonics"],
    # —— 医学 ——
    "肿瘤": ["tumor immunology", "cancer immunotherapy"],
    "免疫": ["immunotherapy"],
    "mrna": ["mRNA vaccine"],
    "基因编辑": ["CRISPR gene editing"],
    "单细胞": ["single-cell sequencing"],
    "阿尔茨海默": ["Alzheimer's disease"],
    "医学影像": ["medical imaging"],
    # —— 经济与商业 ——
    "数字经济": ["digital economy"],
    "esg": ["ESG investing"],
    "供应链": ["supply chain resilience"],
    "金融风险": ["financial risk"],
    "行为经济": ["behavioral economics"],
    # —— 社会科学与心理学 ——
    "平台治理": ["platform governance"],
    "社会网络": ["social network analysis"],
    "教育公平": ["education equity"],
    "老龄化": ["population aging"],
    "算法偏见": ["algorithmic bias"],
    # —— 地球与环境科学 ——
    "气候变化": ["climate change"],
    "碳捕集": ["carbon capture"],
    "极端天气": ["extreme weather"],
    "海洋酸化": ["ocean acidification"],
    "遥感": ["remote sensing"],
    "生物多样性": ["biodiversity"],
    # —— 人文与艺术 ——
    "数字人文": ["digital humanities"],
    "生成艺术": ["AI generated art"],
    "文化遗产": ["cultural heritage preservation"],
    "计算语言学": ["computational linguistics"],
    "媒介研究": ["media studies"],
}

# 二级学科热点的中英映射由 taxonomy 统一维护，这里合并进来，避免两处漂移。
for _label, _queries in hotspot_terms().items():
    _ZH_TERMS.setdefault(_label.lower(), _queries)

_DEFAULT_SUBTHEMES = [
    "推理与规划 (planning & reasoning)",
    "工具调用 (tool use)",
    "多智能体协作 (multi-agent collaboration)",
    "记忆与上下文 (memory & context)",
    "评测与基准 (evaluation & benchmark)",
    "应用与落地 (coding & web agents)",
]

_DEFAULT_QUERIES = [
    "LLM agent",
    "agentic workflow",
    "autonomous agent",
    "multi-agent system",
    "agent benchmark",
]


class PlannerAgent(Agent):
    name = "planner"

    def _translate(self, topic: str) -> tuple[list[str], list[str]]:
        """返回 (queries, subthemes)。命中词典用英文查询，否则按原文扩写。"""
        lower = topic.lower().strip()
        if not _is_cjk(lower):
            queries = [lower, f"{lower} survey", f"{lower} benchmark", f"{lower} review"]
            return self._dedupe(queries)[:6], [lower]

        matched: list[str] = []
        for key, values in _ZH_TERMS.items():
            if key in lower:
                matched.extend(values)

        matched = self._dedupe(matched)
        if matched:
            queries = list(matched)
            if len(queries) < 3:
                queries += [f"{queries[0]} survey", f"{queries[0]} benchmark"]
            return self._dedupe(queries)[:8], matched[:5]

        return self._dedupe(_DEFAULT_QUERIES), list(_DEFAULT_SUBTHEMES)

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            key = re.sub(r"\s+", " ", item.strip().lower())
            if key and key not in seen:
                seen.add(key)
                result.append(item.strip())
        return result

    def _fallback(self, topic: str) -> Plan:
        queries, subthemes = self._translate(topic)
        return Plan(topic=topic, subthemes=subthemes, queries=queries)

    async def run(self, topic: str, days: int | None = None) -> Plan:
        if not self.llm.enabled:
            plan = self._fallback(topic)
            self.log(mode="fallback", queries=plan.queries)
            return plan

        window = days or self.settings.default_days
        user = f"研究主题：{topic}\n时间范围：最近 {window} 天\n请给出检索计划。"
        try:
            data = await self.llm.json(_SYSTEM, user, temperature=0.3)
            plan = Plan(
                topic=topic,
                subthemes=[str(s) for s in data.get("subthemes", [])][:6],
                queries=[str(q) for q in data.get("queries", [])][:8],
                notes=str(data.get("notes", "")),
            )
            if len(plan.queries) < 2 or any(_is_cjk(q) for q in plan.queries):
                fallback = self._fallback(topic)
                plan.queries = fallback.queries
                plan.subthemes = plan.subthemes or fallback.subthemes
            self.log(mode="llm", queries=plan.queries)
            return plan
        except Exception as exc:
            self.log(mode="fallback", error=f"{type(exc).__name__}: {exc}")
            return self._fallback(topic)
