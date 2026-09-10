"""离线单元测试：验证 nba_api 数据解析逻辑（不需要联网）。

运行方式::

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest
from datetime import datetime

from src.fetcher import (
    _games_from_scoreboard_v2,
    _games_from_scoreboard_v3,
    _parse_clock_text,
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
    def test_parse_scheduled_game(self):
        headers = FakeDataSet(
            ["GAME_DATE_EST", "GAME_SEQUENCE", "GAME_ID", "GAME_STATUS_ID",
             "GAME_STATUS_TEXT", "GAMECODE", "HOME_TEAM_ID", "VISITOR_TEAM_ID"],
            [["2026-01-15T00:00:00", 1, "0022500001", 1, "7:00 pm ET",
              "20260115/MEMORL", ORL_ID, MEM_ID]],
        )
        line_score = FakeDataSet(
            ["GAME_DATE_EST", "GAME_SEQUENCE", "GAME_ID", "TEAM_ID",
             "TEAM_ABBREVIATION", "TEAM_CITY_NAME", "TEAM_NICKNAME", "PTS"],
            [
                ["2026-01-15T00:00:00", 1, "0022500001", ORL_ID, "ORL", "Orlando", "Magic", None],
                ["2026-01-15T00:00:00", 2, "0022500001", MEM_ID, "MEM", "Memphis", "Grizzlies", None],
            ],
        )

        games = _games_from_scoreboard_v2(headers, line_score)
        self.assertEqual(len(games), 1)
        game = games[0]
        self.assertEqual(game["away"], "MEM")
        self.assertEqual(game["home"], "ORL")
        self.assertEqual(game["away_name"], "Memphis Grizzlies")
        self.assertEqual(game["status"], "未开始")
        self.assertEqual(game["time"], "01-16 08:00")  # 19:00 ET -> 次日 08:00 北京

    def test_parse_final_game(self):
        headers = FakeDataSet(
            ["GAME_DATE_EST", "GAME_SEQUENCE", "GAME_ID", "GAME_STATUS_ID",
             "GAME_STATUS_TEXT", "GAMECODE", "HOME_TEAM_ID", "VISITOR_TEAM_ID"],
            [["2026-01-15T00:00:00", 1, "0022500001", 3, "Final",
              "20260115/MEMORL", ORL_ID, MEM_ID]],
        )
        line_score = FakeDataSet(
            ["GAME_DATE_EST", "GAME_SEQUENCE", "GAME_ID", "TEAM_ID",
             "TEAM_ABBREVIATION", "TEAM_CITY_NAME", "TEAM_NICKNAME", "PTS"],
            [
                ["2026-01-15T00:00:00", 1, "0022500001", ORL_ID, "ORL", "Orlando", "Magic", 118],
                ["2026-01-15T00:00:00", 2, "0022500001", MEM_ID, "MEM", "Memphis", "Grizzlies", 111],
            ],
        )

        game = _games_from_scoreboard_v2(headers, line_score)[0]
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


class HelperTest(unittest.TestCase):
    def test_parse_clock_text(self):
        self.assertEqual(_parse_clock_text("7:30 pm ET"), (19, 30))
        self.assertEqual(_parse_clock_text("12:00 AM"), (0, 0))
        self.assertIsNone(_parse_clock_text("Final"))


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
        import os

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
