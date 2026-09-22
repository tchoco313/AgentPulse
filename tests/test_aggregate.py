import unittest
from datetime import datetime, timezone

from pulse import aggregate as agg


def usage(ts, session="s1", **kw):
    e = {"kind": "usage", "ts": ts, "session": session, "cwd": "/Users/teo",
         "model": "claude-opus-5", "input": 0, "output": 0,
         "cache_read": 0, "cache_creation": 0, "sub": False}
    e.update(kw)
    return e


def tool(ts, name, skill=None, session="s1", sub=False):
    return {"kind": "tool", "ts": ts, "session": session, "name": name,
            "skill": skill, "sub": sub}


class TestClassifyTool(unittest.TestCase):
    def test_mcp_tool_uses_server_name(self):
        self.assertEqual(agg.classify_tool("mcp__dbhub__execute_sql", None),
                         ("mcp", "dbhub"))

    def test_mcp_server_name_with_underscores(self):
        self.assertEqual(
            agg.classify_tool("mcp__claude-in-chrome__tabs_close_mcp", None),
            ("mcp", "claude-in-chrome"))

    def test_skill_uses_skill_argument(self):
        self.assertEqual(agg.classify_tool("Skill", "dataviz"), ("skill", "dataviz"))

    def test_skill_without_argument_falls_back(self):
        self.assertEqual(agg.classify_tool("Skill", None), ("skill", "(미상)"))

    def test_builtin_tool(self):
        self.assertEqual(agg.classify_tool("Bash", None), ("tools", "Bash"))

    def test_malformed_mcp_name_is_not_mcp(self):
        self.assertEqual(agg.classify_tool("mcp__", None), ("tools", "mcp__"))


class TestLocalDay(unittest.TestCase):
    def test_utc_timestamp_maps_to_local_date(self):
        # Review Focus 1: UTC로 버킷하면 KST 오전 작업이 전날로 밀린다
        ts = "2026-09-22T01:00:00.000Z"
        expected = (datetime(2026, 9, 22, 1, 0, tzinfo=timezone.utc)
                    .astimezone().strftime("%Y-%m-%d"))
        self.assertEqual(agg.local_day(ts), expected)

    def test_late_utc_evening_may_be_next_local_day(self):
        ts = "2026-09-21T23:30:00.000Z"
        expected = (datetime(2026, 9, 21, 23, 30, tzinfo=timezone.utc)
                    .astimezone().strftime("%Y-%m-%d"))
        self.assertEqual(agg.local_day(ts), expected)

    def test_empty_timestamp_gives_empty_string(self):
        self.assertEqual(agg.local_day(""), "")


class TestFold(unittest.TestCase):
    def test_sums_tokens_by_day(self):
        day = agg.local_day("2026-09-22T01:00:00.000Z")
        p = agg.fold([usage("2026-09-22T01:00:00.000Z", input=10, output=5),
                      usage("2026-09-22T01:05:00.000Z", input=1, cache_read=100)])
        self.assertEqual(p["days"][day]["input"], 11)
        self.assertEqual(p["days"][day]["output"], 5)
        self.assertEqual(p["days"][day]["cache_read"], 100)

    def test_counts_tools_by_group(self):
        p = agg.fold([tool("2026-09-22T01:00:00.000Z", "Bash"),
                      tool("2026-09-22T01:00:00.000Z", "Bash"),
                      tool("2026-09-22T01:00:00.000Z", "mcp__dbhub__execute_sql"),
                      tool("2026-09-22T01:00:00.000Z", "Skill", skill="dataviz")])
        day = agg.local_day("2026-09-22T01:00:00.000Z")
        labels = p["days"][day]["labels"]
        self.assertEqual(labels["tools"]["Bash"], 2)
        self.assertEqual(labels["mcp"]["dbhub"], 1)
        self.assertEqual(labels["skill"]["dataviz"], 1)
        self.assertEqual(p["days"][day]["calls"],
                         {"mcp": 1, "skill": 1, "tools": 2})

    def test_compact_dropped_tokens(self):
        p = agg.fold([{"kind": "compact", "ts": "2026-09-20T05:00:00.000Z",
                       "session": "s1", "trigger": "auto",
                       "pre": 966957, "post": 13491}])
        self.assertEqual(p["compacts"][0]["dropped"], 953466)

    def test_compact_dropped_never_negative(self):
        # Review Focus 5
        p = agg.fold([{"kind": "compact", "ts": "2026-09-20T05:00:00.000Z",
                       "session": "s1", "trigger": "auto",
                       "pre": 100, "post": 500}])
        self.assertEqual(p["compacts"][0]["dropped"], 0)

    def test_session_tracks_latest_context(self):
        p = agg.fold([
            usage("2026-09-22T01:00:00.000Z", input=1, cache_read=100, cache_creation=10),
            usage("2026-09-22T02:00:00.000Z", input=2, cache_read=500, cache_creation=20),
        ])
        self.assertEqual(p["sessions"]["s1"]["last_ctx"], 522)
        self.assertEqual(p["sessions"]["s1"]["last_ts"], "2026-09-22T02:00:00.000Z")

    def test_out_of_order_events_do_not_regress_latest(self):
        p = agg.fold([
            usage("2026-09-22T02:00:00.000Z", input=2, cache_read=500),
            usage("2026-09-22T01:00:00.000Z", input=1, cache_read=100),
        ])
        self.assertEqual(p["sessions"]["s1"]["last_ts"], "2026-09-22T02:00:00.000Z")
        self.assertEqual(p["sessions"]["s1"]["last_ctx"], 502)

    def test_sidechain_tokens_count_but_do_not_move_session_state(self):
        # 서브에이전트는 부모와 같은 sessionId를 쓴다. 토큰은 사용자가 쓴 것이
        # 맞지만, 컨텍스트 점유는 부모 것이 아니다.
        p = agg.fold([
            usage("2026-09-22T01:00:00.000Z", input=1, cache_read=100),
            usage("2026-09-22T02:00:00.000Z", input=5, cache_read=900, sub=True),
        ])
        day = agg.local_day("2026-09-22T01:00:00.000Z")
        self.assertEqual(p["days"][day]["input"], 6)          # 토큰은 다 센다
        self.assertEqual(p["sessions"]["s1"]["last_ctx"], 101)  # 사용자 값 유지
        self.assertEqual(p["sessions"]["s1"]["last_ts"],
                         "2026-09-22T01:00:00.000Z")

    def test_sidechain_activity_does_not_move_last_ts(self):
        p = agg.fold([
            usage("2026-09-22T01:00:00.000Z", input=1),
            {"kind": "activity", "ts": "2026-09-22T05:00:00.000Z",
             "session": "s1", "sub": True},
        ])
        self.assertEqual(p["sessions"]["s1"]["last_ts"],
                         "2026-09-22T01:00:00.000Z")

    def test_sidechain_tool_calls_count_in_ranking(self):
        p = agg.fold([tool("2026-09-22T01:00:00.000Z", "Bash", sub=True)])
        day = agg.local_day("2026-09-22T01:00:00.000Z")
        self.assertEqual(p["days"][day]["labels"]["tools"]["Bash"], 1)

    def test_title_event_names_session(self):
        p = agg.fold([{"kind": "title", "session": "s1", "title": "제목"}])
        self.assertEqual(p["sessions"]["s1"]["title"], "제목")

    def test_activity_updates_last_ts_but_not_context(self):
        p = agg.fold([usage("2026-09-22T01:00:00.000Z", input=1, cache_read=100),
                      {"kind": "activity", "ts": "2026-09-22T03:00:00.000Z",
                       "session": "s1"}])
        self.assertEqual(p["sessions"]["s1"]["last_ts"], "2026-09-22T03:00:00.000Z")
        self.assertEqual(p["sessions"]["s1"]["last_ctx"], 101)

    def test_folding_onto_existing_partial_accumulates(self):
        p = agg.fold([usage("2026-09-22T01:00:00.000Z", input=10)])
        p = agg.fold([usage("2026-09-22T01:00:00.000Z", input=5)], p)
        day = agg.local_day("2026-09-22T01:00:00.000Z")
        self.assertEqual(p["days"][day]["input"], 15)


class TestMerge(unittest.TestCase):
    def test_incremental_equals_whole(self):
        """증분 경로와 전체 경로가 같은 답을 낸다 — 증분 스캔이 옳다는 근거.

        서브에이전트 이벤트를 섞어둔다. 세션 상태를 건너뛰는 분기가 생겼으니,
        그 분기가 쪼개는 지점에 따라 달라지지 않는지도 여기서 걸린다.
        """
        events = [
            usage("2026-09-20T01:00:00.000Z", input=10, output=1),
            tool("2026-09-20T01:00:00.000Z", "Bash"),
            usage("2026-09-20T03:00:00.000Z", input=7, cache_read=70, sub=True),
            tool("2026-09-20T03:00:00.000Z", "Read", sub=True),
            usage("2026-09-21T01:00:00.000Z", input=20, cache_read=5),
            tool("2026-09-21T01:00:00.000Z", "mcp__dbhub__execute_sql"),
            {"kind": "activity", "ts": "2026-09-21T04:00:00.000Z",
             "session": "s1", "sub": True},
            {"kind": "compact", "ts": "2026-09-21T02:00:00.000Z", "session": "s1",
             "trigger": "auto", "pre": 900, "post": 100},
        ]
        whole = agg.merge([agg.fold(events)])

        incremental = agg.merge([agg.fold(events[:3]), agg.fold(events[3:])])

        self.assertEqual(whole, incremental)

    def test_totals_and_cache_ratio(self):
        s = agg.merge([agg.fold([
            usage("2026-09-22T01:00:00.000Z", input=10, output=10,
                  cache_read=80, cache_creation=0)])])
        self.assertEqual(s["totals"]["total"], 100)
        self.assertAlmostEqual(s["totals"]["cache_read_ratio"], 0.8)

    def test_zero_tokens_gives_zero_ratio_not_crash(self):
        s = agg.merge([agg.empty_partial()])
        self.assertEqual(s["totals"]["total"], 0)
        self.assertEqual(s["totals"]["cache_read_ratio"], 0.0)

    def test_days_sorted_ascending(self):
        s = agg.merge([agg.fold([usage("2026-09-22T01:00:00.000Z", input=1),
                                 usage("2026-09-20T01:00:00.000Z", input=1)])])
        days = [d["day"] for d in s["days"]]
        self.assertEqual(days, sorted(days))

    def test_since_filters_days_and_tokens(self):
        d20 = agg.local_day("2026-09-20T01:00:00.000Z")
        d22 = agg.local_day("2026-09-22T01:00:00.000Z")
        s = agg.merge([agg.fold([usage("2026-09-20T01:00:00.000Z", input=100),
                                 usage("2026-09-22T01:00:00.000Z", input=7)])],
                      since=d22)
        self.assertEqual([d["day"] for d in s["days"]], [d22])
        self.assertEqual(s["totals"]["input"], 7)
        self.assertNotIn(d20, [d["day"] for d in s["days"]])

    def test_since_filters_tool_ranking_too(self):
        """기간 탭을 바꿨는데 순위만 전체 기간이면 조용히 틀린 숫자가 된다."""
        d22 = agg.local_day("2026-09-22T01:00:00.000Z")
        p = agg.fold([tool("2026-09-20T01:00:00.000Z", "OldTool"),
                      tool("2026-09-22T01:00:00.000Z", "NewTool")])

        s = agg.merge([p], since=d22)

        self.assertEqual(s["tools"]["tools"], [["NewTool", 1]])
        self.assertEqual(s["totals"]["calls"], 1)

    def test_calls_total_matches_ranking_sum_under_since(self):
        d22 = agg.local_day("2026-09-22T01:00:00.000Z")
        p = agg.fold([tool("2026-09-20T01:00:00.000Z", "Bash"),
                      tool("2026-09-22T01:00:00.000Z", "Bash"),
                      tool("2026-09-22T01:00:00.000Z", "mcp__dbhub__execute_sql")])

        s = agg.merge([p], since=d22)

        ranked = sum(n for g in ("mcp", "skill", "tools")
                     for _, n in s["tools"][g])
        self.assertEqual(ranked, s["totals"]["calls"])

    def test_tools_ranked_descending(self):
        s = agg.merge([agg.fold([tool("2026-09-22T01:00:00.000Z", "Read"),
                                 tool("2026-09-22T01:00:00.000Z", "Bash"),
                                 tool("2026-09-22T01:00:00.000Z", "Bash")])])
        self.assertEqual(s["tools"]["tools"][0], ["Bash", 2])
        self.assertEqual(s["tools"]["tools"][1], ["Read", 1])

    def test_same_session_across_files_is_merged_not_doubled(self):
        # Review Focus 3: resume하면 같은 sessionId가 여러 파일에 걸친다
        a = agg.fold([usage("2026-09-22T01:00:00.000Z", session="s1",
                            input=1, cache_read=100)])
        b = agg.fold([usage("2026-09-22T05:00:00.000Z", session="s1",
                            input=1, cache_read=900)])

        s = agg.merge([a, b])

        self.assertEqual(len(s["sessions"]), 1)
        self.assertEqual(s["sessions"]["s1"]["last_ts"], "2026-09-22T05:00:00.000Z")
        self.assertEqual(s["sessions"]["s1"]["last_ctx"], 901)
        self.assertEqual(s["totals"]["input"], 2)

    def test_sidechain_tokens_survive_merge(self):
        a = agg.fold([usage("2026-09-22T01:00:00.000Z", session="s1", input=7,
                            sub=True)])
        b = agg.fold([usage("2026-09-22T01:00:00.000Z", session="s1", input=3)])

        s = agg.merge([a, b])

        self.assertEqual(s["totals"]["input"], 10)   # 총계에는 둘 다
        self.assertEqual(s["sessions"]["s1"]["last_ctx"], 3)  # 카드는 부모 것만

    def test_compact_stats(self):
        s = agg.merge([agg.fold([
            {"kind": "compact", "ts": "2026-09-20T05:00:00.000Z", "session": "s1",
             "trigger": "auto", "pre": 1000, "post": 100},
            {"kind": "compact", "ts": "2026-09-21T05:00:00.000Z", "session": "s1",
             "trigger": "manual", "pre": 500, "post": 100},
        ])])
        self.assertEqual(s["compacts"]["count"], 2)
        self.assertEqual(s["compacts"]["auto"], 1)
        self.assertEqual(s["compacts"]["manual"], 1)
        self.assertEqual(s["compacts"]["dropped_total"], 1300)
        self.assertEqual(s["compacts"]["dropped_avg"], 650)

    def test_compact_stats_with_none(self):
        s = agg.merge([agg.empty_partial()])
        self.assertEqual(s["compacts"]["count"], 0)
        self.assertEqual(s["compacts"]["dropped_avg"], 0)


if __name__ == "__main__":
    unittest.main()
