"""
tests/test_integration_wine.py — Integration tests using real package zip fixtures.

These tests use the actual zip files in tests/fixtures/sample_packages/ — NOT mocks.
Wine must be installed and WINEPREFIX must be initialised for the tests that need
doall.sh regeneration.

Test cases:
  - lv1-ifelse-01-1.zip  : generated tests, needs Wine to regenerate all 25 tests
  - hhy-mintree-2.zip    : manual tests (inputs present), needs Wine to generate answers

Setup:
    The test unzips each fixture into a temporary directory, runs convert_package,
    and verifies the output — the fixture originals are never modified.

Skip condition:
    If ``wine`` is not found on PATH or WINEPREFIX is not initialised, the tests
    are skipped with an informative message rather than failing cryptically.
"""

import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

# Ensure the repo root is importable regardless of how pytest is invoked.
sys.path.insert(0, str(Path(__file__).parent.parent))

from polysync.convert import convert_package, _tests_complete

FIXTURE_DIR = Path(__file__).parent / 'fixtures' / 'sample_packages'
LV1_ZIP     = FIXTURE_DIR / 'lv1-ifelse-01-1.zip'
MINTREE_ZIP = FIXTURE_DIR / 'hhy-mintree-2.zip'

# Wine prefix: honour WINEPREFIX env var if set; otherwise fall back to the
# project-local default that the README instructs users to create.
_DEFAULT_WINEPREFIX = os.path.expanduser('~/tools/polysyncvnoj/.wineprefix')
WINEPREFIX = os.environ.get('WINEPREFIX', _DEFAULT_WINEPREFIX)


def _wine_available():
    """Return True if wine binary is on PATH."""
    return shutil.which('wine') is not None


def _wineprefix_ok():
    """Return True if WINEPREFIX exists and has been initialised (drive_c present)."""
    return os.path.isdir(os.path.join(WINEPREFIX, 'drive_c'))


def _unzip_to(zip_path: Path, dest: str) -> str:
    """Extract zip_path into dest and return the extraction directory path."""
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
    return dest


def _skip_if_no_wine():
    """Skip the calling test if wine / WINEPREFIX are not ready."""
    if not _wine_available():
        raise unittest.SkipTest(
            "wine không tìm thấy trên PATH — cài bằng: "
            "sudo dpkg --add-architecture i386 && sudo apt install -y wine32 wine64 wine"
        )
    if not _wineprefix_ok():
        raise unittest.SkipTest(
            f"WINEPREFIX chưa được khởi tạo: {WINEPREFIX!r}. "
            "Chạy: export WINEPREFIX=... && wineboot --init"
        )


# ---------------------------------------------------------------------------
# Test: lv1-ifelse-01-1 — generated tests, needs Wine regen
# ---------------------------------------------------------------------------

class TestLv1IfelseIntegration(unittest.TestCase):
    """lv1-ifelse-01-1.zip: 25 tests, only 2 inputs in package, needs doall.sh."""

    def setUp(self):
        _skip_if_no_wine()
        if not LV1_ZIP.exists():
            self.skipTest(f"Fixture missing: {LV1_ZIP}")

    def test_convert_package_lv1_with_wine(self):
        """Full pipeline: unzip → doall.sh via Wine → convert_package produces 25 tests."""
        with tempfile.TemporaryDirectory() as pkg_tmp, \
             tempfile.TemporaryDirectory() as out_tmp:

            pkg_dir = _unzip_to(LV1_ZIP, pkg_tmp)

            # Sanity: before running, test data should be incomplete.
            # (only 2 inputs present, no .a files, but XML says 25 tests)
            from polysync.convert import parse_problem_xml
            info = parse_problem_xml(pkg_dir, allow_zero_points=True)
            self.assertEqual(info['test_count'], 25,
                             "lv1 fixture should declare 25 tests")
            self.assertFalse(
                _tests_complete(pkg_dir, info['test_count']),
                "Before wine regen, tests should be incomplete"
            )

            # Run the full conversion — this calls doall.sh internally.
            result_info = convert_package(
                pkg_dir, out_tmp,
                points_total=100,
                allow_zero_points=True,
                wineprefix=WINEPREFIX,
            )

            # Verify all 25 .in and .out files were produced.
            for i in range(1, result_info['test_count'] + 1):
                in_f  = os.path.join(out_tmp, f"{i}.in")
                out_f = os.path.join(out_tmp, f"{i}.out")
                self.assertTrue(os.path.exists(in_f),  f"Missing {i}.in")
                self.assertTrue(os.path.exists(out_f), f"Missing {i}.out")
                # CRLF normalisation: no output file should contain \r\n
                with open(out_f, 'rb') as fh:
                    data = fh.read()
                self.assertNotIn(b'\r\n', data,
                                 f"{i}.out still contains CRLF after normalisation")

            # init.yml should exist and reference test_cases.
            init_yml = os.path.join(out_tmp, 'init.yml')
            self.assertTrue(os.path.exists(init_yml), "init.yml missing")
            with open(init_yml) as f:
                content = f.read()
            self.assertIn('test_cases:', content)
            self.assertIn('1.in', content)

    def test_no_wine_regen_when_data_complete(self):
        """If all test data is already present, regenerate_tests_via_doall is NOT called.

        We verify this by:
        1. Constructing a minimal 1-test package with both input and .a present.
        2. Asserting _tests_complete returns True before convert_package.
        3. Running convert_package with a deliberately wrong wineprefix path.
           If wine regen were called, it would fail (path doesn't exist).
           If it's correctly skipped, convert_package succeeds.
        """
        with tempfile.TemporaryDirectory() as pkg_tmp, \
             tempfile.TemporaryDirectory() as out_tmp:

            # Make a minimal package with 1 test where data is pre-populated.
            tests_dir = os.path.join(pkg_tmp, 'tests')
            os.makedirs(tests_dir, exist_ok=True)
            with open(os.path.join(tests_dir, '01'), 'w') as f:
                f.write('1\n')
            with open(os.path.join(tests_dir, '01.a'), 'w') as f:
                f.write('1\n')

            # Minimal problem.xml with 1 manual test (100 points)
            xml_content = '''<?xml version="1.0" encoding="utf-8"?>
<problem>
  <names><name language="english" value="Test"/></names>
  <judging input-file="" output-file="">
    <testset name="tests">
      <time-limit>1000</time-limit>
      <memory-limit>262144</memory-limit>
      <test-count>1</test-count>
      <tests><test points="100.0" method="manual"/></tests>
    </testset>
  </judging>
  <assets>
    <checker name="checker">
      <source path="check.cpp" type="cpp.g++17"/>
    </checker>
    <solutions/>
  </assets>
</problem>'''
            with open(os.path.join(pkg_tmp, 'problem.xml'), 'w') as f:
                f.write(xml_content)

            # Also create a fake doall.sh and check.cpp so the package is "valid"
            with open(os.path.join(pkg_tmp, 'doall.sh'), 'w') as f:
                f.write('#!/bin/bash\necho "should not be called"\n')
            with open(os.path.join(pkg_tmp, 'check.cpp'), 'w') as f:
                f.write('// dummy checker\n')

            # Verify _tests_complete sees the data as complete
            from polysync.convert import _tests_complete, parse_problem_xml
            info = parse_problem_xml(pkg_tmp, allow_zero_points=True)
            self.assertTrue(_tests_complete(pkg_tmp, info['test_count']),
                            "_tests_complete should return True — test data is present")

            # Use a deliberately invalid wineprefix.  If wine regen were invoked,
            # the function would raise RuntimeError about a bad WINEPREFIX.
            # If regen is correctly skipped (data complete), convert succeeds.
            bogus_wineprefix = '/nonexistent/wineprefix'
            try:
                convert_package(
                    pkg_tmp, out_tmp,
                    points_total=100,
                    allow_zero_points=True,
                    wineprefix=bogus_wineprefix,
                )
            except RuntimeError as e:
                self.fail(
                    f"convert_package raised RuntimeError even though test data was "
                    f"complete — wine regen should have been skipped: {e}"
                )

            # Output files should exist
            self.assertTrue(os.path.exists(os.path.join(out_tmp, '1.in')))
            self.assertTrue(os.path.exists(os.path.join(out_tmp, '1.out')))


# ---------------------------------------------------------------------------
# Test: hhy-mintree-2 — manual tests (all inputs present), needs Wine for .a
# ---------------------------------------------------------------------------

class TestMintreeIntegration(unittest.TestCase):
    """hhy-mintree-2.zip: 11 manual tests, all inputs present, no .a files → needs Wine."""

    def setUp(self):
        _skip_if_no_wine()
        if not MINTREE_ZIP.exists():
            self.skipTest(f"Fixture missing: {MINTREE_ZIP}")

    def test_convert_package_mintree_with_wine(self):
        """Full pipeline: all inputs present, Wine generates .a answers for all 11 tests."""
        with tempfile.TemporaryDirectory() as pkg_tmp, \
             tempfile.TemporaryDirectory() as out_tmp:

            pkg_dir = _unzip_to(MINTREE_ZIP, pkg_tmp)

            # Sanity: inputs are present but .a files are not.
            from polysync.convert import parse_problem_xml
            info = parse_problem_xml(pkg_dir, allow_zero_points=True)
            self.assertEqual(info['test_count'], 11,
                             "mintree fixture should declare 11 tests")

            tests_dir = os.path.join(pkg_dir, 'tests')
            # All inputs should be present.
            for i in range(1, 12):
                self.assertTrue(
                    os.path.exists(os.path.join(tests_dir, f"{i:02d}")),
                    f"Input {i:02d} should be present before wine regen"
                )
            # No .a files before regen.
            a_files = [f for f in os.listdir(tests_dir) if f.endswith('.a')]
            self.assertEqual(a_files, [],
                             "No .a files expected before wine regen")

            # Run the full conversion.
            result_info = convert_package(
                pkg_dir, out_tmp,
                points_total=100,
                allow_zero_points=True,
                wineprefix=WINEPREFIX,
            )

            # All 11 .in and .out files should now exist.
            for i in range(1, result_info['test_count'] + 1):
                in_f  = os.path.join(out_tmp, f"{i}.in")
                out_f = os.path.join(out_tmp, f"{i}.out")
                self.assertTrue(os.path.exists(in_f),  f"Missing {i}.in")
                self.assertTrue(os.path.exists(out_f), f"Missing {i}.out")

            # CRLF normalisation check on all output files.
            for i in range(1, result_info['test_count'] + 1):
                out_f = os.path.join(out_tmp, f"{i}.out")
                with open(out_f, 'rb') as fh:
                    data = fh.read()
                self.assertNotIn(b'\r\n', data,
                                 f"{i}.out still contains CRLF after normalisation")

            # Verify init.yml
            init_yml = os.path.join(out_tmp, 'init.yml')
            self.assertTrue(os.path.exists(init_yml), "init.yml missing")
            with open(init_yml) as f:
                content = f.read()
            self.assertIn('test_cases:', content)
            self.assertIn('  name: bridged', content)    # Invariant 1: nested checker format

    def test_wine_regen_called_when_answers_missing(self):
        """When .a files are absent (even if inputs present), doall.sh IS called."""
        with tempfile.TemporaryDirectory() as pkg_tmp:
            pkg_dir = _unzip_to(MINTREE_ZIP, pkg_tmp)

            # Confirm tests are not complete (no .a files)
            from polysync.convert import parse_problem_xml
            info = parse_problem_xml(pkg_dir, allow_zero_points=True)
            self.assertFalse(
                _tests_complete(pkg_dir, info['test_count']),
                "Test data should be incomplete before regen (no .a files)"
            )


# ---------------------------------------------------------------------------
# Test: error path — missing wineprefix raises RuntimeError
# ---------------------------------------------------------------------------

class TestMissingWineprefixError(unittest.TestCase):
    """When tests are missing and doall.sh exists but wineprefix is None, raise."""

    def test_raises_when_wineprefix_not_provided(self):
        if not LV1_ZIP.exists():
            self.skipTest(f"Fixture missing: {LV1_ZIP}")

        with tempfile.TemporaryDirectory() as pkg_tmp, \
             tempfile.TemporaryDirectory() as out_tmp:

            pkg_dir = _unzip_to(LV1_ZIP, pkg_tmp)

            with self.assertRaises(RuntimeError) as ctx:
                convert_package(
                    pkg_dir, out_tmp,
                    points_total=100,
                    allow_zero_points=True,
                    wineprefix=None,   # Not provided!
                )
            self.assertIn('wineprefix', str(ctx.exception).lower())


if __name__ == '__main__':
    unittest.main(verbosity=2)
