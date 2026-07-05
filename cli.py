#!/usr/bin/env python3
"""
cli.py — polysync entrypoint.

Usage:
    python cli.py sync [--force] [--only CODE]
    python cli.py status
    python cli.py list-remote

Run `python cli.py <subcommand> --help` for details.
"""

import argparse
import logging
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths relative to this file
# ---------------------------------------------------------------------------

_REPO_DIR    = Path(__file__).parent
_CONFIG_PATH = _REPO_DIR / 'sync_config.yml'
_STATE_PATH  = _REPO_DIR / 'state.json'
_LOGS_DIR    = _REPO_DIR / 'logs'
_ENV_PATH    = _REPO_DIR / '.env'

_DEFAULT_PROBLEMS_DIR   = os.path.expanduser('~/vnoj-docker/dmoj/problems')
_DEFAULT_SITE_CONTAINER = 'vnoj_site'


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging(verbose: bool = False) -> None:
    """Configure root logger: stdout + daily log file under logs/."""
    _LOGS_DIR.mkdir(exist_ok=True)
    log_file = _LOGS_DIR / f"sync-{datetime.now(timezone.utc).strftime('%Y%m%d')}.log"

    level = logging.DEBUG if verbose else logging.INFO
    fmt   = '%(asctime)s %(levelname)-8s %(message)s'
    datefmt = '%Y-%m-%dT%H:%M:%SZ'

    root = logging.getLogger()
    root.setLevel(level)

    # stdout handler
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(level)
    sh.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    root.addHandler(sh)

    # file handler
    fh = logging.FileHandler(log_file, encoding='utf-8')
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    root.addHandler(fh)


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# .env loader (no external dependency)
# ---------------------------------------------------------------------------

def _load_env(env_path: Path) -> None:
    """Parse a simple KEY=VALUE .env file into os.environ.

    Skips blank lines and lines starting with #.
    Does NOT override existing environment variables (export takes priority).
    """
    if not env_path.exists():
        return
    with open(env_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            key   = key.strip()
            value = value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


# ---------------------------------------------------------------------------
# Credential helper
# ---------------------------------------------------------------------------

def _get_credentials() -> tuple[str, str]:
    _load_env(_ENV_PATH)
    api_key    = os.environ.get('POLYGON_API_KEY', '')
    api_secret = os.environ.get('POLYGON_API_SECRET', '')
    if not api_key or not api_secret:
        sys.exit(
            "Thiếu POLYGON_API_KEY / POLYGON_API_SECRET. "
            "Đặt trong .env hoặc export trước khi chạy."
        )
    return api_key, api_secret


# ---------------------------------------------------------------------------
# Single-problem sync pipeline
# ---------------------------------------------------------------------------

def _sync_one(problem: dict, api_key: str, api_secret: str,
               problems_dir: str, site_container: str,
               allow_zero_points: bool,
               wineprefix: str = None) -> None:
    """Run the full pipeline for a single problem entry from the config.

    Raises on any failure so the caller can catch and report.
    """
    from polysync.polygon_api import download_polygon_package, fetch_statement
    from polysync.convert import build_description_markdown, convert_package
    from polysync.vnoj import push_to_vnoj

    code = problem['code']
    problem_dir = os.path.join(problems_dir, code)

    with tempfile.TemporaryDirectory() as tmp:
        pkg_dir     = download_polygon_package(
            problem['polygon_id'], api_key, api_secret, tmp
        )
        statement   = fetch_statement(problem['polygon_id'], api_key, api_secret)
        description = build_description_markdown(statement)

        if os.path.exists(problem_dir):
            log.info("[convert] %s đã tồn tại, sẽ ghi đè test data.", problem_dir)
            shutil.rmtree(problem_dir)

        info = convert_package(
            pkg_dir, problem_dir,
            points_total=problem['points'],
            partial=problem['partial'],
            allow_zero_points=allow_zero_points,
            wineprefix=wineprefix,
        )

        payload = {
            'code':         code,
            'name':         info['name'],
            'description':  description,
            'time_limit':   info['time_limit'],
            'memory_limit': info['memory_limit_kb'],
            'points':       problem['points'],
            'partial':      problem['partial'],
            'group':        problem['group'],
            'type':         problem['type'],
            'is_public':    problem['public'],
            'languages':    problem['languages'],
        }
        push_to_vnoj(problems_dir, site_container, payload)

    log.info("✓ Synced: %s", code)


# ---------------------------------------------------------------------------
# Subcommand: sync
# ---------------------------------------------------------------------------

def cmd_sync(args) -> int:
    """Sync all (or one) problem(s).  Returns exit code (0 = all OK)."""
    from polysync.config import load_config, ConfigError
    from polysync.polygon_api import fetch_latest_package_meta
    from polysync.state import (
        load_state, save_state, is_up_to_date, update_problem_state
    )

    try:
        cfg = load_config(str(_CONFIG_PATH))
    except Exception as exc:
        log.error("Config error: %s", exc)
        return 1

    api_key, api_secret = _get_credentials()
    state = load_state(str(_STATE_PATH))

    problems = cfg['problems']
    if args.only:
        problems = [p for p in problems if p['code'] == args.only]
        if not problems:
            log.error("Không tìm thấy bài có code='%s' trong sync_config.yml.", args.only)
            return 1

    ok_list      = []
    skipped_list = []
    failed_list  = []   # list of (code, error_str)

    for problem in problems:
        code = problem['code']

        # --- locked ---
        if problem.get('locked'):
            log.info("[skip] %s — locked: true", code)
            skipped_list.append(code)
            continue

        # --- change detection (skip if --force not set) ---
        if not args.force:
            try:
                meta = fetch_latest_package_meta(
                    problem['polygon_id'], api_key, api_secret
                )
                time.sleep(1)
            except Exception as exc:
                log.error("[%s] Không lấy được package meta: %s", code, exc)
                failed_list.append((code, str(exc)))
                continue

            if is_up_to_date(state, problem['polygon_id'], meta['package_id']):
                log.info("[skip] %s — unchanged (package_id=%d)", code, meta['package_id'])
                skipped_list.append(code)
                continue
        else:
            meta = None   # will be fetched below via download (revision bundled)

        # --- full sync ---
        try:
            _sync_one(
                problem, api_key, api_secret,
                args.problems_dir, args.site_container,
                allow_zero_points=args.allow_zero_points,
                wineprefix=args.wine_prefix,
            )
            time.sleep(1)

            # Update state: if we already have meta (from change-detection step)
            # use it; otherwise re-fetch to record the package_id.
            if meta is None:
                try:
                    meta = fetch_latest_package_meta(
                        problem['polygon_id'], api_key, api_secret
                    )
                    time.sleep(1)
                except Exception:
                    pass   # non-fatal: state just won't be updated this run

            if meta:
                update_problem_state(state, problem['polygon_id'], meta['package_id'])
                save_state(str(_STATE_PATH), state)

            ok_list.append(code)

        except Exception as exc:
            log.error("[%s] FAILED: %s", code, exc, exc_info=True)
            failed_list.append((code, str(exc)))

    # --- summary report ---
    log.info("")
    log.info("==== Sync report ====")
    log.info("OK:      %d bài", len(ok_list))
    log.info("SKIPPED: %d bài (unchanged/locked)", len(skipped_list))
    log.info("FAILED:  %d bài", len(failed_list))
    for code, err in failed_list:
        log.info("  - %s: %s", code, err)

    return 1 if failed_list else 0


# ---------------------------------------------------------------------------
# Subcommand: status
# ---------------------------------------------------------------------------

def cmd_status(args) -> int:
    from polysync.config import load_config, ConfigError
    from polysync.state import load_state, get_problem_state

    try:
        cfg = load_config(str(_CONFIG_PATH))
    except Exception as exc:
        log.error("Config error: %s", exc)
        return 1

    state = load_state(str(_STATE_PATH))

    col_w = [10, 12, 22, 16]
    header = (
        f"{'CODE':<{col_w[0]}}  {'POLYGON_ID':<{col_w[1]}}  "
        f"{'LAST_SYNCED_AT':<{col_w[2]}}  {'STATUS':<{col_w[3]}}"
    )
    sep = '-' * (sum(col_w) + 6)
    print(header)
    print(sep)

    for p in cfg['problems']:
        code       = p['code']
        polygon_id = p['polygon_id']
        entry      = get_problem_state(state, polygon_id)

        if p.get('locked'):
            status = 'locked'
        elif entry is None:
            status = 'never-synced'
        else:
            # We don't know the "latest" package_id here without hitting the API;
            # just show last-synced info and let the user judge.
            status = 'synced'

        last_synced = (entry or {}).get('last_synced_at', '-')
        print(
            f"{code:<{col_w[0]}}  {polygon_id:<{col_w[1]}}  "
            f"{last_synced:<{col_w[2]}}  {status:<{col_w[3]}}"
        )

    return 0


# ---------------------------------------------------------------------------
# Subcommand: list-remote
# ---------------------------------------------------------------------------

def cmd_list_remote(args) -> int:
    from polysync.polygon_api import polygon_call

    api_key, api_secret = _get_credentials()
    try:
        problems = polygon_call('problems.list', {}, api_key, api_secret)
    except Exception as exc:
        log.error("Không lấy được danh sách bài từ Polygon: %s", exc)
        return 1

    print(f"{'ID':<10}  {'NAME'}")
    print('-' * 50)
    for p in problems:
        print(f"{p.get('id', '?'):<10}  {p.get('name', '?')}")

    return 0


# ---------------------------------------------------------------------------
# Argument parser + main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='polysync',
        description='Sync đề từ Polygon sang VNOJ (DMOJ fork).',
    )
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Hiển thị log DEBUG.')

    sub = parser.add_subparsers(dest='command', required=True)

    # --- sync ---
    p_sync = sub.add_parser('sync', help='Sync bài từ Polygon sang VNOJ.')
    p_sync.add_argument('--force', action='store_true',
                        help='Bỏ qua check state, ép sync lại toàn bộ.')
    p_sync.add_argument('--only', metavar='CODE',
                        help='Chỉ sync 1 bài theo code (bỏ qua check state).')
    p_sync.add_argument('--problems-dir', default=_DEFAULT_PROBLEMS_DIR,
                        help=f'Thư mục problems/ của vnoj-docker (mặc định: {_DEFAULT_PROBLEMS_DIR})')
    p_sync.add_argument('--site-container', default=_DEFAULT_SITE_CONTAINER,
                        help=f'Tên container site (mặc định: {_DEFAULT_SITE_CONTAINER})')
    p_sync.add_argument('--allow-zero-points', action='store_true',
                        help='Cho phép import bài không có points trên Polygon (tự chia đều).')
    _default_wineprefix = os.path.expanduser('~/tools/polysyncvnoj/.wineprefix')
    p_sync.add_argument(
        '--wine-prefix',
        default=_default_wineprefix,
        metavar='PATH',
        help=(
            f'Đường dẫn đến Wine prefix (WINEPREFIX) dùng khi chạy doall.sh '
            f'để regenerate test cho package loại standard. '
            f'Mặc định: {_default_wineprefix}'
        ),
    )

    # --- status ---
    sub.add_parser('status', help='Hiển thị trạng thái sync của từng bài.')

    # --- list-remote ---
    sub.add_parser('list-remote', help='Liệt kê bài có trên Polygon (tham khảo).')

    return parser


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()
    _setup_logging(verbose=getattr(args, 'verbose', False))

    dispatch = {
        'sync':        cmd_sync,
        'status':      cmd_status,
        'list-remote': cmd_list_remote,
    }
    exit_code = dispatch[args.command](args)
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
