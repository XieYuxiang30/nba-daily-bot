"""微信推送模块（Server 酱）。

在 [sct.ftqq.com](https://sct.ftqq.com/) 微信扫码登录后获取 SendKey，
写入项目根目录的 ``.env`` 文件即可开启推送::

    SERVERCHAN_KEY=SCTxxxxxxxx

未配置 Key 时只会跳过推送，不会影响主流程。
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Union

import requests
from dotenv import load_dotenv

SERVERCHAN_URL = "https://sctapi.ftqq.com/{key}.send"

# Server 酱 SendKey 形如 SCT123456xxxx（Turbo 版）或 SCUxxxx（旧版），
# 只包含字母和数字；占位值/中文都会被判定为无效。
VALID_KEY = re.compile(r"^(SCT|SCU)[A-Za-z0-9]{8,}$")

# 加载 .env 文件（GitHub Actions 里由 secrets 注入环境变量，不受影响）
load_dotenv()


def _read_key() -> str:
    key = (os.getenv("SERVERCHAN_KEY") or "").strip()
    if not key or not VALID_KEY.match(key):
        return ""
    return key


def send_to_wechat(
    formatted_games: Union[str, List[Dict[str, Any]]],
    title: str = "今日NBA赛程播报",
    fallback_content: str = "",
) -> bool:
    """把赛程推送到微信。

    Args:
        formatted_games: :func:`src.formatter.format_games` 的结果，
            也可以是已经拼好的字符串。
        title: 推送标题。
        fallback_content: 当 ``formatted_games`` 为空时使用的文本。

    Returns:
        是否推送成功。
    """
    key = _read_key()
    if not key:
        print("未配置 SERVERCHAN_KEY（或仍是占位值），跳过微信推送。")
        return False

    if isinstance(formatted_games, str):
        content = formatted_games or fallback_content
    elif formatted_games:
        content = "\n\n".join(
            f"**{game['status']}** | {game['away']} {game['score']} {game['home']} | {game['time']}"
            for game in formatted_games
        )
    else:
        content = fallback_content

    if not content:
        content = "今日无NBA比赛安排。"

    try:
        response = requests.post(
            SERVERCHAN_URL.format(key=key),
            data={"title": title, "desp": content},
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001 - 推送失败不应中断主流程
        print(f"推送发生异常: {exc}")
        return False

    if response.status_code != 200:
        print(f"微信推送失败，状态码: {response.status_code}")
        return False

    try:
        payload = response.json()
    except ValueError:
        print("微信推送成功！")
        return True

    if payload.get("code") == 0:
        print("微信推送成功！")
        return True
    print(f"微信推送失败: {payload.get('message', payload)}")
    return False
