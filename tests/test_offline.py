"""离线单元测试：验证各数据源的解析逻辑（不需要联网）。

运行方式::

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from src import fetcher
from src.fetcher import (
    FetchError,
    _games_from_scoreboard_v2,
    _games_from_scoreboard_v3,
    _parse_clock_text,
    fetch_from_nba_cdn,
)
from src.formatter import NO_GAME_MESSAGE, build_wechat_content, format_games
from src.notifier import _read_key

ORL_ID = 1610612753
MEM_ID = 1610612763


class FakeDataSet:
    """模拟 nba_api 的 DataSet。"""

    def __init__(self, headers, rows):
        self._headers = headers
        self._rows = rows

    def get_dict(self):
        return {"headers": self._headers, "data": self._rows}


class ScoreboardV2ParseTest(unittest.TestCase):
    def _parse(self, status_id, status_text, scores):
        headers = FakeDataSet(
            ["GAME_DATE_EST", "GAME_SEQUENCE", "GAME_ID", "GAME_STATUS_ID",
             "GAME_STATUS_TEXT", "GAMECODE", "HOME_TEAM_ID", "VISITOR_TEAM_ID"],
            [["2026-01-15T00:00:00", 1, "0022500001", status_id, status_text,
              "20260115/MEMORL", ORL_ID, MEM_ID]],
        )
        line_score = FakeDataSet(
            ["GAME_DATE_EST", "GAME_SEQUENCE", "GAME_ID", "TEAM_ID",
             "TEAM_ABBREVIATION", "TEAM_CITY_NAME", "TEAM_NICKNAME", "PTS"],
            [
                ["2026-01-15T00:00:00", 1, "0022500001", ORL_ID, "ORL", "Orlando", "Magic", scores[0]],
                ["2026-01-15T00:00:00", 2, "0022500001", MEM_ID, "MEM", "Memphis", "Grizzlies", scores[1]],
            ],
        )
        return _games_from_scoreboard_v2(headers, line_score)[0]

    def test_parse_scheduled_game(self):
        game = self._parse(1, "7:00 pm ET", (None, None))
        self.assertEqual(game["away"], "MEM")
        self.assertEqual(game["home"], "ORL")
        self.assertEqual(game["away_name"], "Memphis Grizzlies")
        self.assertEqual(game["status"], "未开始")
        self.assertEqual(game["time"], "01-16 08:00")  # 19:00 ET -> 次日 08:00 北京

    def test_parse_final_game(self):
        game = self._parse(3, "Final", (118, 111))
        self.assertEqual(game["status"], "已结束")
        self.assertEqual((game["away_score"], game["home_score"]), (111, 118))


class ScoreboardV3ParseTest(unittest.TestCase):
    def test_parse_scheduled_game(self):
        headers = FakeDataSet(
            ["gameId", "gameCode", "gameStatus", "gameStatusText", "period",
             "gameClock", "gameTimeUTC", "gameEt"],
            [["0022500001", "20260115/MEMORL", 1, "7:00 pm ET", 0, "",
              "2026-01-16T00:00:00Z", "2026-01-15 19:00 ET"]],
        )
        line_score = FakeDataSet(
            ["gameId", "teamId", "teamCity", "teamName", "teamTricode", "score"],
            [
                ["0022500001", ORL_ID, "Orlando", "Magic", "ORL", None],
                ["0022500001", MEM_ID, "Memphis", "Grizzlies", "MEM", None],
            ],
        )

        game = _games_from_scoreboard_v3(headers, line_score)[0]
        self.assertEqual((game["away"], game["home"]), ("MEM", "ORL"))
        self.assertEqual(game["status"], "未开始")
        self.assertEqual(game["time"], "01-16 08:00")


class NbaCdnParseTest(unittest.TestCase):
    """CDN 数据源：模拟官方 CDN 返回结构（含未开始/已结束两种状态）。"""

    payload = {
        "scoreboard": {
            "gameDate": "2026-01-15",
            "games": [
                {
                    "gameId": "0022500001",
                    "gameCode": "20260115/MEMORL",
                    "gameStatus": 3,
                    "gameStatusText": "Final",
                    "gameTimeUTC": "2026-01-16T00:00:00Z",
                    "awayTeam": {"teamTricode": "MEM", "teamCity": "Memphis",
                                 "teamName": "Grizzlies", "score": 111},
                    "homeTeam": {"teamTricode": "ORL", "teamCity": "Orlando",
                                 "teamName": "Magic", "score": 118},
                },
                {
                    "gameId": "0022500002",
                    "gameCode": "20260115/BOSMIA",
                    "gameStatus": 1,
                    "gameStatusText": "7:30 pm ET",
                    "gameTimeUTC": "2026-01-16T00:30:00Z",
                    "awayTeam": {"teamTricode": "BOS", "teamCity": "Boston",
                                 "teamName": "Celtics", "score": 0},
                    "homeTeam": {"teamTricode": "MIA", "teamCity": "Miami",
                                 "teamName": "Heat", "score": 0},
                },
            ],
        }
    }

    def test_parse_games(self):
        today_et = fetcher.datetime.now(fetcher.EASTERN_TZ).strftime("%Y-%m-%d")
        with mock.patch.object(fetcher, "_http_get_json", return_value=self.payload):
            games = fetch_from_nba_cdn(today_et)

        self.assertEqual(len(games), 2)
        final_game = games[0]
        self.assertEqual((final_game["away"], final_game["home"]), ("MEM", "ORL"))
        self.assertEqual(final_game["status"], "已结束")
        self.assertEqual(final_game["home_name"], "Orlando Magic")

    def test_other_date_is_skipped(self):
        with self.assertRaises(FetchError):
            fetch_from_nba_cdn("2000-01-01")


class HelperTest(unittest.TestCase):
    def test_parse_clock_text(self):
        self.assertEqual(_parse_clock_text("7:30 pm ET"), (19, 30))
        self.assertEqual(_parse_clock_text("12:00 AM"), (0, 0))
        self.assertIsNone(_parse_clock_text("Final"))

    def test_sort_games_handles_missing_tip_off(self):
        """没有开赛时间的比赛不能和带时区的时间比较时崩溃。"""
        games = [
            fetcher._build_game("1", 3, "Final", "A", "A", 1, "B", "B", 2, None),
            fetcher._build_game("2", 1, "7:00 pm ET", "C", "C", 0, "D", "D", 0,
                                fetcher._parse_iso("2026-01-16T00:00:00Z")),
        ]
        ordered = fetcher._sort_games(games)
        self.assertEqual(ordered[0]["game_id"], "2")
        self.assertEqual(ordered[1]["time"], "时间待定")


class FormatterTest(unittest.TestCase):
    def test_no_games(self):
        self.assertEqual(format_games([]), NO_GAME_MESSAGE)
        self.assertIn(NO_GAME_MESSAGE, build_wechat_content([], "2026-01-15"))

    def test_format_rows(self):
        games = [
            {
                "status_id": 3, "status": "已结束", "away": "MEM", "home": "ORL",
                "away_score": 111, "home_score": 118, "time": "01-16 08:00",
            },
            {
                "status_id": 1, "status": "未开始", "away": "BOS", "home": "MIA",
                "away_score": 0, "home_score": 0, "time": "01-16 09:00",
            },
        ]
        rows = format_games(games)
        self.assertEqual(rows[0]["score"], "111 - 118")
        self.assertEqual(rows[1]["score"], "VS")
        self.assertIn("BOS VS MIA", build_wechat_content(games, "2026-01-15", "ESPN"))


class NotifierKeyTest(unittest.TestCase):
    def test_placeholder_key_is_ignored(self):
        original = os.environ.get("SERVERCHAN_KEY")
        try:
            os.environ["SERVERCHAN_KEY"] = "SCT123456你的Key"
            self.assertEqual(_read_key(), "")
            os.environ["SERVERCHAN_KEY"] = "SCT123456abcdefghijklmnop"
            self.assertTrue(_read_key().startswith("SCT"))
        finally:
            if original is None:
                os.environ.pop("SERVERCHAN_KEY", None)
            else:
                os.environ["SERVERCHAN_KEY"] = original


if __name__ == "__main__":
    unittest.main()
