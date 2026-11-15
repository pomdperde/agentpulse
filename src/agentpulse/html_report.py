from __future__ import annotations

import html
from collections import Counter

from agentpulse.models import Analysis, Critique, Paper, Plan, RunMeta, Synthesis

_CSS = """
:root{--bg:#0b1020;--panel:#131a2e;--line:#243052;--text:#e6ebff;--muted:#93a0c4;
--accent:#5b8cff;--accent2:#38d6b0;--warn:#ffb454;--danger:#ff6b81}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.7 "Segoe UI","PingFang SC","Microsoft YaHei",system-ui,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:48px 24px 80px}
header{border-bottom:1px solid var(--line);padding-bottom:28px;margin-bottom:32px}
h1{font-size:30px;margin:0 0 10px;letter-spacing:.5px}
.sub{color:var(--muted);font-size:14px}
.chips{margin-top:14px;display:flex;flex-wrap:wrap;gap:8px}
.chip{background:#1b2440;border:1px solid var(--line);color:#c8d3f5;
padding:4px 10px;border-radius:999px;font-size:12px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin:28px 0}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
.kpi b{display:block;font-size:26px;color:var(--accent);line-height:1.2}
.kpi span{color:var(--muted);font-size:12px}
h2{font-size:20px;margin:44px 0 16px;padding-left:10px;border-left:3px solid var(--accent)}
h3{font-size:16px;margin:26px 0 10px;color:#cfd9ff}
.bars{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px 20px}
.bar-row{display:grid;grid-template-columns:220px 1fr 46px;gap:12px;align-items:center;margin:10px 0}
.bar-track{background:#1a2340;border-radius:999px;height:9px;overflow:hidden}
.bar-fill{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2))}
.bar-num{text-align:right;color:var(--muted);font-size:13px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:20px 22px;margin:14px 0}
.card h4{margin:0 0 6px;font-size:16px}
.card h4 a{color:#cfe0ff;text-decoration:none}
.card h4 a:hover{color:var(--accent)}
.meta{color:var(--muted);font-size:12px;margin-bottom:10px}
.meta code{background:#0e1526;border:1px solid var(--line);border-radius:6px;padding:1px 6px}
.fields{margin:0;padding:0;list-style:none}
.fields li{margin:6px 0;font-size:14px}
.fields b{color:#9fb6ff;font-weight:600}
details{margin-top:10px}
summary{cursor:pointer;color:var(--muted);font-size:13px}
.abstract{color:#b9c5e6;font-size:13.5px;background:#0e1526;border:1px solid var(--line);
border-radius:10px;padding:12px 14px;margin-top:10px}
table{width:100%;border-collapse:collapse;font-size:13px;background:var(--panel);
border:1px solid var(--line);border-radius:14px;overflow:hidden}
th,td{padding:9px 12px;text-align:left;border-bottom:1px solid var(--line)}
th{color:#a9b8e0;background:#16203a;font-weight:600}
tr:last-child td{border-bottom:none}
td a{color:#cfe0ff;text-decoration:none}
.tag{display:inline-block;background:#1b2b4d;color:#9fc1ff;border-radius:6px;
padding:1px 7px;font-size:11px;margin-right:6px}
.note{background:#1a1f33;border:1px solid #3a3050;color:#d9c9ff;border-radius:12px;
padding:14px 18px;font-size:13.5px}
.warn{border-color:#4a3a1a;background:#241d10;color:#ffd79a}
ul.plain{padding-left:20px;color:#c5d0ee}
footer{margin-top:56px;color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:18px}
"""


def _e(text: str) -> str:
    return html.escape(text or "")


def _link(paper: Paper) -> str:
    title = _e(paper.title or paper.uid)
    return f'<a href="{_e(paper.url)}" target="_blank" rel="noopener">{title}</a>' if paper.url else title


def _paper_card(paper: Paper, analysis: Analysis | None) -> str:
    meta = [
        f"<code>{_e(paper.uid)}</code>",
        _e(paper.venue or paper.source),
        paper.published.isoformat() if paper.published else "日期未知",
        f"引用 {paper.citation_count}" if paper.citation_count else "",
        f"相关度 {paper.score:.2f}",
    ]
    meta_html = " · ".join(m for m in meta if m)
    fields = ""
    if analysis:
        rows = [
            ("问题", analysis.problem),
            ("方法", analysis.method),
            ("贡献", analysis.contribution),
            ("局限", analysis.limitations),
            ("可复用", analysis.reusable),
        ]
        items = "".join(f"<li><b>{label}</b>：{_e(value)}</li>" for label, value in rows if value)
        tags = "".join(f'<span class="tag">{_e(k)}</span>' for k in analysis.keywords[:5])
        fields = f'<ul class="fields">{items}</ul><div>{tags}</div>'
    abstract = paper.abstract[:900] + ("…" if len(paper.abstract) > 900 else "")
    return f"""<div class="card">
<h4>{_link(paper)}</h4>
<div class="meta">{meta_html}</div>
{fields}
<details><summary>展开摘要</summary><div class="abstract">{_e(abstract)}</div></details>
</div>"""


def _month_histogram(papers: list[Paper]) -> list[tuple[str, int]]:
    counter = Counter(
        paper.published.strftime("%Y-%m") for paper in papers if paper.published
    )
    return sorted(counter.items())[-10:]


def render_html(
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

    kpis = [
        (f"{meta.kept}", "精选论文"),
        (f"{len(synthesis.themes)}", "热点主题"),
        (f"{len({p.source for p in papers})}", "数据源"),
        (f"{meta.days}d", "回溯窗口"),
    ]
    kpi_html = "".join(
        f'<div class="kpi"><b>{value}</b><span>{label}</span></div>' for value, label in kpis
    )

    theme_counts = [(t.name, len(t.uids)) for t in synthesis.themes]
    max_count = max([c for _, c in theme_counts] or [1])
    theme_bars = "".join(
        f'<div class="bar-row"><div>{_e(name)}</div>'
        f'<div class="bar-track"><div class="bar-fill" style="width:{count / max_count * 100:.0f}%"></div></div>'
        f'<div class="bar-num">{count}</div></div>'
        for name, count in theme_counts
    ) or '<div class="sub">暂无主题数据</div>'

    field_counts = [(f.name, len(f.uids)) for f in synthesis.fields]
    field_max = max([c for _, c in field_counts] or [1])
    field_bars = "".join(
        f'<div class="bar-row"><div>{_e(name)}</div>'
        f'<div class="bar-track"><div class="bar-fill" style="width:{count / field_max * 100:.0f}%"></div></div>'
        f'<div class="bar-num">{count}</div></div>'
        for name, count in field_counts
    ) or '<div class="sub">暂无学科数据</div>'

    months = _month_histogram(papers)
    month_max = max([c for _, c in months] or [1])
    month_bars = "".join(
        f'<div class="bar-row"><div>{_e(month)}</div>'
        f'<div class="bar-track"><div class="bar-fill" style="width:{count / month_max * 100:.0f}%"></div></div>'
        f'<div class="bar-num">{count}</div></div>'
        for month, count in months
    ) or '<div class="sub">暂无日期数据</div>'

    must_read = "".join(
        f"<li>{_link(paper_by_uid[uid])} — {_e(paper_by_uid[uid].venue or paper_by_uid[uid].source)}"
        f"，相关度 {paper_by_uid[uid].score:.2f}</li>"
        for uid in synthesis.must_read
        if uid in paper_by_uid
    ) or '<li class="sub">暂无推荐</li>'

    theme_sections = []
    for index, theme in enumerate(synthesis.themes, start=1):
        cards = "".join(
            _paper_card(paper_by_uid[uid], by_uid.get(uid))
            for uid in theme.uids
            if uid in paper_by_uid
        )
        summary = f"<p>{_e(theme.summary)}</p>" if theme.summary else ""
        trend = f'<div class="meta">趋势：{_e(theme.trend)}</div>' if theme.trend else ""
        theme_sections.append(
            f"<h3>{index}. {_e(theme.name)}（{len(theme.uids)} 篇）</h3>{summary}{trend}{cards}"
        )
    themes_html = "".join(theme_sections) or '<div class="note">本轮未形成主题聚类。</div>'

    rows = "".join(
        f"<tr><td>{i}</td><td>{_link(p)}</td><td>{_e(p.source)}</td>"
        f"<td>{p.published.isoformat() if p.published else '—'}</td>"
        f"<td>{p.citation_count or '—'}</td><td>{p.score:.2f}</td></tr>"
        for i, p in enumerate(papers, start=1)
    )

    trends = "".join(f"<li>{_e(t)}</li>" for t in synthesis.trends)
    gaps = "".join(f"<li>{_e(g)}</li>" for g in synthesis.gaps)
    if not gaps:
        gaps = '<li class="sub">本轮未识别出明确空白。</li>'

    issues = "".join(f"<li>{_e(i)}</li>" for i in critique.issues)
    quality = (
        f'<div class="note warn"><b>质量闸门提示</b><ul class="plain">{issues}</ul></div>'
        if issues
        else '<div class="note">质量闸门：通过（引用完整、主题覆盖率达标）。</div>'
    )

    offline_note = (
        '<div class="note warn">当前报告使用离线演示语料，不代表实时热点。</div>'
        if meta.offline
        else ""
    )

    errors = "".join(f"<li>{_e(e)}</li>" for e in fetch_errors[:8])
    error_block = f'<ul class="plain">{errors}</ul>' if errors else "<p class=\"sub\">无抓取降级记录。</p>"

    source_line = "、".join(meta.sources)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(meta.topic)} · AgentPulse 论文汇总</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
<header>
<h1>{_e(meta.topic)} · 热点与论文汇总</h1>
<div class="sub">{meta.window_start.isoformat()} ~ {meta.window_end.isoformat()} ·
数据源 {_e(source_line)} · 抓取 {meta.fetched} 篇 / 精选 {meta.kept} 篇 ·
门类 {_e(meta.field or '全部')} ·
{'LLM 分析' if meta.llm_used else '规则分析'}</div>
<div class="chips">{''.join(f'<span class="chip">{_e(q)}</span>' for q in plan.queries)}</div>
</header>
{offline_note}
<div class="kpis">{kpi_html}</div>

<h2>学科门类分布</h2>
<div class="bars">{field_bars}</div>

<h2>热点主题分布</h2>
<div class="bars">{theme_bars}</div>

<h2>时间分布</h2>
<div class="bars">{month_bars}</div>

<h2>必读推荐</h2>
<ul class="plain">{must_read}</ul>

<h2>趋势判断</h2>
<ul class="plain">{trends or '<li class="sub">数据不足以给出趋势判断。</li>'}</ul>

<h2>研究空白与机会</h2>
<ul class="plain">{gaps}</ul>

<h2>分主题详读</h2>
{themes_html}

<h2>论文清单</h2>
<table><thead><tr><th>#</th><th>标题</th><th>来源</th><th>日期</th><th>引用</th><th>相关度</th></tr></thead>
<tbody>{rows}</tbody></table>

<h2>质量校验</h2>
{quality}

<h2>方法与局限</h2>
<p class="sub">排序规则：关键词覆盖 45% + 新鲜度 30% + 引用 15% + 社区热度 10%。
仅基于标题与摘要，未读全文；引用数为上游快照，可能滞后。</p>
{error_block}
<div class="sub">子主题假设：{_e('、'.join(plan.subthemes) or '—')}</div>

<footer>AgentPulse · 生成于 {meta.generated_at.isoformat(timespec='seconds')} ·
本报告由多 Agent 流水线自动生成</footer>
</div></body></html>"""
