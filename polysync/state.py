"""
polysync/state.py — Read/write state.json, compare package revisions.

state.json schema:
{
    "<polygon_id_str>": {
        "package_id": 12345,
        "last_synced_at": "2026-07-05T06:00:00Z"
    },
    ...
}

All polygon_id keys are stored as strings (JSON object keys must be strings).
"""

import json
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_DEFAULT_STATE: dict = {}


def load_state(state_path: str) -> dict:
    """Load state.json from disk.

    Returns an empty dict if the file does not exist yet (first run).
    Raises json.JSONDecodeError if the file is present but malformed.
    """
    if not os.path.exists(state_path):
        return dict(_DEFAULT_STATE)
    with open(state_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_state(state_path: str, state: dict) -> None:
    """Atomically write state dict to state_path (write + rename)."""
    tmp_path = state_path + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write('\n')
    os.replace(tmp_path, state_path)
    log.debug("State saved to %s", state_path)


def get_problem_state(state: dict, polygon_id) -> dict | None:
    """Return the stored state entry for polygon_id, or None if never synced."""
    return state.get(str(polygon_id))


def is_up_to_date(state: dict, polygon_id, latest_package_id: int) -> bool:
    """Return True if the stored package_id matches latest_package_id."""
    entry = get_problem_state(state, polygon_id)
    if entry is None:
        return False
    return entry.get('package_id') == latest_package_id


def update_problem_state(state: dict, polygon_id, package_id: int) -> None:
    """Update state in-memory with the new package_id and current UTC timestamp.

    Call save_state() afterwards to persist to disk.
    """
    state[str(polygon_id)] = {
        'package_id':    package_id,
        'last_synced_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    }
