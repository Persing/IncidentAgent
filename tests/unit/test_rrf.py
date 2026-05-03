# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""Unit tests for Reciprocal Rank Fusion."""

from src.retrieval.bm25_index import reciprocal_rank_fusion


class TestReciprocalRankFusion:
    def test_single_list_order_preserved(self):
        result = reciprocal_rank_fusion(["a", "b", "c"])
        names = [r for r, _ in result]
        assert names == ["a", "b", "c"]

    def test_scores_decrease_with_rank(self):
        result = reciprocal_rank_fusion(["a", "b", "c"])
        scores = [s for _, s in result]
        assert scores[0] > scores[1] > scores[2]

    def test_agreement_boosts_score(self):
        # "a" appears first in both lists — should beat "b" and "c"
        result = reciprocal_rank_fusion(["a", "b", "c"], ["a", "c", "b"])
        names = [r for r, _ in result]
        assert names[0] == "a"

    def test_item_only_in_one_list(self):
        # "x" only in list 1; "y" only in list 2 — both still appear in output
        result = reciprocal_rank_fusion(["x", "a"], ["y", "a"])
        names = [r for r, _ in result]
        assert "x" in names
        assert "y" in names
        assert "a" in names

    def test_three_lists(self):
        result = reciprocal_rank_fusion(["a", "b"], ["b", "a"], ["a", "c"])
        names = [r for r, _ in result]
        # "a" appears 1st in two lists and 2nd in one → highest cumulative score
        assert names[0] == "a"

    def test_empty_lists(self):
        result = reciprocal_rank_fusion([], [])
        assert result == []

    def test_single_item_lists(self):
        result = reciprocal_rank_fusion(["only"], ["only"])
        assert len(result) == 1
        assert result[0][0] == "only"

    def test_rrf_formula_spot_check(self):
        # With k=60, rank-1 score = 1/(60+1) ≈ 0.01639
        result = reciprocal_rank_fusion(["a"])
        score = result[0][1]
        assert abs(score - 1 / 61) < 1e-9

    def test_custom_k(self):
        # Smaller k → larger score difference between rank 1 and rank 2
        result_k1 = reciprocal_rank_fusion(["a", "b"], k=1)
        result_k60 = reciprocal_rank_fusion(["a", "b"], k=60)
        diff_k1 = result_k1[0][1] - result_k1[1][1]
        diff_k60 = result_k60[0][1] - result_k60[1][1]
        assert diff_k1 > diff_k60

    def test_output_sorted_descending(self):
        result = reciprocal_rank_fusion(["c", "b", "a"], ["a", "b", "c"])
        scores = [s for _, s in result]
        assert scores == sorted(scores, reverse=True)
