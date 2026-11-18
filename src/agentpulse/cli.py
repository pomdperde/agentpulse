from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from agentpulse.pipeline.cleanup import clean
from agentpulse.core.config import Settings
from agentpulse.infra.llm import BudgetExceeded
from agentpulse.pipeline.orchestrator import ALL_SOURCES, Pipeline
from agentpulse.core.settings_store import effective_settings
from agentpulse.core.taxonomy import (
    FIELDS,
    children_of,
    find_node,
    find_subfield,
    subfields_of,
)


def _parse_sources(value: str) -> list[str]:
    items = [s.strip() for s in value.split(",") if s.strip()]
    unknown = [s for s in items if s not in ALL_SOURCES]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"未知数据源 {unknown}，可选：{', '.join(ALL_SOURCES)}"
        )
    return items


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentpulse",
        description="多 Agent 的 Agent 领域热点检索与论文汇总系统",
    )
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="执行一轮检索并生成报告")
    run.add_argument(
        "-t", "--topic", action="append",
        help="检索主题，可重复传入：-t \"LLM agent\" -t \"CUDA\"，按顺序依次检索",
    )
    run.add_argument("--days", type=int, default=None, help="时间窗口天数")
    run.add_argument("--max-papers", type=int, default=None, help="精选论文条数上限")
    run.add_argument(
        "--sources",
        type=_parse_sources,
        default=list(ALL_SOURCES),
        help=f"逗号分隔的数据源，可选 {','.join(ALL_SOURCES)}",
    )
    run.add_argument("--out", default="reports", help="输出根目录（默认 ./reports）")
    run.add_argument(
        "--from-json",
        type=Path,
        default=None,
        help="跳过抓取，直接读取候选论文 JSON",
    )
    run.add_argument("--offline", action="store_true", help="使用内置演示语料，不访问网络")
    run.add_argument("--no-llm", action="store_true", help="不调用模型，走规则路径")
    run.add_argument(
        "--ieee",
        action="store_true",
        help="只保留 IEEE 出版物（DOI 前缀 10.1109，元数据来自 OpenAlex/Crossref）",
    )
    run.add_argument(
        "--venue",
        default=None,
        help="只保留指定期刊/会议的论文，如 Nature、CVPR、Journal of Finance",
    )
    run.add_argument(
        "--field",
        choices=list(FIELDS),
        default=None,
        help="只保留指定学科门类的论文（Google Scholar Metrics 式一级分类）",
    )
    run.add_argument(
        "--subfield",
        default=None,
        help="在所选门类下进一步限定二级学科，可用值见 --list-subfields",
    )
    run.add_argument(
        "--subfield3",
        default=None,
        help="在所选二级学科下进一步限定三级学科",
    )
    run.add_argument(
        "--list-subfields",
        action="store_true",
        help="列出指定门类的二级、三级学科与热点主题后退出",
    )

    serve = sub.add_parser("serve", help="启动 Web 面板")
    serve.add_argument("--port", type=int, default=8123, help="监听端口（默认 8123）")
    serve.add_argument("--host", default="127.0.0.1", help="监听地址")

    clean_cmd = sub.add_parser("clean", help="清理历史论文报告，释放磁盘空间")
    clean_cmd.add_argument("--out", default="reports", help="报告根目录（默认 ./reports）")
    clean_cmd.add_argument(
        "--keep", type=int, default=20, help="保留最近 N 轮报告（默认 20，0 表示全删）"
    )
    clean_cmd.add_argument(
        "--days", type=int, default=None, help="额外删除超过 N 天的报告"
    )
    clean_cmd.add_argument("--dry-run", action="store_true", help="只统计不删除")

    return parser


def _run(args: argparse.Namespace) -> int:
    if getattr(args, "list_subfields", False):
        if not args.field:
            print("--list-subfields 需要配合 --field 使用", file=sys.stderr)
            return 2
        nodes = subfields_of(args.field)
        for node in nodes:
            labels = " / ".join(label for label, _ in node.hotspots)
            print(f"- {node.name}: {labels}")
            for child in children_of(args.field, node.name):
                child_labels = " / ".join(label for label, _ in child.hotspots)
                print(f"    - {child.name}: {child_labels}")
        print(
            f"\n共 {len(nodes)} 个二级学科 / "
            f"{sum(len(children_of(args.field, n.name)) for n in nodes)} 个三级学科"
        )
        return 0

    if args.subfield:
        if not args.field:
            print("--subfield 需要配合 --field 使用", file=sys.stderr)
            return 2
        if find_subfield(args.field, args.subfield) is None:
            valid = "、".join(node.name for node in subfields_of(args.field))
            print(f"未知二级学科：{args.subfield}；可用：{valid}", file=sys.stderr)
            return 2

    if args.subfield3:
        if not args.field or not args.subfield:
            print("--subfield3 需要配合 --field 与 --subfield 使用", file=sys.stderr)
            return 2
        if find_node(args.field, (args.subfield, args.subfield3)) is None:
            valid = "、".join(
                node.name for node in children_of(args.field, args.subfield)
            )
            print(f"未知三级学科：{args.subfield3}；可用：{valid}", file=sys.stderr)
            return 2

    settings = effective_settings()
    days = args.days or settings.default_days
    max_papers = args.max_papers or settings.max_papers

    pipeline = Pipeline(
        settings,
        Path(args.out),
        no_llm=args.no_llm,
        offline=args.offline,
    )
    try:
        run_dir = asyncio.run(
            pipeline.run(
                topics=args.topic or ["LLM agent"],
                days=days,
                sources=args.sources,
                max_papers=max_papers,
                from_json=args.from_json,
                field=args.field,
                subfield=args.subfield,
                subfield3=args.subfield3,
                ieee_only=args.ieee,
                venue=args.venue,
            )
        )
    except BudgetExceeded as exc:
        print(f"[中止] {exc}\n建议：调大 TOKEN_BUDGET，或加 --no-llm 走规则模式。", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"[失败] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    critique = json.loads((run_dir / "critique.json").read_text(encoding="utf-8"))
    synthesis = json.loads((run_dir / "synthesis.json").read_text(encoding="utf-8"))
    print(f"报告：{run_dir / 'report.md'}")
    print(f"HTML：{run_dir / 'report.html'}")
    print(
        f"主题数：{len(synthesis.get('themes', []))}｜"
        f"质量闸门：{'通过' if critique.get('ok') else '有提示'}"
    )
    print(f"产物目录：{run_dir}")
    return 0


def _clean(args: argparse.Namespace) -> int:
    root = Path(args.out)
    if not root.is_dir():
        print(f"报告目录不存在：{root}", file=sys.stderr)
        return 1
    if args.keep < 0:
        print("--keep 不能为负数", file=sys.stderr)
        return 2

    result = clean(root, keep=args.keep, days=args.days, dry_run=args.dry_run)
    if not result.removed:
        print(f"没有需要清理的报告，{len(result.kept)} 个报告全部保留")
        return 0

    verb = "可清理" if result.dry_run else "已清理"
    print(
        f"{verb} {len(result.removed)} 个报告，"
        f"释放 {result.freed_bytes / 1024 / 1024:.1f} MB，"
        f"保留 {len(result.kept)} 个"
    )
    for path in result.removed[:10]:
        print(f"  - {path.name}")
    if len(result.removed) > 10:
        print(f"  …… 其余 {len(result.removed) - 10} 个")
    if result.dry_run:
        print("（dry-run：未实际删除，去掉 --dry-run 执行）")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "serve":
        from agentpulse.api.web import serve

        serve(port=args.port, host=args.host)
        return 0
    if args.command == "run":
        return _run(args)
    if args.command == "clean":
        return _clean(args)

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
