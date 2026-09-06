"""Unit tests for the flat keyword rule engine.

Ported from Usagi `src/keyword_rule_engine.py`. Critical behavior:

  - ASCII keywords use **token boundary** regex to avoid false hits
    like "Q1" matching "Q1R5".
  - CJK / non-ASCII keywords use **substring** matching.
  - Empty search text / empty keyword list produce is_recommended=False
    with an explanatory reason.
  - Exclusion prefix (``-foo``) forces is_recommended=False when the
    exclusion matches.
"""
from __future__ import annotations

import unittest

from cd_monitor.core.keyword_rule_engine import (
    build_search_text,
    build_search_text_from_strings,
    evaluate_keyword_rules,
    normalize_text,
    split_required_excluded,
    to_legacy_pair,
)


class KeywordRuleEngineTests(unittest.TestCase):
    # ---------- normalize_text ----------

    def test_normalize_text_lowercases_and_collapses_whitespace(self):
        self.assertEqual(normalize_text("  Hello   World  "), "hello world")
        self.assertEqual(normalize_text(""), "")
        self.assertEqual(normalize_text(None or ""), "")

    # ---------- ASCII token boundary ----------

    def test_ascii_keyword_does_not_match_as_substring_of_longer_token(self):
        """Critical: Q1 must NOT match Q1R5 (Usagi behavior)."""
        r = evaluate_keyword_rules(["Q1"], "this is the Q1R5 model only")
        self.assertFalse(r["is_recommended"])
        self.assertEqual(r["matched_keywords"], [])
        self.assertEqual(r["keyword_hit_count"], 0)

    def test_ascii_keyword_matches_standalone_token(self):
        r = evaluate_keyword_rules(["Q1"], "model Q1 in stock")
        self.assertTrue(r["is_recommended"])
        self.assertEqual(r["matched_keywords"], ["q1"])

    def test_ascii_keyword_matches_at_end_of_text(self):
        r = evaluate_keyword_rules(["Q1"], "we have Q1")
        self.assertTrue(r["is_recommended"])

    def test_ascii_keyword_matches_at_start_of_text(self):
        r = evaluate_keyword_rules(["Q1"], "Q1 model")
        self.assertTrue(r["is_recommended"])

    def test_case_insensitive_match_for_ascii(self):
        r = evaluate_keyword_rules(["macbook"], "MACBOOK air m1 for sale")
        self.assertTrue(r["is_recommended"])
        self.assertEqual(r["matched_keywords"], ["macbook"])

    # ---------- CJK / non-ASCII substring ----------

    def test_cjk_keyword_uses_substring_match(self):
        r = evaluate_keyword_rules(["アイドルマスター"], "the アイドルマスター CD is here")
        self.assertTrue(r["is_recommended"])
        self.assertEqual(r["matched_keywords"], ["アイドルマスター"])

    def test_cjk_keyword_with_partial_match_still_hits(self):
        r = evaluate_keyword_rules(["初回限定盤"], "初回限定盤B type CD")
        self.assertTrue(r["is_recommended"])

    def test_mixed_keyword_uses_substring_match(self):
        r = evaluate_keyword_rules(["SRCL-3520"], "新品 SRCL-3520 到货")
        self.assertTrue(r["is_recommended"])

    # ---------- Exclusion prefix ----------

    def test_excluded_keyword_forces_not_recommended_when_no_required_match(self):
        r = evaluate_keyword_rules(["-韩版"], "韩版 CD high quality")
        self.assertFalse(r["is_recommended"])
        self.assertIn("排除", r["reason"])

    def test_excluded_keyword_overrides_required_match(self):
        r = evaluate_keyword_rules(
            ["初回限定盤", "-韩版"],
            "初回限定盤 韩版 high quality",
        )
        self.assertFalse(r["is_recommended"])
        self.assertIn("排除", r["reason"])
        # The required match still shows in matched_keywords for visibility.
        self.assertEqual(r["matched_keywords"], ["初回限定盤"])

    def test_required_keyword_hits_without_exclusion_match_returns_recommended(self):
        r = evaluate_keyword_rules(
            ["初回限定盤", "-韩版"],
            "初回限定盤 日版 high quality",
        )
        self.assertTrue(r["is_recommended"])
        self.assertEqual(r["matched_keywords"], ["初回限定盤"])

    # ---------- Multi-keyword OR within required ----------

    def test_multiple_required_keywords_or_logic(self):
        r = evaluate_keyword_rules(
            ["アイドルマスター", "ラブライブ"],
            "selling ラブライブ CD",
        )
        self.assertTrue(r["is_recommended"])
        self.assertEqual(r["matched_keywords"], ["ラブライブ"])

    def test_multiple_required_keywords_dedup(self):
        r = evaluate_keyword_rules(
            ["foo", "foo", "FOO", "Foo"],
            "foo bar baz",
        )
        self.assertTrue(r["is_recommended"])
        # normalize_text dedupes case-insensitively.
        self.assertEqual(r["matched_keywords"], ["foo"])

    # ---------- Empty handling ----------

    def test_empty_keyword_list_returns_not_recommended(self):
        r = evaluate_keyword_rules([], "anything")
        self.assertFalse(r["is_recommended"])
        self.assertEqual(r["reason"], "未配置关键词规则。")
        self.assertEqual(r["matched_keywords"], [])
        self.assertEqual(r["keyword_hit_count"], 0)

    def test_empty_search_text_returns_not_recommended(self):
        r = evaluate_keyword_rules(["foo"], "")
        self.assertFalse(r["is_recommended"])
        self.assertEqual(r["reason"], "可匹配文本为空，关键词规则无法执行。")

    def test_whitespace_only_search_text_treated_as_empty(self):
        r = evaluate_keyword_rules(["foo"], "   \n\t  ")
        self.assertFalse(r["is_recommended"])
        self.assertEqual(r["keyword_hit_count"], 0)

    def test_keyword_with_only_whitespace_is_ignored(self):
        r = evaluate_keyword_rules(["   ", "foo"], "foo bar")
        self.assertTrue(r["is_recommended"])

    # ---------- split helpers ----------

    def test_split_required_excluded_basic(self):
        required, excluded = split_required_excluded(
            ["foo", "-bar", "baz", "-qux"]
        )
        self.assertEqual(required, ["foo", "baz"])
        self.assertEqual(excluded, ["bar", "qux"])

    def test_split_required_excluded_empty_and_dash_only(self):
        required, excluded = split_required_excluded(["", "-", "foo", "  "])
        self.assertEqual(required, ["foo"])
        self.assertEqual(excluded, [])

    def test_to_legacy_pair_alias(self):
        required, excluded = to_legacy_pair(["foo", "-bar"])
        self.assertEqual(required, ["foo"])
        self.assertEqual(excluded, ["bar"])

    # ---------- build_search_text helpers ----------

    def test_build_search_text_from_strings_concatenates(self):
        s = build_search_text_from_strings(
            "title here", "", "  description  ", None or ""
        )
        self.assertEqual(s, "title here description")

    def test_build_search_text_walks_nested_record(self):
        record = {
            "商品信息": {
                "商品标题": "IDOLMASTER CD",
                "商品ID": "12345",
                "nested": {"inner": "value"},
            },
            "卖家信息": {
                "卖家昵称": "user_a",
                "等级": 3,
            },
        }
        text = build_search_text(record)
        self.assertIn("idolmaster cd", text)
        self.assertIn("12345", text)
        self.assertIn("user_a", text)
        self.assertIn("3", text)  # int coerced to str

    def test_build_search_text_handles_missing_keys(self):
        # Empty / missing keys must not crash.
        self.assertEqual(build_search_text({}), "")
        self.assertEqual(build_search_text({"商品信息": None}), "")

    # ---------- Result schema ----------

    def test_result_schema_matches_ai_analysis(self):
        """Downstream consumers (save / notify) depend on this exact shape."""
        r = evaluate_keyword_rules(["foo"], "foo bar")
        self.assertEqual(
            set(r.keys()),
            {"analysis_source", "is_recommended", "reason",
             "matched_keywords", "keyword_hit_count"},
        )
        self.assertEqual(r["analysis_source"], "keyword")
        self.assertIsInstance(r["is_recommended"], bool)
        self.assertIsInstance(r["matched_keywords"], list)
        self.assertIsInstance(r["keyword_hit_count"], int)


if __name__ == "__main__":
    unittest.main()
