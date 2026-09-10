"""NBA 每日赛程播报 - 程序入口。

用法::

    python main.py                       # 播报今天（北京时间）的赛程
    python main.py --date 2026-01-15     # 播报指定日期
    python main.py --source espn         # 指定数据源
    python main.py --no-push             # 只打印，不推送微信
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from rich.console import Console
from rich.table import Table

from src.fetcher import BEIJING_TZ, FetchError, get_today_games
from src.formatter import NO_GAME_MESSAGE, build_wechat_content, format_games
from src.notifier import send_to_wechat

NO_GAME_PREFIX = "今日无NBA比赛安排"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NBA 每日赛程播报")
    parser.add_argument(
        "--date",
        help="查询日期，格式 YYYY-MM-DD（美东日期），默认取北京时间的今天",
    )
    parser.add_argument(
        "--source",
        choices=["auto", "nba_api", "espn"],
        default="auto",
        help="数据源，默认 auto（nba_api 优先，失败自动回退 ESPN）",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="只打印到终端，不做微信推送",
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


def main() -> int:
    args = parse_args()
    date_label = args.date or datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")

    try:
        games, source = get_today_games(date=args.date, source=args.source)
    except FetchError as exc:
        Console().print(f"[red]抓取数据失败: {exc}[/red]")
        return 1

    formatted_games = format_games(games)
    print_to_terminal(formatted_games, date_label, source)

    if args.no_push:
        return 0

    content = build_wechat_content(games, date_label, source)
    send_to_wechat(
        formatted_games,
        title=f"{date_label} NBA 赛程播报",
        fallback_content=NO_GAME_MESSAGE,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
