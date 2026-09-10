"""数据处理与格式化模块。

把 :mod:`fetcher` 返回的标准化比赛数据转换成：
1. 终端表格需要的行数据；
2. 微信推送需要的 Markdown 文本。
"""

from __future__ import annotations

from typing import Any, Dict, List, Union

from src.fetcher import STATUS_FINAL, STATUS_LIVE

NO_GAME_MESSAGE = "今日无NBA比赛安排。"

Formatted = Union[str, List[Dict[str, Any]]]


def format_games(games) -> Formatted:
    """把比赛数据转换成易读的行数据。

    Args:
        games: :func:`src.fetcher.get_today_games` 返回的比赛列表。

    Returns:
        有比赛时返回字典列表；无比赛时返回提示字符串（保持与 ``main.py``
        中的分支一致）。
    """
    if not games:
        return NO_GAME_MESSAGE

    formatted: List[Dict[str, Any]] = []
    for game in games:
        status_id = game.get("status_id")
        if status_id in (STATUS_FINAL, STATUS_LIVE):
            score = f"{game.get('away_score', 0)} - {game.get('home_score', 0)}"
        else:
            score = "VS"

        formatted.append(
            {
                "status": game.get("status", "未开始"),
                "away": game.get("away", "?"),
                "score": score,
                "home": game.get("home", "?"),
                "time": game.get("time", "时间待定"),
            }
        )
    return formatted


def build_wechat_content(games, date_label: str, source: str = "") -> str:
    """生成推送到微信的 Markdown 文本。"""
    if not games:
        return f"🏀 **{date_label} NBA 赛程**\n\n{NO_GAME_MESSAGE}"

    lines = [f"🏀 **{date_label} NBA 赛程**", ""]
    for game in games:
        if game.get("status_id") in (STATUS_FINAL, STATUS_LIVE):
            score = f"{game.get('away_score', 0)} - {game.get('home_score', 0)}"
        else:
            score = "VS"
        lines.append(
            f"**{game.get('status', '未开始')}** | "
            f"{game.get('away', '?')} {score} {game.get('home', '?')} | "
            f"{game.get('time', '时间待定')}"
        )
        lines.append("")
    lines.append(f"> 数据来源：{source}" if source else "")
    return "\n".join(line for line in lines if line is not None).strip() + "\n"
