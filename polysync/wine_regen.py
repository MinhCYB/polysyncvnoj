"""
polysync/wine_regen.py — Regenerate test data for Polygon 'standard' packages.

Polygon standard packages contain doall.sh which invokes generators, the model
solution and the checker (all bundled as .exe files) through Wine to produce
the full test suite.  This module wraps that process.

Exports:
    regenerate_tests_via_doall   Run doall.sh via bash + Wine, return (rc, log)
"""

import os
import subprocess


def regenerate_tests_via_doall(pkg_dir, wineprefix, timeout=300):
    """Chạy doall.sh trong pkg_dir qua wine để regenerate test thiếu.

    QUAN TRỌNG: stdin PHẢI là subprocess.DEVNULL — doall.sh có nhiều lệnh
    `read` để dừng chờ người dùng khi có lỗi (thiết kế cho chạy tay).
    Không redirect stdin sẽ khiến process TREO VÔ THỜI HẠN khi chạy
    trong cron không ai canh.

    Trả về (returncode, log_text) — không tự raise ở đây, caller verify
    lại file sau khi chạy xong.

    Args:
        pkg_dir:    Đường dẫn tuyệt đối đến thư mục package đã giải nén.
        wineprefix: Đường dẫn đến WINEPREFIX (thư mục chứa Wine config).
        timeout:    Thời gian tối đa (giây) chờ doall.sh hoàn thành.
                    Mặc định 300s (5 phút) — đủ cho bài ~100 test nặng.

    Returns:
        (returncode: int, log_text: str)  — log_text là stdout + stderr gộp.

    Raises:
        RuntimeError: nếu pkg_dir không chứa doall.sh (không phải standard
                      package hợp lệ).
        subprocess.TimeoutExpired: nếu doall.sh chạy quá timeout giây.
    """
    doall_path = os.path.join(pkg_dir, 'doall.sh')
    if not os.path.exists(doall_path):
        raise RuntimeError(
            f"{pkg_dir} không có doall.sh — không phải standard package "
            "hợp lệ để tự regenerate."
        )

    # Zip extraction strips Unix execute bits — mark all .sh scripts executable
    # so that doall.sh can invoke scripts/gen-answer.sh, scripts/gen-input-*.sh, etc.
    for root, _dirs, files in os.walk(pkg_dir):
        for fname in files:
            if fname.endswith('.sh'):
                fpath = os.path.join(root, fname)
                current = os.stat(fpath).st_mode
                os.chmod(fpath, current | 0o111)

    env = os.environ.copy()
    env['WINEPREFIX'] = wineprefix
    # Tắt Wine debug output để log sạch hơn; lỗi thật vẫn ra stderr của wine.
    env['WINEDEBUG'] = '-all'
    # Tắt Mono/Gecko auto-install (không cần cho .exe C++/Pascal).
    env['WINEDLLOVERRIDES'] = 'mscoree,mshtml='

    proc = subprocess.run(
        ['bash', 'doall.sh'],
        cwd=pkg_dir,
        stdin=subprocess.DEVNULL,   # CRITICAL: tránh treo vô hạn trên lệnh `read`
        capture_output=True,
        timeout=timeout,
        env=env,
    )
    log_text = (proc.stdout + proc.stderr).decode(errors='replace')
    return proc.returncode, log_text
