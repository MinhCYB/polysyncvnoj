"""
polysync/polygon_api.py — Polygon API client.

Exports:
    polygon_sign            HMAC-style request signing
    polygon_call            Low-level signed POST helper
    fetch_latest_package_meta   Lightweight: returns {package_id, revision} without downloading
    download_polygon_package    Full: downloads and extracts the zip to dest_dir
    fetch_statement         Fetches problem.statements JSON
"""

import hashlib
import json
import os
import random
import string
import time
import urllib.parse
import urllib.request
import zipfile

POLYGON_API_BASE = 'https://polygon.codeforces.com/api'


def polygon_sign(method_name, params, api_key, api_secret):
    rand = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    params = dict(params)
    params['apiKey'] = api_key
    params['time'] = str(int(time.time()))
    sorted_params = '&'.join(f'{k}={v}' for k, v in sorted(params.items()))
    to_hash = f'{rand}/{method_name}?{sorted_params}#{api_secret}'
    sig_hash = hashlib.sha512(to_hash.encode('utf-8')).hexdigest()
    params['apiSig'] = rand + sig_hash
    return params


def polygon_call(method_name, params, api_key, api_secret, raw=False):
    signed = polygon_sign(method_name, params, api_key, api_secret)
    url = f'{POLYGON_API_BASE}/{method_name}'
    data = urllib.parse.urlencode(signed).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST')
    with urllib.request.urlopen(req) as resp:
        raw_bytes = resp.read()
    if raw:
        return raw_bytes
    result = json.loads(raw_bytes)
    if result.get('status') != 'OK':
        raise RuntimeError(f"Polygon API error on {method_name}: {result.get('comment')}")
    return result['result']


def fetch_latest_package_meta(problem_id, api_key, api_secret):
    """Cheap check: call problem.packages, return the latest READY package's
    metadata as {'package_id': int, 'revision': int}.

    Does NOT download the zip — intended to be called frequently for change
    detection without incurring download costs.

    Raises RuntimeError if no READY package exists.
    """
    packages = polygon_call(
        'problem.packages', {'problemId': problem_id}, api_key, api_secret
    )
    ready = [p for p in packages if p['state'] == 'READY']
    if not ready:
        raise RuntimeError(
            f"No READY package found for problem {problem_id}. "
            "Build a package on Polygon first (Package tab → Create package)."
        )
    latest = max(ready, key=lambda p: p['revision'])
    return {'package_id': latest['id'], 'revision': latest['revision']}


def download_polygon_package(problem_id, api_key, api_secret, dest_dir):
    """Download and extract the latest READY linux package to dest_dir/extracted/.

    Reuses fetch_latest_package_meta to get package_id so the listing logic
    is not duplicated.

    Returns: path to the extracted directory.
    """
    import logging
    log = logging.getLogger(__name__)

    log.info("[polygon] Fetching package list for problem %s...", problem_id)
    meta = fetch_latest_package_meta(problem_id, api_key, api_secret)
    package_id = meta['package_id']
    log.info("[polygon] Downloading package #%d (revision %d)...",
             package_id, meta['revision'])

    zip_bytes = polygon_call(
        'problem.package',
        {'problemId': problem_id, 'packageId': package_id, 'type': 'linux'},
        api_key, api_secret, raw=True,
    )
    # problem.package returns raw zip bytes on success, but on error it returns
    # a JSON body.  Detect that case by checking the first byte.
    if zip_bytes[:1] == b'{':
        result = json.loads(zip_bytes)
        raise RuntimeError(
            f"Polygon API error on problem.package: {result.get('comment')}"
        )

    zip_path = os.path.join(dest_dir, 'package.zip')
    with open(zip_path, 'wb') as f:
        f.write(zip_bytes)

    extract_dir = os.path.join(dest_dir, 'extracted')
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(extract_dir)
    log.info("[polygon] Extracted to %s", extract_dir)
    return extract_dir


def fetch_statement(problem_id, api_key, api_secret, lang='english'):
    """Fetch problem.statements and return the statement dict for the requested
    language (falls back to the first available language if the requested one
    is absent).  Returns None if no statements are available at all.
    """
    import logging
    log = logging.getLogger(__name__)

    log.info("[polygon] Fetching statement (problem.statements)...")
    statements = polygon_call(
        'problem.statements', {'problemId': problem_id}, api_key, api_secret
    )
    if not statements:
        return None
    if lang not in statements:
        lang = next(iter(statements))
    return statements[lang]
