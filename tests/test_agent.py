"""Agent loop driven by a scripted fake chat model and synthetic tool results (no LLM, Qloo or Nominatim calls)."""
import json
import unittest
from unittest import mock

import agent
import qloo


def call(name, args, id_="c1"):
    raw = args if isinstance(args, str) else json.dumps(args)
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": id_, "type": "function", "function": {"name": name, "arguments": raw}}]}


def final(text):
    return {"role": "assistant", "content": text}


class FakeChat:
    """Returns the scripted assistant messages in order and keeps what the model was shown."""

    def __init__(self, *script, repeat_last=False):
        self.script, self.repeat_last, self.seen = list(script), repeat_last, []

    def __call__(self, messages):
        self.seen.append([dict(m) for m in messages])
        if self.repeat_last and len(self.script) == 1:
            return self.script[0]
        return self.script.pop(0)


def places(*rows):
    return {"results": [{"name": n, "location": {"latitude": lat, "longitude": lon}, "query": {"affinity": w}}
                        for n, lat, lon, w in rows]}


VERDICT = '```json\n{"taste_match": {"score": 80, "reason": "Tags over-index"}, "competition_gap": {"score": 40, "reason": "Two look-alikes"}}\n```'
MEMO = "## Verdict\nGood fit with changes.\n\n## Evidence\n- Sample Cafe\n\n## Limits\nAggregate taste only.\n\n" + VERDICT


class AgentLoop(unittest.TestCase):
    def setUp(self):
        fakes = {
            "find_tags": lambda q, limit=8: {"results": [{"name": "Wine bar", "id": "urn:tag:sample:wine_bar"}]},
            "area_taste": lambda loc, demo=None: places(("Sample Cafe", 40.70, -73.95, 0.9), ("Sample Deli", 40.71, -73.94, 0.8)),
            "recommend_places": lambda loc, tags, demo=None: places(("Rival Bar", 40.705, -73.952, 0.7)),
            "where_popular": lambda place, loc: places((None, 40.702, -73.948, 0.95)),
        }
        for name, fn in fakes.items():
            p = mock.patch.object(qloo, name, side_effect=fn)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch("geo.urllib.request.urlopen", side_effect=AssertionError("no geocoding expected"))
        p.start()
        self.addCleanup(p.stop)

    def test_full_run_order_and_deterministic_scorecard(self):
        chat = FakeChat(call("find_tags", {"query": "wine bar"}), call("area_taste", {"location": "Brooklyn"}),
                        call("competitors", {"location": "Brooklyn", "tag_urns": ["urn:tag:sample:wine_bar"]}),
                        call("hotspots", {"place": "Rival Bar", "location": "Brooklyn"}), call("site_math", {}), final(MEMO))
        events = list(agent.run("wine bar", "Brooklyn", chat_fn=chat))
        self.assertEqual([k for k, _ in events], ["step"] * 5 + ["geo", "scorecard", "memo"])
        self.assertEqual([d["tool"] for k, d in events if k == "step"],
                         ["find_tags", "area_taste", "competitors", "hotspots", "site_math"])
        geo_payload, sc, memo = events[5][1], events[6][1], events[7][1]
        oi = geo_payload["recommended"]["opportunity_index"]
        self.assertEqual([b["score"] for b in sc["bars"]], [80, 40, oi])
        self.assertEqual(sc["fit"], int((80 + 40 + oi) / 3 + 0.5))
        self.assertTrue(memo.startswith(f"## Verdict: {sc['call']} (fit score {sc['fit']})"))
        self.assertIn("## Scorecard\n- Taste match: 80/100 - Tags over-index", memo)
        self.assertIn("## Limits", memo)
        self.assertNotIn("```", memo)

    def test_malformed_arguments_are_returned_to_the_model(self):
        chat = FakeChat(call("find_tags", '{"query": "wine'), call("find_tags", "[1, 2]", "c2"),
                        call("find_tags", {"query": "wine bar"}, "c3"), final(MEMO))
        events = list(agent.run("wine bar", "Brooklyn", chat_fn=chat))
        steps = [d for k, d in events if k == "step"]
        self.assertEqual([s["error"] for s in steps], ["lookup failed", "lookup failed", None])
        tool_msgs = [m for m in chat.seen[-1] if m["role"] == "tool"]
        self.assertIn("not valid JSON", tool_msgs[0]["content"])
        self.assertIn("JSON object", tool_msgs[1]["content"])
        self.assertEqual([m["tool_call_id"] for m in tool_msgs], ["c1", "c2", "c3"])
        self.assertEqual(qloo.find_tags.call_count, 1)
        self.assertEqual(events[-1][0], "memo")

    def test_step_limit_is_respected(self):
        chat = FakeChat(call("find_tags", {"query": "wine"}), repeat_last=True)
        events = list(agent.run("wine bar", "Brooklyn", max_steps=3, chat_fn=chat))
        self.assertEqual(len(chat.seen), 3)
        self.assertEqual(events[-1], ("error", "step_limit"))
        self.assertEqual(sum(1 for k, _ in events if k == "step"), 3)

    def test_tool_errors_reach_the_model_but_not_the_browser(self):
        qloo.find_tags.side_effect = RuntimeError("harness stderr: internal detail")
        chat = FakeChat(call("find_tags", {"query": "wine"}), final(MEMO))
        events = list(agent.run("wine bar", "Brooklyn", chat_fn=chat))
        self.assertEqual(events[0][1]["error"], "lookup failed")
        self.assertNotIn("internal detail", json.dumps(events))
        self.assertIn("internal detail", chat.seen[-1][-1]["content"])

    def test_no_scorecard_without_valid_subscores(self):
        chat = FakeChat(final("## Verdict\nNot enough data.\n\n## Limits\nNone."))
        events = list(agent.run("wine bar", "Brooklyn", chat_fn=chat))
        self.assertEqual([k for k, _ in events], ["memo"])


class Verdict(unittest.TestCase):
    def test_json_block_is_validated_and_clamped(self):
        memo, v = agent.parse_verdict('Memo\n```json\n{"taste_match": {"score": 150, "reason": "  a \\n b "}, '
                                      '"competition_gap": {"score": "-5"}}\n```')
        self.assertEqual(memo, "Memo")
        self.assertEqual(v, {"taste_match": {"score": 100, "reason": "a b"}, "competition_gap": {"score": 0, "reason": ""}})

    def test_invalid_scores_are_dropped(self):
        _, v = agent.parse_verdict('```json\n{"taste_match": {"score": "high"}, "competition_gap": {"score": true}}\n```')
        self.assertIsNone(v)
        _, v = agent.parse_verdict('```json\n{"taste_match": 72.4, "competition_gap": NaN}\n```')
        self.assertEqual(v, {"taste_match": {"score": 72, "reason": ""}})

    def test_last_valid_block_wins_and_broken_json_is_ignored(self):
        text = '```json\n{"taste_match": 10}\n```\nmore\n```json\n{"taste_match": 90, oops}\n```'
        memo, v = agent.parse_verdict(text)
        self.assertEqual(v["taste_match"]["score"], 10)
        self.assertIn("oops", memo)

    def test_falls_back_to_scorecard_bullets(self):
        memo, v = agent.parse_verdict("## Scorecard\n- **Taste match**: 66/100 - strong tags\n- Competition gap: 50/100 - crowded")
        self.assertEqual(v["taste_match"], {"score": 66, "reason": "strong tags"})
        self.assertEqual(v["competition_gap"]["score"], 50)

    def test_no_verdict(self):
        self.assertEqual(agent.parse_verdict("just text"), ("just text", None))
        self.assertEqual(agent.parse_verdict(None), ("", None))


class Scorecard(unittest.TestCase):
    verdict = {"taste_match": {"score": 80, "reason": "a"}, "competition_gap": {"score": 60, "reason": "b"}}

    def test_location_from_opportunity_index_and_thresholds(self):
        site = {"recommended": {"opportunity_index": 70, "nearest_competitor_km": 0.4}}
        sc = agent.build_scorecard(self.verdict, site)
        self.assertEqual((sc["fit"], sc["call"], sc["complete"]), (70, "GO", True))
        self.assertEqual(sc["bars"][2]["source"], "site_math")
        self.assertIn("0.4 km", sc["bars"][2]["reason"])
        low = agent.build_scorecard(self.verdict, {"recommended": {"opportunity_index": 0}})
        self.assertEqual((low["fit"], low["call"]), (47, "ADJUST"))
        bad = agent.build_scorecard({"taste_match": {"score": 20, "reason": ""},
                                     "competition_gap": {"score": 30, "reason": ""}},
                                    {"recommended": {"opportunity_index": 30}})
        self.assertEqual((bad["fit"], bad["call"]), (27, "NO-GO"))

    def test_missing_site_gives_partial_scorecard_without_call(self):
        sc = agent.build_scorecard(self.verdict, None)
        self.assertEqual((sc["fit"], sc["complete"], len(sc["bars"])), (70, False, 2))
        self.assertEqual(sc["call"], "INCOMPLETE")
        self.assertIn("missing", sc["method"])
        one = agent.build_scorecard({"taste_match": {"score": 90, "reason": ""}}, {"recommended": {"opportunity_index": 90}})
        self.assertEqual(one["call"], "INCOMPLETE")
        self.assertIsNone(agent.build_scorecard(None, {"recommended": {"opportunity_index": 90}}))

    def test_memo_heading_and_scorecard_are_rewritten(self):
        sc = agent.build_scorecard(self.verdict, {"recommended": {"opportunity_index": 40}})
        memo = agent.memo_with_scorecard("## Verdict: GO (fit score 99)\nWhy.\n## Scorecard\n- Taste match: 99/100\n## Risks\n- r", sc)
        self.assertEqual(memo.count("## Scorecard"), 1)
        self.assertTrue(memo.startswith("## Verdict: ADJUST (fit score 60)\nWhy."))
        self.assertNotIn("99", memo)
        self.assertIn("## Risks", memo)

    def test_incomplete_memo_heading_says_partial_score(self):
        sc = agent.build_scorecard(self.verdict, None)
        memo = agent.memo_with_scorecard("## Verdict: GO (fit score 99)\nWhy.", sc)
        self.assertTrue(memo.startswith("## Verdict: INCOMPLETE (partial score 70, not a fit score)\nWhy."))
        self.assertNotIn("fit score 70", memo)


if __name__ == "__main__":
    unittest.main()
