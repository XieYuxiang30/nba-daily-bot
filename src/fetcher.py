"""数据抓取模块。

数据源优先级：
1. ``nba_api``（NBA 官方 stats 接口，ScoreboardV3 / ScoreboardV2）
2. ESPN 公开接口（无需 Key，作为官方接口不可用时的兜底）

两个数据源的数据结构不同，这里统一转换成「标准化比赛字典」后再交给
:mod:`formatter` 处理，避免上层逻辑与数据源耦合。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

try:  # nba_api 依赖较重，导入失败时仍允许使用 ESPN 数据源
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
ESPN_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}

NBA_API_TIMEOUT = 30
ESPN_TIMEOUT = 20

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
    """所有数据源都不可用 / 解析失败时抛出。"""


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


def _parse_clock_text(text: str) -> Optional[Any]:
    """从 ``7:30 pm ET`` 这类文本里解析出小时和分钟。

    返回 ``(hour, minute)``，小时保持 12 小时制，是否 +12 由调用方判断。
    """
    if not text:
        return None
    match = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)", text, re.IGNORECASE)
    if not match:
        return None
    hour = int(match.group(1)) % 12
    minute = int(match.group(2))
    meridiem = match.group(3).lower()
    return (hour + 12 if meridiem == "pm" else hour), minute


def _beijing_label(dt: Optional[datetime]) -> str:
    if dt is None:
        return "时间待定"
    return dt.strftime("%m-%d %H:%M")


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 数据源一：NBA 官方接口（nba_api）
# ---------------------------------------------------------------------------


def _game_time_from_nba(row: Dict[str, Any]) -> Optional[datetime]:
    """解析 NBA 官方接口给出的开赛时间（统一转成北京时间）。"""
    utc_dt = _parse_iso(_pick(row, "gametimeutc", "game_time_utc"))
    if utc_dt:
        return _to_beijing(utc_dt)

    # ScoreboardV2 没有 UTC 字段，只有美东时间的文本，例如 "7:30 pm ET"
    status_text = str(_pick(row, "gamestatustext", "game_status_text", default=""))
    clock = _parse_clock_text(status_text)
    if not clock:
        return None

    date_part = _pick(row, "gamedateest", "game_date_est", "gamedate", "game_date")
    base: Optional[datetime] = None
    if isinstance(date_part, str) and date_part:
        try:
            base = datetime.strptime(date_part[:10], "%Y-%m-%d")
        except ValueError:
            base = None
    if base is None:
        return None

    eastern = base.replace(hour=clock[0], minute=clock[1], tzinfo=EASTERN_TZ)
    return eastern.astimezone(BEIJING_TZ)


def _games_from_scoreboard_v3(game_header, line_score) -> List[Game]:
    """解析 ScoreboardV3 的数据。

    V3 的 GameHeader 里没有主客队 ID，但 ``gameCode`` 形如 ``20260115/MEMORL``
    （客队在前、主队在后），可以据此判断主客场。
    """
    games: List[Game] = []
    headers = _rows(game_header)
    lines = _rows(line_score)

    by_game: Dict[str, List[Dict[str, Any]]] = {}
    for line in lines:
        by_game.setdefault(str(_pick(line, "gameid", default="")), []).append(line)

    for row in headers:
        game_id = str(_pick(row, "gameid", default=""))
        game_code = str(_pick(row, "gamecode", default=""))
        team_rows = by_game.get(game_id, [])
        if not team_rows:
            continue

        # gameCode 形如 20260115/MEMORL：客队在前、主队（结尾）在后。
        # 球队缩写长度不固定（UTA/UTAH、NOP/NO），所以直接用真实缩写去比对。
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
                game_id=game_id,
                status_id=status_id,
                status_detail=str(_pick(row, "gamestatustext", "game_status_text", default="")),
                away_abbr=str(_pick(away_row, "teamtricode", "team_abbreviation", default="?")),
                away_name=_team_name(away_row),
                away_score=_int_or_none(_pick(away_row, "score", "pts", default=0)) or 0,
                home_abbr=str(_pick(home_row, "teamtricode", "team_abbreviation", default="?")),
                home_name=_team_name(home_row),
                home_score=_int_or_none(_pick(home_row, "score", "pts", default=0)) or 0,
                tip_off=_game_time_from_nba(row),
            )
        )
    return games


def _games_from_scoreboard_v2(game_header, line_score) -> List[Game]:
    """解析 ScoreboardV2 的数据（V2 直接提供主客队 ID）。"""
    games: List[Game] = []
    headers = _rows(game_header)
    lines = _rows(line_score)

    teams: Dict[str, Dict[str, Any]] = {}
    for line in lines:
        team_id = str(_pick(line, "teamid", "team_id", default=""))
        if team_id:
            teams[team_id] = line

    for row in headers:
        home_id = str(_pick(row, "hometeamid", "home_team_id", default=""))
        away_id = str(_pick(row, "visitorteamid", "visitor_team_id", default=""))
        home_row = teams.get(home_id)
        away_row = teams.get(away_id)
        if home_row is None or away_row is None:
            continue

        status_id = _int_or_none(_pick(row, "gamestatusid", "game_status_id")) or STATUS_PENDING
        status_id = status_id if status_id in STATUS_TEXT else STATUS_PENDING

        games.append(
            _build_game(
                game_id=str(_pick(row, "gameid", "game_id", default="")),
                status_id=status_id,
                status_detail=str(_pick(row, "gamestatustext", "game_status_text", default="")),
                away_abbr=str(_pick(away_row, "teamabbreviation", "teamtricode", default="?")),
                away_name=_team_name(away_row),
                away_score=_int_or_none(_pick(away_row, "pts", "score", default=0)) or 0,
                home_abbr=str(_pick(home_row, "teamabbreviation", "teamtricode", default="?")),
                home_name=_team_name(home_row),
                home_score=_int_or_none(_pick(home_row, "pts", "score", default=0)) or 0,
                tip_off=_game_time_from_nba(row),
            )
        )
    return games


def _team_name(team_row: Dict[str, Any]) -> str:
    city = _pick(team_row, "teamcity", "team_city_name", "teamcityname", default="")
    name = _pick(team_row, "teamname", "team_nickname", "teamnickname", default="")
    return " ".join(part for part in (city, name) if part).strip() or "未知球队"


def _build_game(
    game_id: str,
    status_id: int,
    status_detail: str,
    away_abbr: str,
    away_name: str,
    away_score: int,
    home_abbr: str,
    home_name: str,
    home_score: int,
    tip_off: Optional[datetime],
) -> Game:
    return {
        "game_id": game_id,
        "status_id": status_id,
        "status": STATUS_TEXT[status_id],
        "detail": status_detail,
        "away": away_abbr,
        "away_name": away_name,
        "away_score": away_score,
        "home": home_abbr,
        "home_name": home_name,
        "home_score": home_score,
        "tip_off": tip_off,
        "time": _beijing_label(tip_off),
    }


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
                return sorted(games, key=lambda g: g["tip_off"] or datetime.max)
            errors.append("ScoreboardV3 返回空数据")
        except Exception as exc:  # noqa: BLE001 - 网络/解析异常都按失败处理
            errors.append(f"ScoreboardV3: {exc}")

    if scoreboardv2 is not None:
        try:
            board = scoreboardv2.ScoreboardV2(game_date=date_str, timeout=NBA_API_TIMEOUT)
            games = _games_from_scoreboard_v2(board.game_header, board.line_score)
            if games:
                return sorted(games, key=lambda g: g["tip_off"] or datetime.max)
            errors.append("ScoreboardV2 返回空数据")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"ScoreboardV2: {exc}")

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
    compact = date_str.replace("-", "")
    try:
        response = requests.get(
            ESPN_SCOREBOARD_URL,
            params={"dates": compact},
            headers=ESPN_HEADERS,
            timeout=ESPN_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        raise FetchError(f"ESPN 接口请求失败: {exc}") from exc

    games: List[Game] = []
    for event in payload.get("events", []):
        competitions = event.get("competitions") or []
        if not competitions:
            continue
        competition = competitions[0]

        teams: Dict[str, Dict[str, Any]] = {}
        for competitor in competition.get("competitors", []):
            teams[competitor.get("homeAway", "")] = competitor

        home = teams.get("home")
        away = teams.get("away")
        if not home or not away:
            continue

        status_type = (event.get("status") or {}).get("type") or {}
        state = status_type.get("state", "pre")
        status_id = _ESPN_STATE_TO_STATUS.get(state, STATUS_PENDING)

        games.append(
            _build_game(
                game_id=str(event.get("id", "")),
                status_id=status_id,
                status_detail=str(status_type.get("detail") or status_type.get("description") or ""),
                away_abbr=((away.get("team") or {}).get("abbreviation") or "?"),
                away_name=(away.get("team") or {}).get("displayName", "未知球队"),
                away_score=_int_or_none(away.get("score")) or 0,
                home_abbr=((home.get("team") or {}).get("abbreviation") or "?"),
                home_name=(home.get("team") or {}).get("displayName", "未知球队"),
                home_score=_int_or_none(home.get("score")) or 0,
                tip_off=_to_beijing(_parse_iso(event.get("date") or competition.get("date"))),
            )
        )

    return sorted(games, key=lambda g: g["tip_off"] or datetime.max)


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def get_today_games(date: Optional[str] = None, source: str = "auto") -> Tuple[List[Game], str]:
    """获取某一天的比赛。

    Args:
        date: ``YYYY-MM-DD``，默认取「北京时间」的今天。
        source: ``auto`` / ``nba_api`` / ``espn``。

    Returns:
        ``(比赛列表, 实际使用的数据源名称)``。没有比赛时返回空列表。

    Raises:
        FetchError: 所有可选数据源都失败。
    """
    date_str = date or datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")

    providers = {
        "nba_api": ("nba_api", fetch_from_nba_api),
        "espn": ("ESPN", fetch_from_espn),
    }
    order = ["nba_api", "espn"] if source == "auto" else [source]
    if source not in providers and source != "auto":
        raise FetchError(f"未知数据源: {source}")

    errors: List[str] = []
    for name in order:
        label, func = providers[name]
        try:
            return func(date_str), label
        except FetchError as exc:
            errors.append(f"{label}: {exc}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: {exc}")

    raise FetchError("数据抓取失败 -> " + " | ".join(errors))
