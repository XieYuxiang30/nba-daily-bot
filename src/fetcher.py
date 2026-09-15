"""数据抓取模块。

数据源（按 ``auto`` 顺序尝试）：

1. ``nba_api``：NBA 官方 stats 接口（ScoreboardV3 → ScoreboardV2）
2. ``espn``：ESPN 公开 JSON 接口
3. ``nba_cdn``：NBA 官网 CDN 的当日记分牌 JSON（仅限当天）

不同数据源结构各异，这里统一转换成「标准化比赛字典」后再交给
:mod:`formatter` 处理，避免上层逻辑与数据源耦合。
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

try:  # nba_api 依赖较重，导入失败时仍可使用其它数据源
    from nba_api.stats.endpoints import scoreboardv2, scoreboardv3
except Exception:  # pragma: no cover - 仅在缺少依赖时触发
    scoreboardv2 = None
    scoreboardv3 = None

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

BEIJING_TZ = ZoneInfo("Asia/Shanghai")
EASTERN_TZ = ZoneInfo("America/New_York")

ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
)
NBA_CDN_SCOREBOARD_URL = (
    "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"
)

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
}

NBA_API_TIMEOUT = 12  # 官方接口在部分网络下会超时，控制单次等待时间
HTTP_CONNECT_TIMEOUT = 8
HTTP_READ_TIMEOUT = 20
HTTP_RETRIES = 2

# 排序用的「空时间」哨兵，必须带时区，否则与 aware datetime 比较会抛 TypeError
_FAR_FUTURE = datetime.max.replace(tzinfo=timezone.utc, microsecond=0)

STATUS_PENDING = 1  # 未开始
STATUS_LIVE = 2  # 进行中
STATUS_FINAL = 3  # 已结束

STATUS_TEXT = {
    STATUS_PENDING: "未开始",
    STATUS_LIVE: "进行中",
    STATUS_FINAL: "已结束",
}

Game = Dict[str, Any]


class FetchError(Exception):
    """数据源不可用 / 解析失败时抛出。"""


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------


def _norm(name: Any) -> str:
    """列名归一化：转小写并去掉下划线，兼容 ``HOME_TEAM_ID`` / ``homeTeamId``。"""
    return str(name).lower().replace("_", "")


def _rows(dataset) -> List[Dict[str, Any]]:
    """把 nba_api 的 DataSet 转成「归一化列名 -> 值」的字典列表。"""
    payload = dataset.get_dict()
    headers = [_norm(h) for h in payload.get("headers", [])]
    return [dict(zip(headers, row)) for row in payload.get("data", [])]


def _pick(row: Dict[str, Any], *names: str, default: Any = None) -> Any:
    """按候选列名取值，兼容不同版本接口的命名差异。"""
    for name in names:
        key = _norm(name)
        if key in row and row[key] not in (None, ""):
            return row[key]
    return default


def _to_beijing(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BEIJING_TZ)


def _parse_iso(value: Any) -> Optional[datetime]:
    """解析 ISO8601 时间字符串（支持 ``Z`` 结尾）。"""
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_clock_text(text: str) -> Optional[Tuple[int, int]]:
    """从 ``7:30 pm ET`` 这类文本里解析 ``(hour, minute)``（24 小时制）。"""
    if not text:
        return None
    match = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)", str(text), re.IGNORECASE)
    if not match:
        return None
    hour = int(match.group(1)) % 12
    minute = int(match.group(2))
    if match.group(3).lower() == "pm":
        hour += 12
    return hour, minute


def _beijing_label(dt: Optional[datetime]) -> str:
    return "时间待定" if dt is None else dt.strftime("%m-%d %H:%M")


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sort_games(games: List[Game]) -> List[Game]:
    """按开赛时间排序；没有时间的比赛排在最后。"""
    return sorted(games, key=lambda g: g["tip_off"] or _FAR_FUTURE)


def _http_get_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    retries: int = HTTP_RETRIES,
) -> Any:
    """带重试的 JSON GET 请求，失败时抛出带状态码的 :class:`FetchError`。"""
    last_error = ""
    for attempt in range(retries + 1):
        try:
            response = requests.get(
                url,
                params=params,
                headers=HTTP_HEADERS,
                timeout=(HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT),
            )
        except Exception as exc:  # noqa: BLE001 - 网络异常统一处理
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    last_error = (
                        f"返回内容不是 JSON（{exc}）：{response.text[:120]!r}"
                    )
            else:
                last_error = (
                    f"HTTP {response.status_code}：{response.text[:120]!r}"
                )
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise FetchError(last_error or "请求失败")


# ---------------------------------------------------------------------------
# 比赛数据组装
# ---------------------------------------------------------------------------


def _team_name(team_row: Dict[str, Any]) -> str:
    city = _pick(team_row, "teamcity", "team_city_name", default="")
    name = _pick(team_row, "teamname", "team_nickname", default="")
    return " ".join(part for part in (city, name) if part).strip() or "未知球队"


def _build_game(
    game_id: str,
    status_id: int,
    status_detail: str,
    away_abbr: str,
    away_name: str,
    away_score: Optional[int],
    home_abbr: str,
    home_name: str,
    home_score: Optional[int],
    tip_off: Optional[datetime],
) -> Game:
    return {
        "game_id": game_id,
        "status_id": status_id,
        "status": STATUS_TEXT[status_id],
        "detail": status_detail,
        "away": away_abbr or "?",
        "away_name": away_name,
        "away_score": away_score or 0,
        "home": home_abbr or "?",
        "home_name": home_name,
        "home_score": home_score or 0,
        "tip_off": tip_off,
        "time": _beijing_label(tip_off),
    }


def _game_time_from_nba(row: Dict[str, Any]) -> Optional[datetime]:
    """解析 NBA 官方接口的开赛时间（统一转成北京时间）。"""
    utc_dt = _parse_iso(_pick(row, "gametimeutc", "game_time_utc"))
    if utc_dt:
        return _to_beijing(utc_dt)

    # ScoreboardV2 没有 UTC 字段，只有美东时间的文本，例如 "7:30 pm ET"
    clock = _parse_clock_text(_pick(row, "gamestatustext", "game_status_text"))
    if not clock:
        return None

    date_part = _pick(row, "gamedateest", "game_date_est", "gamedate", "game_date")
    if not isinstance(date_part, str) or not date_part:
        return None
    try:
        base = datetime.strptime(date_part[:10], "%Y-%m-%d")
    except ValueError:
        return None

    eastern = base.replace(hour=clock[0], minute=clock[1], tzinfo=EASTERN_TZ)
    return eastern.astimezone(BEIJING_TZ)


# ---------------------------------------------------------------------------
# 数据源一：NBA 官方接口（nba_api）
# ---------------------------------------------------------------------------


def _games_from_scoreboard_v3(game_header, line_score) -> List[Game]:
    """解析 ScoreboardV3。

    V3 的 GameHeader 里没有主客队 ID，但 ``gameCode`` 形如 ``20260115/MEMORL``
    （客队在前、主队在结尾），因此用真实球队缩写去比对判断主客场。
    """
    games: List[Game] = []
    by_game: Dict[str, List[Dict[str, Any]]] = {}
    for line in _rows(line_score):
        by_game.setdefault(str(_pick(line, "gameid", default="")), []).append(line)

    for row in _rows(game_header):
        team_rows = by_game.get(str(_pick(row, "gameid", default="")), [])
        if not team_rows:
            continue

        game_code = str(_pick(row, "gamecode", default=""))
        codes = [
            str(_pick(team_row, "teamtricode", "teamabbreviation", default=""))
            for team_row in team_rows
        ]
        home_code = away_code = None
        for index, code in enumerate(codes):
            other = codes[1 - index] if len(codes) > 1 else ""
            if (
                code
                and other
                and game_code.endswith(code)
                and game_code[: -len(code)].endswith(other)
            ):
                home_code, away_code = code, other
                break

        away_row = home_row = None
        for team_row in team_rows:
            tricode = str(_pick(team_row, "teamtricode", "teamabbreviation", default=""))
            if home_code and tricode == home_code:
                home_row = team_row
            elif away_code and tricode == away_code:
                away_row = team_row
        if away_row is None or home_row is None:
            # 兜底：按返回顺序，第一条为客队
            away_row, home_row = team_rows[0], team_rows[-1]

        status_id = _int_or_none(_pick(row, "gamestatus", "game_status_id")) or STATUS_PENDING
        status_id = status_id if status_id in STATUS_TEXT else STATUS_PENDING

        games.append(
            _build_game(
                game_id=str(_pick(row, "gameid", default="")),
                status_id=status_id,
                status_detail=str(_pick(row, "gamestatustext", "game_status_text", default="")),
                away_abbr=str(_pick(away_row, "teamtricode", "teamabbreviation", default="")),
                away_name=_team_name(away_row),
                away_score=_int_or_none(_pick(away_row, "score", "pts")),
                home_abbr=str(_pick(home_row, "teamtricode", "teamabbreviation", default="")),
                home_name=_team_name(home_row),
                home_score=_int_or_none(_pick(home_row, "score", "pts")),
                tip_off=_game_time_from_nba(row),
            )
        )
    return _sort_games(games)


def _games_from_scoreboard_v2(game_header, line_score) -> List[Game]:
    """解析 ScoreboardV2（直接提供主客队 ID）。"""
    games: List[Game] = []
    teams: Dict[str, Dict[str, Any]] = {}
    for line in _rows(line_score):
        team_id = str(_pick(line, "teamid", default=""))
        if team_id:
            teams[team_id] = line

    for row in _rows(game_header):
        home_row = teams.get(str(_pick(row, "hometeamid", default="")))
        away_row = teams.get(str(_pick(row, "visitorteamid", default="")))
        if home_row is None or away_row is None:
            continue

        status_id = _int_or_none(_pick(row, "gamestatusid", "game_status_id")) or STATUS_PENDING
        status_id = status_id if status_id in STATUS_TEXT else STATUS_PENDING

        games.append(
            _build_game(
                game_id=str(_pick(row, "gameid", default="")),
                status_id=status_id,
                status_detail=str(_pick(row, "gamestatustext", "game_status_text", default="")),
                away_abbr=str(_pick(away_row, "teamabbreviation", "teamtricode", default="")),
                away_name=_team_name(away_row),
                away_score=_int_or_none(_pick(away_row, "pts", "score")),
                home_abbr=str(_pick(home_row, "teamabbreviation", "teamtricode", default="")),
                home_name=_team_name(home_row),
                home_score=_int_or_none(_pick(home_row, "pts", "score")),
                tip_off=_game_time_from_nba(row),
            )
        )
    return _sort_games(games)


def fetch_from_nba_api(date_str: str) -> List[Game]:
    """通过 nba_api 获取指定日期（美东日期）的比赛。"""
    if scoreboardv3 is None and scoreboardv2 is None:
        raise FetchError("nba_api 未安装")

    errors: List[str] = []

    if scoreboardv3 is not None:
        try:
            board = scoreboardv3.ScoreboardV3(game_date=date_str, timeout=NBA_API_TIMEOUT)
            games = _games_from_scoreboard_v3(board.game_header, board.line_score)
            if games:
                return games
            errors.append("ScoreboardV3 无比赛数据")
        except Exception as exc:  # noqa: BLE001 - 网络/解析异常都按失败处理
            errors.append(f"ScoreboardV3 {type(exc).__name__}: {exc}")

    if scoreboardv2 is not None:
        try:
            board = scoreboardv2.ScoreboardV2(game_date=date_str, timeout=NBA_API_TIMEOUT)
            games = _games_from_scoreboard_v2(board.game_header, board.line_score)
            if games:
                return games
            errors.append("ScoreboardV2 无比赛数据")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"ScoreboardV2 {type(exc).__name__}: {exc}")

    raise FetchError("；".join(errors) or "NBA 官方接口无数据")


# ---------------------------------------------------------------------------
# 数据源二：ESPN 公开接口
# ---------------------------------------------------------------------------

_ESPN_STATE_TO_STATUS = {
    "pre": STATUS_PENDING,
    "in": STATUS_LIVE,
    "post": STATUS_FINAL,
}


def fetch_from_espn(date_str: str) -> List[Game]:
    """通过 ESPN 公开接口获取指定日期（美东日期）的比赛。"""
    payload = _http_get_json(
        ESPN_SCOREBOARD_URL, params={"dates": date_str.replace("-", "")}
    )

    games: List[Game] = []
    for event in payload.get("events", []) or []:
        competitions = event.get("competitions") or []
        if not competitions:
            continue
        competition = competitions[0]

        teams = {
            competitor.get("homeAway", ""): competitor
            for competitor in competition.get("competitors", [])
        }
        home, away = teams.get("home"), teams.get("away")
        if not home or not away:
            continue

        status_type = (event.get("status") or {}).get("type") or {}
        status_id = _ESPN_STATE_TO_STATUS.get(status_type.get("state", "pre"), STATUS_PENDING)

        games.append(
            _build_game(
                game_id=str(event.get("id", "")),
                status_id=status_id,
                status_detail=str(
                    status_type.get("detail") or status_type.get("description") or ""
                ),
                away_abbr=(away.get("team") or {}).get("abbreviation", ""),
                away_name=(away.get("team") or {}).get("displayName", "未知球队"),
                away_score=_int_or_none(away.get("score")),
                home_abbr=(home.get("team") or {}).get("abbreviation", ""),
                home_name=(home.get("team") or {}).get("displayName", "未知球队"),
                home_score=_int_or_none(home.get("score")),
                tip_off=_to_beijing(_parse_iso(event.get("date") or competition.get("date"))),
            )
        )
    return _sort_games(games)


# ---------------------------------------------------------------------------
# 数据源三：NBA 官网 CDN（仅当日数据，适合云服务器环境）
# ---------------------------------------------------------------------------


def fetch_from_nba_cdn(date_str: str) -> List[Game]:
    """通过 NBA 官网 CDN 获取**当日**比赛（该接口不提供历史/未来日期）。"""
    today_et = datetime.now(EASTERN_TZ).strftime("%Y-%m-%d")
    if date_str != today_et:
        raise FetchError(f"CDN 仅提供当日数据（美东 {today_et}），跳过 {date_str}")

    payload = _http_get_json(NBA_CDN_SCOREBOARD_URL)
    scoreboard = payload.get("scoreboard") or {}

    games: List[Game] = []
    for item in scoreboard.get("games", []) or []:
        away = item.get("awayTeam") or {}
        home = item.get("homeTeam") or {}
        if not away.get("teamTricode") or not home.get("teamTricode"):
            continue

        status_id = _int_or_none(item.get("gameStatus")) or STATUS_PENDING
        status_id = status_id if status_id in STATUS_TEXT else STATUS_PENDING

        games.append(
            _build_game(
                game_id=str(item.get("gameId", "")),
                status_id=status_id,
                status_detail=str(item.get("gameStatusText", "")),
                away_abbr=str(away.get("teamTricode", "")),
                away_name=" ".join(
                    part for part in (away.get("teamCity"), away.get("teamName")) if part
                ).strip() or "未知球队",
                away_score=_int_or_none(away.get("score")),
                home_abbr=str(home.get("teamTricode", "")),
                home_name=" ".join(
                    part for part in (home.get("teamCity"), home.get("teamName")) if part
                ).strip() or "未知球队",
                home_score=_int_or_none(home.get("score")),
                tip_off=_to_beijing(_parse_iso(item.get("gameTimeUTC"))),
            )
        )
    return _sort_games(games)


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

#: 数据源注册表：key -> (展示名, 抓取函数)
SOURCES: Dict[str, Tuple[str, Callable[[str], List[Game]]]] = {
    "nba_api": ("NBA官方接口", fetch_from_nba_api),
    "espn": ("ESPN", fetch_from_espn),
    "nba_cdn": ("NBA官网CDN", fetch_from_nba_cdn),
}

SOURCE_ORDER = ["nba_api", "espn", "nba_cdn"]


def today_str() -> str:
    """北京时间的今天（美东日期与之可能有 1 天差异，这里作为默认查询日期）。"""
    return datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")


def get_today_games(date: Optional[str] = None, source: str = "auto") -> Tuple[List[Game], str]:
    """获取某一天的比赛。

    Args:
        date: ``YYYY-MM-DD``（美东日期），默认取北京时间的今天。
        source: ``auto`` / ``nba_api`` / ``espn`` / ``nba_cdn``。

    Returns:
        ``(比赛列表, 实际使用的数据源名称)``；当天没有比赛时列表为空。

    Raises:
        FetchError: 所有尝试的数据源都失败。
    """
    date_str = date or today_str()

    if source == "auto":
        keys = SOURCE_ORDER
    elif source in SOURCES:
        keys = [source]
    else:
        raise FetchError(f"未知数据源: {source}")

    errors: List[str] = []
    for key in keys:
        label, func = SOURCES[key]
        try:
            return func(date_str), label
        except FetchError as exc:
            errors.append(f"{label}: {exc}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: {type(exc).__name__}: {exc}")

    raise FetchError("数据抓取失败 -> " + " | ".join(errors))


def diagnose(date: Optional[str] = None) -> Tuple[str, List[Tuple[str, bool, str]]]:
    """逐个测试数据源，返回 ``(日期, [(数据源, 是否可用, 说明)])``。

    用于排查线上环境到底哪个数据源不可用（``python main.py --diagnose``）。
    """
    date_str = date or today_str()
    results: List[Tuple[str, bool, str]] = []
    for key in SOURCE_ORDER:
        label, func = SOURCES[key]
        started = time.time()
        try:
            games = func(date_str)
        except Exception as exc:  # noqa: BLE001 - 诊断模式不抛异常
            results.append((label, False, f"{type(exc).__name__}: {exc}"[:300]))
        else:
            cost = time.time() - started
            results.append((label, True, f"可用，返回 {len(games)} 场比赛，耗时 {cost:.1f}s"))
    return date_str, results


__all__ = [
    "BEIJING_TZ",
    "EASTERN_TZ",
    "FetchError",
    "Game",
    "SOURCE_ORDER",
    "SOURCES",
    "STATUS_FINAL",
    "STATUS_LIVE",
    "STATUS_PENDING",
    "diagnose",
    "fetch_from_espn",
    "fetch_from_nba_api",
    "fetch_from_nba_cdn",
    "get_today_games",
    "today_str",
]
