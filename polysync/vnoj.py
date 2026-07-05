"""
polysync/vnoj.py — Push a converted problem to VNOJ via manage.py shell.

The payload dict is embedded directly into the Python script that is piped
through stdin to `docker exec -i <container> python3 manage.py shell`.

This avoids writing any temporary file into problems_dir and eliminates the
risk of leftover .json files if the process crashes between write and delete.

Approach:
  1. json.dumps(payload) → JSON string
  2. repr() → safe Python string literal (all special chars escaped)
  3. Prepend  `import json; payload = json.loads(<repr>)`  to the script body
  4. Pipe the full script via stdin to docker exec
"""

import json
import logging
import subprocess
import sys

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Script body executed inside the container
# (payload is injected at runtime as the first line — see push_to_vnoj)
# ---------------------------------------------------------------------------

_PROBLEM_SCRIPT_BODY = r'''
from judge.models import Problem, ProblemGroup, ProblemType, Language

group, _ = ProblemGroup.objects.get_or_create(
    name=payload['group'], defaults={'full_name': payload['group']})
ptype, _ = ProblemType.objects.get_or_create(
    name=payload['type'], defaults={'full_name': payload['type']})

problem, created = Problem.objects.get_or_create(
    code=payload['code'],
    defaults={
        'name':               payload['name'],
        'description':        payload['description'],
        'time_limit':         payload['time_limit'],
        'memory_limit':       payload['memory_limit'],
        'points':             payload['points'],
        'partial':            payload['partial'],
        'group':              group,
        'is_public':          payload['is_public'],
        'is_manually_managed': True,
    },
)
if not created:
    problem.name               = payload['name']
    problem.description        = payload['description']
    problem.time_limit         = payload['time_limit']
    problem.memory_limit       = payload['memory_limit']
    problem.points             = payload['points']
    problem.partial            = payload['partial']
    problem.is_manually_managed = True
    problem.save()

problem.types.set([ptype])
langs = Language.objects.filter(key__in=payload['languages'])
problem.allowed_languages.set(langs)

print(f"{'CREATED' if created else 'UPDATED'} problem code={problem.code} id={problem.id}")
'''


def _build_script(payload: dict) -> str:
    """Return the full Python script to execute inside the container.

    The payload is embedded as a JSON string assigned to `payload` at the very
    top of the script, so no file I/O is needed inside the container.

    repr() of a str produces a valid Python string literal with all quotes,
    backslashes, and non-ASCII characters properly escaped.
    """
    payload_json_repr = repr(json.dumps(payload))
    header = f"import json\npayload = json.loads({payload_json_repr})\n"
    return header + _PROBLEM_SCRIPT_BODY


def push_to_vnoj(problems_dir, site_container, payload):
    """Create or update a Problem on VNOJ by piping a Python script via
    `docker exec -i <site_container> python3 manage.py shell`.

    The payload dict is embedded inline — no temporary files are written.
    """
    log.info(
        "[vnoj] Creating/updating Problem '%s' via %s manage.py shell...",
        payload.get('code'), site_container,
    )
    script = _build_script(payload)

    result = subprocess.run(
        ['docker', 'exec', '-i', site_container,
         'python3', '/site/manage.py', 'shell'],
        input=script,
        capture_output=True,
        text=True,
    )
    if result.stdout:
        log.info(result.stdout.rstrip())
    if result.returncode != 0 or 'Traceback' in result.stderr:
        log.error(result.stderr)
        raise RuntimeError(
            f"Failed to create/update Problem '{payload.get('code')}' on VNOJ. "
            "See log above for traceback."
        )
