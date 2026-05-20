# pylint: disable=disallowed-name,invalid-name,attribute-defined-outside-init
import unittest
from typing import Any, Dict

from httplint.note import Note, categories, levels


class MockNote(Note):
    category = categories.GENERAL
    level = levels.WARN
    summary = "A test note"
    text = "This note has var %(foo)s."


class TestNoteVars(unittest.TestCase):
    def test_note_vars_direct_assignment(self) -> None:
        """Test setting variables as attributes."""
        note: Any = MockNote("subject1")
        note.foo = "bar"

        # Verify vars() captures instance variables
        note_vars = vars(note)
        self.assertIn("foo", note_vars)
        self.assertEqual(note_vars["foo"], "bar")

    def test_note_vars_dict(self) -> None:
        """Test setting variables in .vars dictionary if supported/used."""
        note: Any = MockNote("subject2")
        # Simulating how httplint might store vars if not directly attributes
        # (Though strictly Note class uses attributes for formatting)
        note.vars = {"foo": "baz"}

        self.assertEqual(note.vars["foo"], "baz")

    def test_stats_collector_logic_simulation(self) -> None:
        """Simulate the logic used in StatsCollector to extract vars."""
        note: Any = MockNote("subject3")
        note.foo = "value1"
        note.vars = {"bar": "value2"}  # Hybrid approach simulation

        extracted_vars: Dict[str, str] = {}
        filtered_keys = ["vars", "subnotes", "subject", "field_type", "message_type"]

        # Logic from stats.py
        for key, value in vars(note).items():
            if key not in filtered_keys:
                extracted_vars[key] = str(value)
        if hasattr(note, "vars"):
            for key, value in note.vars.items():
                if key not in filtered_keys:
                    extracted_vars[key] = str(value)

        self.assertIn("foo", extracted_vars)
        self.assertEqual(extracted_vars["foo"], "value1")
        self.assertIn("bar", extracted_vars)
        self.assertEqual(extracted_vars["bar"], "value2")


if __name__ == "__main__":
    unittest.main()
