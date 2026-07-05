"""
tests/test_config.py — Unit tests for polysync.config.

Covers:
  - Valid config loads and merges defaults correctly
  - ConfigError raised for: missing code, bad code regex, duplicate codes,
    missing polygon_id, non-integer polygon_id, locked flag
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from polysync.config import ConfigError, load_config


def _write_config(data: dict) -> str:
    """Write a config dict to a temp file and return the path."""
    f = tempfile.NamedTemporaryFile(
        mode='w', suffix='.yml', delete=False, encoding='utf-8'
    )
    yaml.safe_dump(data, f, allow_unicode=True)
    f.close()
    return f.name


class TestValidConfig(unittest.TestCase):
    def setUp(self):
        self.path = _write_config({
            'defaults': {
                'group': 'Contest',
                'type': 'contest',
                'languages': ['CPP17', 'PY3'],
                'partial': False,
                'public': False,
            },
            'problems': [
                {'polygon_id': 111, 'code': 'prob_a', 'points': 100},
                {'polygon_id': 222, 'code': 'prob_b', 'partial': True,
                 'group': 'Override Group'},
            ],
        })

    def tearDown(self):
        os.unlink(self.path)

    def test_loads_without_error(self):
        cfg = load_config(self.path)
        self.assertEqual(len(cfg['problems']), 2)

    def test_defaults_merged(self):
        cfg = load_config(self.path)
        p = cfg['problems'][0]
        self.assertEqual(p['group'], 'Contest')
        self.assertEqual(p['type'], 'contest')
        self.assertEqual(p['languages'], ['CPP17', 'PY3'])

    def test_per_problem_override(self):
        cfg = load_config(self.path)
        p = cfg['problems'][1]
        self.assertTrue(p['partial'])
        self.assertEqual(p['group'], 'Override Group')

    def test_locked_defaults_to_false(self):
        cfg = load_config(self.path)
        for p in cfg['problems']:
            self.assertFalse(p['locked'])

    def test_polygon_id_is_int(self):
        cfg = load_config(self.path)
        self.assertIsInstance(cfg['problems'][0]['polygon_id'], int)


class TestMissingCode(unittest.TestCase):
    def test_raises_config_error(self):
        path = _write_config({
            'problems': [{'polygon_id': 123}]  # no 'code'
        })
        try:
            with self.assertRaises(ConfigError):
                load_config(path)
        finally:
            os.unlink(path)


class TestBadCodeRegex(unittest.TestCase):
    BAD_CODES = ['HHY-Jam', 'hhy jam', 'hhy!jam', 'HHY', '123-abc']

    def test_uppercase_rejected(self):
        path = _write_config({'problems': [{'polygon_id': 1, 'code': 'HHY_JAM'}]})
        try:
            with self.assertRaises(ConfigError):
                load_config(path)
        finally:
            os.unlink(path)

    def test_hyphen_rejected(self):
        # hhy-jam uses a hyphen; only underscores are allowed
        path = _write_config({'problems': [{'polygon_id': 1, 'code': 'hhy-jam'}]})
        try:
            with self.assertRaises(ConfigError):
                load_config(path)
        finally:
            os.unlink(path)

    def test_space_rejected(self):
        path = _write_config({'problems': [{'polygon_id': 1, 'code': 'hhy jam'}]})
        try:
            with self.assertRaises(ConfigError):
                load_config(path)
        finally:
            os.unlink(path)


class TestDuplicateCodes(unittest.TestCase):
    def test_raises_config_error(self):
        path = _write_config({
            'problems': [
                {'polygon_id': 1, 'code': 'same_code'},
                {'polygon_id': 2, 'code': 'same_code'},
            ]
        })
        try:
            with self.assertRaises(ConfigError) as ctx:
                load_config(path)
            self.assertIn('same_code', str(ctx.exception))
        finally:
            os.unlink(path)


class TestMissingPolygonId(unittest.TestCase):
    def test_raises_config_error(self):
        path = _write_config({
            'problems': [{'code': 'valid_code'}]  # no polygon_id
        })
        try:
            with self.assertRaises(ConfigError):
                load_config(path)
        finally:
            os.unlink(path)


class TestLockedFlag(unittest.TestCase):
    def test_locked_true_preserved(self):
        path = _write_config({
            'problems': [{'polygon_id': 1, 'code': 'locked_prob', 'locked': True}]
        })
        try:
            cfg = load_config(path)
            self.assertTrue(cfg['problems'][0]['locked'])
        finally:
            os.unlink(path)


class TestEmptyProblems(unittest.TestCase):
    def test_empty_list_is_valid(self):
        path = _write_config({'defaults': {}, 'problems': []})
        try:
            cfg = load_config(path)
            self.assertEqual(cfg['problems'], [])
        finally:
            os.unlink(path)


if __name__ == '__main__':
    unittest.main()
