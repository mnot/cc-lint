"""Tests for cc_lint.note_cooccur note-specific normalisation (issue #7)."""

import unittest

from cc_lint.cooccur import EMPTY_BUNDLE_LABEL
from cc_lint.note_cooccur import (
    NOTE_COOCCUR_SEVERITIES,
    note_bundle_key,
    note_pair_keys,
)


class TestNoteBundleKey(unittest.TestCase):
    def test_empty_fired_set_is_label(self) -> None:
        self.assertEqual(note_bundle_key([]), EMPTY_BUNDLE_LABEL)

    def test_sorted_and_deduped(self) -> None:
        self.assertEqual(
            note_bundle_key(["CC_CONFLICTING", "BAD_SYNTAX", "BAD_SYNTAX"]),
            "BAD_SYNTAX, CC_CONFLICTING",
        )


class TestNotePairKeys(unittest.TestCase):
    def test_all_pairs_when_no_lineage(self) -> None:
        pairs = list(note_pair_keys(["a", "b", "c"], set()))
        self.assertEqual(pairs, ["a, b", "a, c", "b, c"])

    def test_lineage_pairs_excluded(self) -> None:
        # (a, b) is a parent/child edge; the sibling/cross pairs survive.
        lineage = {frozenset(("a", "b"))}
        pairs = list(note_pair_keys(["a", "b", "c"], lineage))
        self.assertEqual(pairs, ["a, c", "b, c"])

    def test_single_note_has_no_pairs(self) -> None:
        self.assertEqual(list(note_pair_keys(["a"], set())), [])


class TestSeverityKnob(unittest.TestCase):
    def test_defaults_to_defects(self) -> None:
        self.assertEqual(NOTE_COOCCUR_SEVERITIES, frozenset({"bad", "warn"}))


if __name__ == "__main__":
    unittest.main()
