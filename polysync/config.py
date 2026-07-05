"""
polysync/config.py — Load and validate sync_config.yml.

Expected schema:

    defaults:
      group: "Uncategorized"
      type: "uncategorized"
      languages: [CPP17, CPP20, PY3, PYPY3, JAVA]
      partial: false
      public: false

    problems:
      - polygon_id: 123456
        code: hhy-jam       # required; must match ^[a-z0-9_]+$
        points: 100
        partial: true
        group: "HHY Contest 2026"
      - polygon_id: 234567
        code: another-one
        locked: true        # if true, skip this problem even if revision changed

Validation rules enforced at load time:
  - `code` is required for every problem entry.
  - `code` must match ^[a-z0-9_]+$.
  - `code` values must be unique across all entries.
  - `polygon_id` is required for every problem entry.
  - Raises ConfigError immediately so the caller never gets a half-validated config.
"""

import logging
import re

import yaml

log = logging.getLogger(__name__)

CODE_PATTERN = re.compile(r'^[a-z0-9_]+$')


class ConfigError(Exception):
    """Raised when sync_config.yml fails validation."""


def load_config(config_path: str) -> dict:
    """Parse and validate sync_config.yml.

    Returns a dict:
        {
            'defaults': {...},
            'problems': [
                {
                    'polygon_id': int,
                    'code': str,
                    'points': float,
                    'partial': bool,
                    'public': bool,
                    'group': str,
                    'type': str,
                    'languages': list[str],
                    'locked': bool,   # default False
                },
                ...
            ]
        }

    All problem entries have defaults merged in so callers don't need to
    handle missing keys.
    """
    with open(config_path, 'r', encoding='utf-8') as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ConfigError(f"sync_config.yml must be a YAML mapping, got: {type(raw)}")

    defaults = raw.get('defaults', {})
    problems_raw = raw.get('problems', [])

    if not isinstance(problems_raw, list):
        raise ConfigError("'problems' must be a YAML list.")

    merged_problems = []
    seen_codes: dict[str, int] = {}   # code → first-occurrence index

    for idx, entry in enumerate(problems_raw):
        if not isinstance(entry, dict):
            raise ConfigError(f"problems[{idx}] must be a mapping, got: {type(entry)}")

        # --- polygon_id ---
        if 'polygon_id' not in entry:
            raise ConfigError(f"problems[{idx}] missing required field 'polygon_id'.")
        polygon_id = entry['polygon_id']
        if not isinstance(polygon_id, int):
            raise ConfigError(
                f"problems[{idx}].polygon_id must be an integer, got: {polygon_id!r}"
            )

        # --- code ---
        if 'code' not in entry:
            raise ConfigError(f"problems[{idx}] (polygon_id={polygon_id}) missing required field 'code'.")
        code = entry['code']
        if not isinstance(code, str) or not CODE_PATTERN.match(code):
            raise ConfigError(
                f"problems[{idx}].code={code!r} does not match ^[a-z0-9_]+$. "
                "Use only lowercase letters, digits, and underscores."
            )

        # --- uniqueness ---
        if code in seen_codes:
            raise ConfigError(
                f"Duplicate code '{code}' at problems[{idx}] — "
                f"first seen at problems[{seen_codes[code]}]. "
                "Each problem must have a unique code."
            )
        seen_codes[code] = idx

        # --- merge defaults ---
        merged: dict = {
            'polygon_id': polygon_id,
            'code':       code,
            'points':     float(entry.get('points',   defaults.get('points',   100))),
            'partial':    bool( entry.get('partial',  defaults.get('partial',  False))),
            'public':     bool( entry.get('public',   defaults.get('public',   False))),
            'group':      str(  entry.get('group',    defaults.get('group',    'Uncategorized'))),
            'type':       str(  entry.get('type',     defaults.get('type',     'uncategorized'))),
            'languages':  list( entry.get('languages', defaults.get('languages', [
                'C', 'C11', 'CPP03', 'CPP11', 'CPP14', 'CPP17', 'CPP20',
                'JAVA', 'JAVA8', 'KOTLIN', 'PAS', 'PY2', 'PY3', 'PYPY', 'PYPY3',
            ]))),
            'locked':     bool( entry.get('locked', False)),
        }
        merged_problems.append(merged)

    return {'defaults': defaults, 'problems': merged_problems}
