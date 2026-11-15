from __future__ import annotations

from agentpulse.models import Analysis, Critique, Paper, Plan, RunMeta, Synthesis


def _cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def _paper_card(paper: Paper, analysis: Analysis | None) -> list[str]:
    title = paper.title or paper.uid
    lines = [f"#### [{title}]({paper.url})" if paper.url else f"#### {title}"]
    meta = [
        f"`{paper.uid}`",
        paper.venue or paper.source,
        paper.published.isoformat() if paper.published else "日期未知",
        f"引用 {paper.citation_count}" if paper.citation_count else "",
        f"热度 {paper.heat:.0f}" if paper.heat else "",
        f"相关度 {paper.score:.2f}",
    ]
    lines.append(" · ".join(m for m in meta if m))
    if analysis:
        for label, value in (
            ("问题", analysis.problem),
            ("方法", analysis.method),
            ("贡献", analysis.contribution),
            ("局限", analysis.limitations),
            ("可复用", analysis.reusable),
        ):
            if value:
                lines.append(f"- **{label}**：{value}")
        if analysis.keywords:
            lines.append(f"- **关键词**：{', '.join(analysis.keywords)}")
    lines.append("")
    return lines


def render_report(
    meta: RunMeta,
    plan: Plan,
    papers: list[Paper],
    analyses: list[Analysis],
    synthesis: Synthesis,
    critique: Critique,
    fetch_errors: list[str],
) -> str:
    by_uid = {a.uid: a for a in analyses}
    paper_by_uid = {p.uid: p for p in papers}

    out: list[str] = []
    out.append(f"# {meta.topic} · 热点与论文汇总")
    out.append("")
    out.append(
        f"> 时间窗口：{meta.window_start.isoformat()} ~ {meta.window_end.isoformat()} ｜ "
        f"数据源：{', '.join(meta.sources)} ｜ "
        f"抓取 {meta.fetched} 篇，精选 {meta.kept} 篇 ｜ "
        f"学科门类：{meta.field or '全部'} ｜ "
        f"生成方式：{'LLM 分析' if meta.llm_used else '规则分析（无 LLM）'}"
    )
    out.append("")

    out.append("## 一、TL;DR")
    out.append("")
    for theme in synthesis.themes[:4]:
        if theme.summary:
            out.append(f"- **{theme.name}**：{theme.summary}")
    if not synthesis.themes:
        out.append("- 本轮未形成有效主题聚类，请查看下方论文清单。")
    out.append("")

    if synthesis.must_read:
        out.append("**必读推荐**：" + "、".join(
            f"《{paper_by_uid[u].title}》" for u in synthesis.must_read if u in paper_by_uid
        ))
        out.append("")

    out.append("## 二、学科门类分布")
    out.append("")
    out.append("| 学科门类 | 论文数 | 占比 |")
    out.append("|---|---|---|")
    total = max(len(papers), 1)
    for field in synthesis.fields or []:
        out.append(
            f"| {_cell(field.name)} | {len(field.uids)} | {len(field.uids) / total:.0%} |"
        )
    if not synthesis.fields:
        out.append("| 其他 | 0 | 0% |")
    out.append("")

    out.append("## 三、主题地图")
    out.append("")
    out.append("| 主题 | 论文数 | 趋势判断 |")
    out.append("|---|---|---|")
    for theme in synthesis.themes:
        out.append(f"| {_cell(theme.name)} | {len(theme.uids)} | {_cell(theme.trend) or '—'} |")
    out.append("")

    out.append("## 四、分主题详读")
    out.append("")
    for index, theme in enumerate(synthesis.themes, start=1):
        out.append(f"### 4.{index} {theme.name}")
        out.append("")
        if theme.summary:
            out.append(theme.summary)
            out.append("")
        if theme.trend:
            out.append(f"**趋势**：{theme.trend}")
            out.append("")
        for uid in theme.uids:
            paper = paper_by_uid.get(uid)
            if paper is None:
                continue
            out.extend(_paper_card(paper, by_uid.get(uid)))

    out.append("## 五、趋势判断")
    out.append("")
    for trend in synthesis.trends or ["本轮数据不足以给出稳定趋势判断。"]:
        out.append(f"- {trend}")
    out.append("")

    out.append("## 六、研究空白与机会")
    out.append("")
    for gap in synthesis.gaps or ["本轮未识别出明确空白（无 LLM 模式下不做推断）。"]:
        out.append(f"- {gap}")
    out.append("")

    out.append("## 七、论文清单")
    out.append("")
    out.append("| # | 标题 | 来源 | 日期 | 引用 | 相关度 |")
    out.append("|---|---|---|---|---|---|")
    for index, paper in enumerate(papers, start=1):
        link = f"[{_cell(paper.title)}]({paper.url})" if paper.url else _cell(paper.title)
        out.append(
            f"| {index} | {link} | {paper.source} | "
            f"{paper.published.isoformat() if paper.published else '—'} | "
            f"{paper.citation_count or '—'} | {paper.score:.2f} |"
        )
    out.append("")

    out.append("## 八、检索过程与方法")
    out.append("")
    out.append(f"- 检索式：{'；'.join(plan.queries)}")
    out.append(f"- 子主题假设：{'、'.join(plan.subthemes) or '—'}")
    if plan.notes:
        out.append(f"- 规划说明：{plan.notes}")
    out.append(
        "- 排序规则：关键词覆盖 45% + 新鲜度 30% + 引用量 15% + 社区热度 10%，按来源加权后截断"
    )
    out.append(
        "- 局限：仅基于标题与摘要，未读全文；引用数为上游快照，可能滞后；"
        "无 LLM 模式下的主题聚类为关键词贪心归并，粒度较粗。"
    )
    if fetch_errors:
        out.append("- 抓取降级记录：")
        for error in fetch_errors[:10]:
            out.append(f"  - {error}")
    if not critique.ok:
        out.append("- 质量闸门提示：")
        for issue in critique.issues:
            out.append(f"  - {issue}")
    out.append("")
    out.append(f"*生成时间：{meta.generated_at.isoformat(timespec='seconds')}*")
    out.append("")
    return "\n".join(out)
