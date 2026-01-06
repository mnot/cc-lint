import unittest
from httplint.note import Note, levels, categories

class MockNote(Note):
    category = categories.GENERAL
    level = levels.WARN
    summary = "A test note"
    text = "This note has var %(foo)s."

class TestNoteVars(unittest.TestCase):
    def test_note_vars_direct_assignment(self):
        """Test setting variables as attributes."""
        n = MockNote("subject1")
        n.foo = "bar"
        
        # Verify vars() captures instance variables
        n_vars = vars(n)
        self.assertIn("foo", n_vars)
        self.assertEqual(n_vars["foo"], "bar")

    def test_note_vars_dict(self):
        """Test setting variables in .vars dictionary if supported/used."""
        n = MockNote("subject2")
        # Simulating how httplint might store vars if not directly attributes
        # (Though strictly Note class uses attributes for formatting)
        n.vars = {'foo': 'baz'}
        
        self.assertEqual(n.vars['foo'], 'baz')

    def test_stats_collector_logic_simulation(self):
        """Simulate the logic used in StatsCollector to extract vars."""
        n = MockNote("subject3")
        n.foo = "value1"
        n.vars = {'bar': 'value2'} # Hybrid approach simulation
        
        extracted_vars = {}
        filtered_keys = ['vars', 'subnotes', 'subject', 'field_type', 'message_type']
        
        # Logic from stats.py
        for k, v in vars(n).items():
            if k not in filtered_keys:
                extracted_vars[k] = str(v)
        if hasattr(n, 'vars'):
            for k, v in n.vars.items():
                if k not in filtered_keys:
                    extracted_vars[k] = str(v)
                    
        self.assertIn('foo', extracted_vars)
        self.assertEqual(extracted_vars['foo'], 'value1')
        self.assertIn('bar', extracted_vars)
        self.assertEqual(extracted_vars['bar'], 'value2')

if __name__ == '__main__':
    unittest.main()
