import datetime
import unittest

from httplint.message import HttpResponseLinter


class TestLinterIntegration(unittest.TestCase):
    def test_linter_base_uri_setting(self) -> None:
        """Test that we can set and retrieve base_uri on the linter."""
        linter = HttpResponseLinter()
        # Default behavior check (getattr default)
        self.assertEqual(getattr(linter, "base_uri", None), "")

        # Setting it
        test_uri = "http://example.com/"
        linter.base_uri = test_uri
        self.assertEqual(linter.base_uri, test_uri)

    def test_linter_start_time_setting(self) -> None:
        """Test setting start_time (used for Age calculation etc)."""
        linter = HttpResponseLinter()
        now = datetime.datetime.now().timestamp()
        linter.start_time = now
        self.assertEqual(linter.start_time, now)


if __name__ == "__main__":
    unittest.main()
