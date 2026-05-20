"""Behavioural tests for cc_lint.stats.StatsCollector."""

# pylint: disable=disallowed-name,invalid-name,attribute-defined-outside-init
import unittest
from typing import Any, cast

from httplint import HttpResponseLinter
from httplint.note import Note, categories, levels

from cc_lint.hll import hll_estimate
from cc_lint.stats import StatsCollector


def _attach_note(linter: HttpResponseLinter, note: Note) -> None:
    """Push a Note instance directly onto the linter's notes list.

    HttpResponseLinter exposes notes via a Protocol that doesn't promise the
    underlying list interface; the runtime object is a UserList. Cast to Any
    so this test helper isn't tied to the protocol shape.
    """
    cast(Any, linter.notes).data.append(note)


class _WarnNote(Note):
    category = categories.GENERAL
    level = levels.WARN
    summary = ""
    text = ""


class _InfoNote(Note):
    category = categories.GENERAL
    level = levels.INFO
    summary = ""
    text = ""


def _linter_for(url: str) -> HttpResponseLinter:
    linter = HttpResponseLinter()
    linter.base_uri = url
    return linter


class TestStatsCollectorAccumulation(unittest.TestCase):
    def test_warn_notes_counted_info_notes_ignored(self) -> None:
        stats = StatsCollector()
        for note_cls, url in [
            (_WarnNote, "http://a.example/"),
            (_WarnNote, "http://b.example/"),
            (_InfoNote, "http://c.example/"),
        ]:
            linter = _linter_for(url)
            # Inject a fake note into the linter's notes list so process_linter
            # can read it.
            note: Any = note_cls("s")
            _attach_note(linter, note)
            stats.process_linter(linter)
        self.assertEqual(stats.total_responses, 3)
        self.assertIn(_WarnNote.__name__, stats.note_data)
        self.assertEqual(stats.note_data[_WarnNote.__name__]["count"], 2)
        self.assertNotIn(_InfoNote.__name__, stats.note_data)

    def test_samples_deduped_by_site(self) -> None:
        stats = StatsCollector()
        for url in [
            "http://example.com/a",
            "http://www.example.com/b",  # same site after www-strip
            "http://other.com/c",
        ]:
            linter = _linter_for(url)
            _attach_note(linter, _WarnNote("s"))
            stats.process_linter(linter)
        samples = stats.note_data[_WarnNote.__name__]["samples"]
        sites = {s.get("site") for s in samples}
        self.assertEqual(sites, {"example.com", "other.com"})

    def test_sample_sites_gate_blocks_other_sites(self) -> None:
        # Only "big.example" is in the sample ceiling; small.example responses
        # still contribute to counts but not to the sample list.
        stats = StatsCollector(sample_sites={"big.example"})
        for url in [
            "http://big.example/x",
            "http://small.example/y",
            "http://small.example/z",
        ]:
            linter = _linter_for(url)
            _attach_note(linter, _WarnNote("s"))
            stats.process_linter(linter)
        data = stats.note_data[_WarnNote.__name__]
        self.assertEqual(data["count"], 3)
        sites = {s.get("site") for s in data["samples"]}
        self.assertEqual(sites, {"big.example"})

    def test_per_note_hll_grows_with_distinct_sites(self) -> None:
        stats = StatsCollector()
        for i in range(50):
            linter = _linter_for(f"http://site-{i}.example/")
            _attach_note(linter, _WarnNote("s"))
            stats.process_linter(linter)
        note = stats.note_data[_WarnNote.__name__]
        self.assertIn("sites_hll", note)
        # Coarse-signal HLL: just verify the estimate is in the same order of
        # magnitude as 50.
        estimate = hll_estimate(note["sites_hll"])
        self.assertGreater(estimate, 20)
        self.assertLess(estimate, 200)

    def test_to_dict_carries_sites_hll_and_note_data(self) -> None:
        stats = StatsCollector()
        linter = _linter_for("http://a.example/")
        _attach_note(linter, _WarnNote("s"))
        stats.process_linter(linter)
        data = stats.to_dict()
        self.assertEqual(data["total_responses"], 1)
        self.assertIn("sites_hll", data)
        self.assertIn(_WarnNote.__name__, data["notes"])


if __name__ == "__main__":
    unittest.main()
