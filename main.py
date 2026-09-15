"""NBA 每日赛程播报 - 程序入口。

用法::

    python main.py                       # 播报今天（北京时间）的赛程
    python main.py --date 2026-01-15     # 播报指定日期（美东日期）
    python main.py --source espn         # 指定数据源
    python main.py --no-push             # 只打印，不推送微信
    python main.py --diagnose            # 逐个检测数据源是否可用
"""

from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.table import Table

from src.fetcher import SOURCE_ORDER, FetchError, diagnose, get_today_games, today_str
from src.formatter import NO_GAME_MESSAGE, build_wechat_content, format_games
from src.notifier import send_to_wechat


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NBA 每日赛程播报")
    parser.add_argument(
        "--date",
        help="查询日期，格式 YYYY-MM-DD（美东日期），默认取北京时间的今天",
    )
    parser.add_argument(
        "--source",
        choices=["auto", *SOURCE_ORDER],
        default="auto",
        help="数据源，默认 auto（依次尝试所有可用数据源）",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="只打印到终端，不做微信推送",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="逐个检测数据源可用性后退出（排查线上失败原因用）",
    )
    return parser.parse_args()


def print_to_terminal(formatted_games, date_label: str, source: str) -> None:
    """在终端打印漂亮的表格。"""
    console = Console()

    if isinstance(formatted_games, str):
        console.print(f"[yellow]{formatted_games}[/yellow]")
        return

    table = Table(
        title=f"🏀 {date_label} NBA 赛程（数据来源：{source}）",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("状态", style="cyan", justify="center")
    table.add_column("客队", style="green")
    table.add_column("比分", style="yellow", justify="center")
    table.add_column("主队", style="green")
    table.add_column("时间 (北京)", style="blue", justify="center")

    for game in formatted_games:
        table.add_row(
            game["status"], game["away"], game["score"],
            game["home"], game["time"],
        )

    console.print(table)


def run_diagnose(date: str | None) -> int:
    """打印各数据源的检测结果。"""
    console = Console()
    console.print("[bold]数据源诊断[/bold]")

    date_str, results = diagnose(date)
    table = Table(title=f"查询日期：{date_str}", header_style="bold magenta")
    table.add_column("数据源", style="cyan")
    table.add_column("结果", justify="center")
    table.add_column("说明", style="white")

    for label, ok, detail in results:
        table.add_row(label, "[green]可用[/green]" if ok else "[red]失败[/red]", detail)
    console.print(table)
    return 0


def main() -> int:
    args = parse_args()

    if args.diagnose:
        return run_diagnose(args.date)

    date_label = args.date or today_str()

    try:
        games, source = get_today_games(date=args.date, source=args.source)
    except FetchError as exc:
        Console().print(f"[red]抓取数据失败: {exc}[/red]")
        return 1

    formatted_games = format_games(games)
    print_to_terminal(formatted_games, date_label, source)

    if args.no_push:
        return 0

    send_to_wechat(
        build_wechat_content(games, date_label, source),
        title=f"{date_label} NBA 赛程播报",
        fallback_content=NO_GAME_MESSAGE,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
