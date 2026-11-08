"""单测：整体唯一分配（匈牙利）、列序对齐、半身图配对。

跑法：python run_tests.py
"""

from __future__ import annotations

import itertools
import unittest

import numpy as np

from strinova_star.avatars import HalfbodyMatcher, assign, head_window, imread, solve


def brute_force(matrix: np.ndarray, maximize: bool = True) -> float:
    """穷举最优指派的总分，只用于小矩阵对拍。

    行多于列时只分配 min(行, 列) 行，其余行留空（和 solve() 的约定一致）。
    """
    rows, cols = matrix.shape
    pairs = min(rows, cols)
    best = float("-inf") if maximize else float("inf")
    for chosen in itertools.combinations(range(rows), pairs):
        for perm in itertools.permutations(range(cols), pairs):
            total = sum(matrix[r, c] for r, c in zip(chosen, perm))
            best = max(best, total) if maximize else min(best, total)
    return best


class SolveTest(unittest.TestCase):
    def test_matches_brute_force(self):
        rng = np.random.default_rng(7)
        for rows, cols in [(3, 3), (3, 5), (5, 3), (5, 5)]:
            for maximize in (True, False):
                matrix = rng.random((rows, cols))
                picks = solve(matrix, maximize)
                total = sum(matrix[i, j] for i, j in enumerate(picks) if j >= 0)
                self.assertAlmostEqual(total, brute_force(matrix, maximize), places=9)

    def test_rows_greater_than_cols(self):
        matrix = np.array([[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]])
        picks = solve(matrix, maximize=True)
        self.assertEqual(sorted(p for p in picks if p >= 0), [0, 1])

    def test_single_cell(self):
        self.assertEqual(solve(np.array([[0.25]]), maximize=True), [0])


class HalfbodyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matcher = HalfbodyMatcher()

    def test_names_and_count(self):
        self.assertEqual(len(self.matcher), 24)
        self.assertNotIn("_self", self.matcher.names)

    def test_head_window_shape(self):
        art = imread(self.matcher.folder / "令.png")
        window = head_window(art)
        self.assertAlmostEqual(window.shape[1] / window.shape[0], 85 / 52, places=2)

    def test_known_pair_margins(self):
        """白墨（绿发）与米雪儿·李（金发）曾经互串，这里钉住它们。"""
        for name in ("白墨", "米雪儿·李", "令"):
            crop = imread(self.matcher.folder / f"{name}.png")
            ranked = self.matcher.scores(crop)
            self.assertEqual(ranked[0].character, name, f"{name} -> {ranked[0]}")

    def test_self_recognised_every_time(self):
        for name in self.matcher.names:
            crop = imread(self.matcher.folder / f"{name}.png")
            self.assertEqual(self.matcher.scores(crop)[0].character, name)


class AssignOrderTest(unittest.TestCase):
    """列序必须和 matcher.names 对齐（曾经因为用排过序的 scores 而整体错位）。"""

    @classmethod
    def setUpClass(cls):
        cls.matcher = HalfbodyMatcher()

    def test_unique_names_for_distinct_crops(self):
        names = self.matcher.names[:5]
        crops = [imread(self.matcher.folder / f"{name}.png") for name in names]
        picks = assign(self.matcher, crops)
        self.assertEqual([p[0] for p in picks], names)

    def test_assign_returns_empty_for_garbage(self):
        blank = np.full((52, 85, 3), 128, np.uint8)
        picks = assign(self.matcher, [blank], min_score=0.6)
        self.assertEqual(picks[0][0], "")


if __name__ == "__main__":
    unittest.main()
