"""Tests for cc_lint.emr.warc_worker pickle round-trip."""

import os
import pickle
import tempfile
import unittest

from cc_lint.emr.warc_worker import WarcWorkerResult, load_warc_worker_result
from cc_lint.stats import StatsCollector


class TestWorkerResultPickle(unittest.TestCase):
    def test_round_trip_preserves_stats(self) -> None:
        stats = StatsCollector()
        result = WarcWorkerResult(
            stats=stats,
            records_seen=42,
            total_ms=1000,
            process_ms=600,
            iterator_ms=400,
        )
        with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
            tmp_path = tmp_file.name
            pickle.dump(result, tmp_file)
        try:
            loaded = load_warc_worker_result(tmp_path)
            self.assertEqual(loaded.records_seen, 42)
            self.assertEqual(loaded.total_ms, 1000)
            self.assertEqual(loaded.process_ms, 600)
            self.assertEqual(loaded.iterator_ms, 400)
            self.assertIsInstance(loaded.stats, StatsCollector)
        finally:
            os.unlink(tmp_path)

    def test_load_missing_file_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_warc_worker_result("/no/such/path")


if __name__ == "__main__":
    unittest.main()
