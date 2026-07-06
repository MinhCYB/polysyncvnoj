"""
tests/test_convert.py — Unit tests for polysync.convert.

Verifies the 5 bug-fix invariants that MUST be preserved during refactoring:
  1. init.yml checker format is FLAT (checker: bridged + sibling checker_args).
  2. Raise RuntimeError when sum(points) == 0 and allow_zero_points=False.
  3. MAIN_SOLUTION_TAGS: accept 'MA', 'main', 'Main'.
  4. run_solution_for_answer raises on non-zero returncode.
  5. testset selection: prefer name="tests", fall back to first node.

All tests use the fixture problem.xml or minimal in-memory XML — no live API
calls or docker exec.
"""

import os
import textwrap
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make repo root importable regardless of how pytest is invoked.
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from polysync.convert import (
    MAIN_SOLUTION_TAGS,
    latex_to_markdown,
    parse_problem_xml,
    write_init_yml,
)

FIXTURE_DIR = Path(__file__).parent / 'fixtures' / 'sample_problem_xml'


# ---------------------------------------------------------------------------
# latex_to_markdown
# ---------------------------------------------------------------------------

class TestLatexToMarkdown(unittest.TestCase):
    def test_inline_math_dollar_to_tilde(self):
        """Polygon dùng $...$ — phải convert sang ~...~ cho Martor/VNOJ."""
        self.assertEqual(
            latex_to_markdown("Tìm $N$ và $M$."),
            "Tìm ~N~ và ~M~.",
        )

    def test_empty_input(self):
        self.assertEqual(latex_to_markdown(''), '')

    def test_textbf(self):
        self.assertEqual(latex_to_markdown(r'\textbf{in đậm}'), '**in đậm**')

    def test_textit(self):
        self.assertEqual(latex_to_markdown(r'\textit{nghiêng}'), '*nghiêng*')

    def test_no_dollar_unchanged(self):
        """Text không có công thức phải giữ nguyên."""
        self.assertEqual(
            latex_to_markdown("Không có công thức ở đây."),
            "Không có công thức ở đây.",
        )




# ---------------------------------------------------------------------------
# Helper: build minimal problem.xml string
# ---------------------------------------------------------------------------

def _make_xml(
    points=None,          # list of per-test point values for "tests" testset
    tag='MA',             # solution tag
    sol_type='cpp.g++17', # solution source type
    testset_name='tests', # name of the single testset
    multi_testset=False,  # if True, add a "pretests" testset before "tests"
    checker_type='cpp.g++17',
):
    """Return a temporary directory path containing a problem.xml."""
    import tempfile

    n = len(points) if points is not None else 2
    test_nodes = ''
    if points is not None:
        test_nodes = ''.join(f'<test points="{p}"/>' for p in points)

    extra_testset = ''
    if multi_testset:
        extra_testset = f'''
        <testset name="pretests">
          <time-limit>1000</time-limit>
          <memory-limit>131072</memory-limit>
          <test-count>1</test-count>
          <tests><test points="0"/></tests>
        </testset>'''

    xml_str = f'''<?xml version="1.0" encoding="utf-8"?>
<problem>
  <names><name language="english" value="Test Problem"/></names>
  <judging input-file="" output-file="">
    {extra_testset}
    <testset name="{testset_name}">
      <time-limit>2000</time-limit>
      <memory-limit>262144</memory-limit>
      <test-count>{n}</test-count>
      <tests>{test_nodes}</tests>
    </testset>
  </judging>
  <assets>
    <checker name="checker">
      <source path="check.cpp" type="{checker_type}"/>
    </checker>
    <solutions>
      <solution tag="{tag}">
        <source path="solutions/std.cpp" type="{sol_type}"/>
      </solution>
    </solutions>
  </assets>
</problem>'''

    tmpdir = tempfile.mkdtemp()
    with open(os.path.join(tmpdir, 'problem.xml'), 'w') as f:
        f.write(xml_str)
    return tmpdir


# ---------------------------------------------------------------------------
# Invariant 5: testset selection
# ---------------------------------------------------------------------------

class TestTestsetSelection(unittest.TestCase):
    def test_prefers_tests_testset_over_pretests(self):
        """Invariant 5: prefer testset name='tests' even when pretests comes first."""
        pkg_dir = _make_xml(points=[30, 30, 40], multi_testset=True)
        info = parse_problem_xml(pkg_dir)
        self.assertEqual(info['test_count'], 3)
        self.assertAlmostEqual(sum(info['points_list']), 100.0)

    def test_falls_back_to_first_testset_when_no_tests_named(self):
        """Invariant 5 fallback: if no testset is named 'tests', use first."""
        pkg_dir = _make_xml(points=[50, 50], testset_name='custom_testset')
        info = parse_problem_xml(pkg_dir)
        self.assertEqual(info['test_count'], 2)

    def test_fixture_xml(self):
        """Fixture has both pretests and tests; should parse tests (3 cases)."""
        info = parse_problem_xml(str(FIXTURE_DIR))
        self.assertEqual(info['test_count'], 3)
        self.assertAlmostEqual(sum(info['points_list']), 100.0)


# ---------------------------------------------------------------------------
# Invariant 2: points validation
# ---------------------------------------------------------------------------

class TestPointsValidation(unittest.TestCase):
    def test_raises_when_all_points_zero(self):
        """Invariant 2: raise RuntimeError when points sum is 0 and flag is off."""
        pkg_dir = _make_xml(points=[0, 0, 0])
        with self.assertRaises(RuntimeError) as ctx:
            parse_problem_xml(pkg_dir, allow_zero_points=False)
        self.assertIn('points', str(ctx.exception).lower())

    def test_allow_zero_points_flag_divides_evenly(self):
        """Invariant 2: with flag set, points are distributed, no raise."""
        pkg_dir = _make_xml(points=[0, 0, 0])
        info = parse_problem_xml(pkg_dir, allow_zero_points=True)
        self.assertEqual(info['test_count'], 3)
        self.assertGreater(sum(info['points_list']), 0)

    def test_normal_points_pass_through(self):
        pkg_dir = _make_xml(points=[30, 30, 40])
        info = parse_problem_xml(pkg_dir)
        self.assertAlmostEqual(info['points_list'], [30.0, 30.0, 40.0])


# ---------------------------------------------------------------------------
# Invariant 3: main solution tag recognition
# ---------------------------------------------------------------------------

class TestMainSolutionTag(unittest.TestCase):
    def _get_main_solution_tag(self, tag):
        pkg_dir = _make_xml(points=[50, 50], tag=tag)
        info = parse_problem_xml(pkg_dir)
        return info['main_solution']

    def test_MA_tag_recognized(self):
        """Invariant 3: 'MA' (Polygon internal code) must be accepted."""
        self.assertIsNotNone(self._get_main_solution_tag('MA'))

    def test_main_lowercase_tag_recognized(self):
        """Invariant 3: 'main' must be accepted."""
        self.assertIsNotNone(self._get_main_solution_tag('main'))

    def test_Main_capitalized_tag_recognized(self):
        """Invariant 3: 'Main' must be accepted."""
        self.assertIsNotNone(self._get_main_solution_tag('Main'))

    def test_non_main_tag_ignored(self):
        """Non-main tags (WA, TLE) should not be selected as main solution."""
        pkg_dir = _make_xml(points=[50, 50], tag='WA')
        info = parse_problem_xml(pkg_dir)
        self.assertIsNone(info['main_solution'])

    def test_MAIN_SOLUTION_TAGS_constant(self):
        self.assertIn('MA',   MAIN_SOLUTION_TAGS)
        self.assertIn('main', MAIN_SOLUTION_TAGS)
        self.assertIn('Main', MAIN_SOLUTION_TAGS)


# ---------------------------------------------------------------------------
# Invariant 4: model solution returncode check
# ---------------------------------------------------------------------------

class TestRunSolutionReturnCode(unittest.TestCase):
    def test_raises_on_nonzero_returncode_stdin(self):
        """Invariant 4: non-zero returncode → RuntimeError (stdin mode)."""
        from polysync.convert import run_solution_for_answer
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path  = os.path.join(tmpdir, 'test.in')
            answer_path = os.path.join(tmpdir, 'test.out')
            # 'false' always exits with code 1
            with open(input_path, 'w') as f:
                f.write('1\n')

            with self.assertRaises(RuntimeError) as ctx:
                run_solution_for_answer(
                    '/bin/false', input_path, answer_path,
                    file_io_names=None, test_label='01',
                )
            self.assertIn('mã lỗi', str(ctx.exception))

    def test_raises_on_nonzero_returncode_file_io(self):
        """Invariant 4: non-zero returncode → RuntimeError (file I/O mode)."""
        from polysync.convert import run_solution_for_answer
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path  = os.path.join(tmpdir, 'test.in')
            answer_path = os.path.join(tmpdir, 'test.out')
            with open(input_path, 'w') as f:
                f.write('1\n')

            with self.assertRaises(RuntimeError):
                run_solution_for_answer(
                    '/bin/false', input_path, answer_path,
                    file_io_names=('input.txt', 'output.txt'),
                    test_label='01',
                )


# ---------------------------------------------------------------------------
# Invariant 1: init.yml checker format
# ---------------------------------------------------------------------------

class TestWriteInitYml(unittest.TestCase):
    def _read_init_yml(self, checker_dst_name):
        import tempfile
        info = {
            'time_limit': 2.0,
            'memory_limit_kb': 256,
            'file_io': False,
            'input_file': '',
            'output_file': '',
            'checker_lang': 'CPP17',
        }
        test_cases = [
            {'in': '1.in', 'out': '1.out', 'points': 50},
            {'in': '2.in', 'out': '2.out', 'points': 50},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            write_init_yml(tmpdir, info, test_cases, checker_dst_name)
            with open(os.path.join(tmpdir, 'init.yml')) as f:
                return f.read()

    def test_checker_format_is_flat(self):
        """Invariant 1: checker must be written as flat keys, not nested."""
        content = self._read_init_yml('checker.cpp')
        # Must have top-level 'checker: bridged'
        self.assertIn('checker: bridged', content)
        # Must have top-level 'checker_args:'
        self.assertIn('checker_args:', content)
        # Must NOT have nested form like 'checker:\n  name:' or 'checker: {name:'
        self.assertNotIn('  name: bridged', content)
        # The checker line should NOT be followed by indented content on the same key
        import re
        nested_pattern = re.compile(r'^checker:\s*\{', re.MULTILINE)
        self.assertIsNone(nested_pattern.search(content))

    def test_no_checker_section_when_none(self):
        """When checker_dst_name is None, no checker keys should appear."""
        content = self._read_init_yml(None)
        self.assertNotIn('checker', content)
        self.assertNotIn('checker_args', content)

    def test_test_cases_present(self):
        content = self._read_init_yml('checker.cpp')
        self.assertIn('test_cases:', content)
        self.assertIn('1.in', content)
        self.assertIn('2.out', content)


if __name__ == '__main__':
    unittest.main()
