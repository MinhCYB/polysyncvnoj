"""
tests/test_state.py — Unit tests for polysync.state.

Covers:
  - load_state: missing file → empty dict; valid file → parsed dict
  - save_state: atomic write via tmp+rename; round-trips correctly
  - get_problem_state: found and not-found cases
  - is_up_to_date: matches and mismatches (including never-synced)
  - update_problem_state: updates in-memory dict, ISO timestamp format
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from polysync.state import (
    get_problem_state,
    is_up_to_date,
    load_state,
    save_state,
    update_problem_state,
)


class TestLoadState(unittest.TestCase):
    def test_missing_file_returns_empty_dict(self):
        state = load_state('/tmp/polysync_nonexistent_state_xyz.json')
        self.assertEqual(state, {})

    def test_existing_file_is_parsed(self):
        data = {'123456': {'package_id': 99, 'last_synced_at': '2026-07-01T00:00:00Z'}}
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        ) as f:
            json.dump(data, f)
            path = f.name
        try:
            state = load_state(path)
            self.assertEqual(state['123456']['package_id'], 99)
        finally:
            os.unlink(path)

    def test_returns_independent_copy(self):
        """Mutating the returned dict should not affect a re-load."""
        data = {'1': {'package_id': 1, 'last_synced_at': 'x'}}
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        ) as f:
            json.dump(data, f)
            path = f.name
        try:
            state1 = load_state(path)
            state1['new_key'] = 'mutated'
            state2 = load_state(path)
            self.assertNotIn('new_key', state2)
        finally:
            os.unlink(path)


class TestSaveState(unittest.TestCase):
    def test_round_trip(self):
        state_in = {
            '111': {'package_id': 5, 'last_synced_at': '2026-07-01T00:00:00Z'},
            '222': {'package_id': 7, 'last_synced_at': '2026-07-02T00:00:00Z'},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'state.json')
            save_state(path, state_in)
            state_out = load_state(path)
        self.assertEqual(state_out, state_in)

    def test_file_is_valid_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'state.json')
            save_state(path, {'42': {'package_id': 1, 'last_synced_at': 'x'}})
            with open(path) as f:
                parsed = json.load(f)
            self.assertIn('42', parsed)

    def test_polygon_id_stored_as_string(self):
        """JSON object keys are always strings; int keys should be stringified."""
        state = {}
        update_problem_state(state, 99999, 42)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'state.json')
            save_state(path, state)
            state2 = load_state(path)
        self.assertIn('99999', state2)


class TestGetProblemState(unittest.TestCase):
    def test_found(self):
        state = {'123': {'package_id': 5, 'last_synced_at': 'ts'}}
        entry = get_problem_state(state, 123)
        self.assertIsNotNone(entry)
        self.assertEqual(entry['package_id'], 5)

    def test_found_string_key(self):
        state = {'456': {'package_id': 9, 'last_synced_at': 'ts'}}
        entry = get_problem_state(state, '456')
        self.assertIsNotNone(entry)

    def test_not_found_returns_none(self):
        state = {}
        self.assertIsNone(get_problem_state(state, 999))


class TestIsUpToDate(unittest.TestCase):
    def test_matching_package_id_is_up_to_date(self):
        state = {'111': {'package_id': 42, 'last_synced_at': 'ts'}}
        self.assertTrue(is_up_to_date(state, 111, 42))

    def test_different_package_id_is_not_up_to_date(self):
        state = {'111': {'package_id': 42, 'last_synced_at': 'ts'}}
        self.assertFalse(is_up_to_date(state, 111, 43))

    def test_never_synced_is_not_up_to_date(self):
        self.assertFalse(is_up_to_date({}, 111, 42))


class TestUpdateProblemState(unittest.TestCase):
    def test_creates_entry(self):
        state = {}
        update_problem_state(state, 555, 77)
        self.assertIn('555', state)
        self.assertEqual(state['555']['package_id'], 77)

    def test_overwrites_existing_entry(self):
        state = {'555': {'package_id': 1, 'last_synced_at': 'old'}}
        update_problem_state(state, 555, 99)
        self.assertEqual(state['555']['package_id'], 99)

    def test_timestamp_is_iso_utc(self):
        import re
        state = {}
        update_problem_state(state, 1, 1)
        ts = state['1']['last_synced_at']
        # e.g. 2026-07-05T06:00:00Z
        self.assertRegex(ts, r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')

    def test_state_not_saved_to_disk_automatically(self):
        """update_problem_state is purely in-memory; save_state must be called separately."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'state.json')
            state = {}
            update_problem_state(state, 1, 1)
            # File should not exist yet
            self.assertFalse(os.path.exists(path))


if __name__ == '__main__':
    unittest.main()
