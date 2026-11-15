from __future__ import annotations

from agentpulse.agents.base import Agent
from agentpulse.models import Analysis, Critique, Paper, Synthesis

_SYSTEM = """你是论文综述的质量评审。
给定研究主题、精选论文、逐篇分析与已归纳的主题，请评估：
1. 主题是否覆盖主要论文，归纳是否准确；
2. 是否存在过度概括、引用不实或遗漏关键工作；
3. 综述是否具备可读性与研究参考价值。
只输出 JSON：{"score": 0-100 的整数, "issues": ["问题"], "strengths": ["优点"]}。
score 低于 60 表示不建议直接发布。"""

# 模型分与规则分的融合权重
_LLM_WEIGHT = 0.6
_RULE_WEIGHT = 0.4
_PASS_SCORE = 65.0


class CriticAgent(Agent):
    """发布前闸门：模型可用时做语义评分，不可用时降级为规则校验打分。"""

    name = "critic"

    async def run(
        self,
        papers: list[Paper],
        analyses: list[Analysis],
        synthesis: Synthesis,
        topic: str = "",
    ) -> Critique:
        rule_score, dimensions, rule_issues, coverage = self._rule_review(
            papers, analyses, synthesis
        )
        if not self.llm.enabled:
            return self._finish(
                rule_score, dimensions, rule_issues, [], "rule", coverage
            )

        review = await self._llm_review(papers, analyses, synthesis, topic)
        if review is None:
            return self._finish(
                rule_score, dimensions, rule_issues, [], "rule", coverage
            )

        llm_score, llm_issues, strengths = review
        score = round(llm_score * _LLM_WEIGHT + rule_score * _RULE_WEIGHT, 1)
        issues = list(rule_issues)
        for issue in llm_issues:
            if issue and issue not in issues:
                issues.append(issue)
        return self._finish(
            score,
            dimensions,
            rule_issues,
            issues,
            "llm",
            coverage,
            strengths=strengths,
        )

    def _rule_review(
        self,
        papers: list[Paper],
        analyses: list[Analysis],
        synthesis: Synthesis,
    ) -> tuple[float, dict[str, float], list[str], float]:
        valid = {p.uid for p in papers}
        issues: list[str] = []

        for theme in synthesis.themes:
            unknown = [uid for uid in theme.uids if uid not in valid]
            if unknown:
                issues.append(f"主题「{theme.name}」引用了不存在的 uid：{unknown[:3]}")

        if not synthesis.themes:
            issues.append("没有任何主题，报告无法成文")

        covered = {uid for theme in synthesis.themes for uid in theme.uids}
        coverage = len(covered) / max(len(papers), 1)
        if coverage < 0.6:
            issues.append(f"主题覆盖率过低：{coverage:.0%}（阈值 60%）")

        empty = [a.uid for a in analyses if not (a.problem or a.contribution)]
        if len(empty) > max(3, len(papers) // 2):
            issues.append(f"{len(empty)} 篇论文缺少有效分析内容，摘要信息可能不足")

        analysed = {a.uid for a in analyses}
        filled = len(analyses) - len(empty)
        completeness = filled / max(len(analyses), 1)
        theme_support = len(covered & analysed) / max(len(analysed), 1)

        # 覆盖 45 分、分析完整度 30 分、主题支撑度 25 分，再按问题数扣分
        base = coverage * 45 + completeness * 30 + theme_support * 25
        penalty = min(len(issues) * 8, 30)
        score = max(0.0, min(100.0, base - penalty))
        dimensions = {
            "coverage": round(coverage * 100, 1),
            "completeness": round(completeness * 100, 1),
            "theme_support": round(theme_support * 100, 1),
        }
        return score, dimensions, issues, coverage

    async def _llm_review(
        self,
        papers: list[Paper],
        analyses: list[Analysis],
        synthesis: Synthesis,
        topic: str,
    ) -> tuple[float, list[str], list[str]] | None:
        digest = self._digest(papers, analyses, synthesis, topic)
        try:
            data = await self.llm.json(_SYSTEM, digest, temperature=0.2)
            score = max(0.0, min(100.0, float(data.get("score", 0))))
            issues = [str(item).strip() for item in data.get("issues", []) if str(item).strip()]
            strengths = [
                str(item).strip() for item in data.get("strengths", []) if str(item).strip()
            ]
        except Exception as exc:  # 模型异常或预算耗尽时降级，不阻断发布
            self.log(mode="llm", fallback="rule", error=f"{type(exc).__name__}: {exc}"[:160])
            return None

        self.log(mode="llm", score=round(score, 1), issues=len(issues))
        return score, issues[:6], strengths[:5]

    def _digest(
        self,
        papers: list[Paper],
        analyses: list[Analysis],
        synthesis: Synthesis,
        topic: str,
    ) -> str:
        by_uid = {a.uid: a for a in analyses}
        lines = [
            f"研究主题：{topic or '未提供'}",
            f"精选论文 {len(papers)} 篇，主题 {len(synthesis.themes)} 个",
            "主题：",
        ]
        for theme in synthesis.themes[:8]:
            lines.append(
                f"- {theme.name}：{(theme.summary or '')[:120]}"
                f"（{len(theme.uids)} 篇，趋势：{(theme.trend or '未标注')[:40]}）"
            )

        lines.append("代表论文：")
        for paper in papers[:12]:
            analysis = by_uid.get(paper.uid)
            contribution = (analysis.contribution if analysis else "")[:100]
            lines.append(
                f"- [{paper.uid}] {paper.title[:100]}"
                f"｜贡献：{contribution or '未抽取'}"
            )

        empty = sum(1 for a in analyses if not (a.problem or a.contribution))
        lines.append(f"分析覆盖：{len(analyses)} 篇，其中 {empty} 篇内容为空")
        if synthesis.gaps:
            lines.append("已识别空白：" + "；".join(synthesis.gaps[:4]))
        return "\n".join(lines)

    def _finish(
        self,
        score: float,
        dimensions: dict[str, float],
        rule_issues: list[str],
        issues: list[str],
        mode: str,
        coverage: float,
        strengths: list[str] | None = None,
    ) -> Critique:
        # 硬性问题（引用不存在的 uid、无主题）仍然一票否决；模型分只参与总分
        blocking = [
            issue
            for issue in rule_issues
            if "不存在的 uid" in issue or "没有任何主题" in issue
        ]
        ok = not blocking and score >= _PASS_SCORE
        critique = Critique(
            ok=ok,
            issues=issues,
            score=round(score, 1),
            mode=mode,
            dimensions=dimensions,
            strengths=strengths or [],
        )
        self.log(
            ok=ok,
            score=critique.score,
            mode=mode,
            coverage=round(coverage, 3),
            issues=len(issues),
        )
        return critique
