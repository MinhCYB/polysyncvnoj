"""
polysync/convert.py — Polygon package → VNOJ data conversion.

Handles:
  - LaTeX → Markdown best-effort conversion  (latex_to_markdown, build_description_markdown)
  - problem.xml parsing                       (parse_problem_xml)
  - Model solution compile + run              (compile_cpp, run_solution_for_answer)
  - Full package conversion                   (convert_package)
  - init.yml writer                           (write_init_yml)

IMPORTANT — preserved bug-fix invariants (do NOT change these):
  1. init.yml checker is written FLAT (checker: bridged / checker_args: …),
     NOT nested.  DMOJ/VNOJ will not parse the nested form.
  2. sum(points_list) == 0  →  raise RuntimeError unless allow_zero_points.
  3. Main solution tag: accept {'MA', 'main', 'Main'} (Polygon uses 'MA'
     internally; 'main'/'Main' appears in some export versions).
  4. Model solution run: check returncode; raise on non-zero instead of
     silently writing empty/wrong output.
  5. testset selection: prefer name="tests"; fall back to first node.
"""

import logging
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _copy_normalised(src, dst):
    """Copy a test file src → dst, normalising CRLF → LF.

    Polygon packages built for Windows (or 'standard') may contain CRLF line
    endings in .in/.a files.  When a Linux checker or standard-diff compares
    contestant output (LF) against such a file the trailing '\\r' looks like
    extra content and causes false Wrong-Answer verdicts.  Stripping CRLFs here
    ensures byte-for-byte consistency regardless of which package type was
    downloaded.
    """
    with open(src, 'rb') as f:
        data = f.read()
    data = data.replace(b'\r\n', b'\n')
    with open(dst, 'wb') as f:
        f.write(data)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LANG_EXT_MAP = {
    'cpp.g++17': 'CPP17',
    'cpp.g++14': 'CPP14',
    'cpp.g++11': 'CPP11',
    'cpp.ms':    'CPP17',
    'cpp.g++':   'CPP17',
    'cpp.g++0x': 'CPP11',
}

# Polygon type values that we can safely pass to g++ as a model solution.
COMPILABLE_CPP_TYPES = set(LANG_EXT_MAP.keys())

# Polygon tags a solution's role with short codes.  "MA" (Main) is the one we
# want; the human-readable "main" string sometimes also appears depending on
# export version, so we accept all three.
MAIN_SOLUTION_TAGS = {'MA', 'main', 'Main'}

DEFAULT_LANGUAGES = [
    'C', 'C11', 'CPP03', 'CPP11', 'CPP14', 'CPP17', 'CPP20',
    'JAVA', 'JAVA8', 'KOTLIN', 'PAS', 'PY2', 'PY3', 'PYPY', 'PYPY3',
]


# ---------------------------------------------------------------------------
# LaTeX → Markdown, best-effort
# ---------------------------------------------------------------------------

def latex_to_markdown(text):
    if not text:
        return ''
    text = text.replace('\\r\\n', '\n').replace('\r\n', '\n')

    # itemize / enumerate → markdown bullet/numbered lists
    def convert_itemize(m):
        body = m.group(1)
        items = re.split(r'\\item\s*', body)
        items = [i.strip() for i in items if i.strip()]
        return '\n'.join(f'- {i}' for i in items) + '\n'

    def convert_enumerate(m):
        body = m.group(1)
        items = re.split(r'\\item\s*', body)
        items = [i.strip() for i in items if i.strip()]
        return '\n'.join(f'{idx + 1}. {i}' for idx, i in enumerate(items)) + '\n'

    text = re.sub(r'\\begin\{itemize\}(.*?)\\end\{itemize\}',
                  convert_itemize, text, flags=re.S)
    text = re.sub(r'\\begin\{enumerate\}(.*?)\\end\{enumerate\}',
                  convert_enumerate, text, flags=re.S)

    # simple text styling
    text = re.sub(r'\\textbf\{([^}]*)\}', r'**\1**', text)
    text = re.sub(r'\\textit\{([^}]*)\}', r'*\1*', text)
    text = re.sub(r'\\emph\{([^}]*)\}',   r'*\1*', text)

    # images: leave a visible note; image files need manual upload
    text = re.sub(
        r'\\includegraphics(\[[^\]]*\])?\{([^}]*)\}',
        r'\n> ⚠️ [Hình ảnh gốc: \2 — cần tự upload lại qua Martor image tool]\n',
        text,
    )

    # \\ line breaks → markdown line break
    text = text.replace('\\\\', '  \n')

    # \begin{center}/\end{center} — strip wrapper, keep content
    text = re.sub(r'\\begin\{center\}(.*?)\\end\{center\}', r'\1', text, flags=re.S)

    # LaTeX-escaped punctuation → plain characters
    for esc, plain in [
        (r'\"', '"'), (r'\%', '%'), (r'\&', '&'),
        (r'\_', '_'), (r'\#', '#'), (r'\{', '{'), (r'\}', '}'),
    ]:
        text = text.replace(esc, plain)

    return text.strip()


def build_description_markdown(statement):
    if not statement:
        return '*(Không lấy được statement từ Polygon, vui lòng nhập thủ công.)*'
    parts = []
    if statement.get('legend'):
        parts.append(latex_to_markdown(statement['legend']))
    if statement.get('input'):
        parts.append('## Input\n\n' + latex_to_markdown(statement['input']))
    if statement.get('output'):
        parts.append('## Output\n\n' + latex_to_markdown(statement['output']))
    if statement.get('notes'):
        parts.append('## Notes\n\n' + latex_to_markdown(statement['notes']))
    return '\n\n'.join(parts)


# ---------------------------------------------------------------------------
# problem.xml parsing
# ---------------------------------------------------------------------------

def parse_problem_xml(pkg_dir, allow_zero_points=False):
    """Parse problem.xml from a Polygon package directory.

    Returns a dict with keys:
        name, file_io, input_file, output_file,
        time_limit, memory_limit_kb, test_count, points_list,
        checker_source, checker_lang,
        main_solution, main_solution_type

    Invariants enforced here (see module docstring for context):
      - Invariant 2: raise if points sum is zero (unless allow_zero_points).
      - Invariant 3: MAIN_SOLUTION_TAGS recognition.
      - Invariant 5: testset selection (prefer "tests", fallback first).
    """
    tree = ET.parse(os.path.join(pkg_dir, 'problem.xml'))
    root = tree.getroot()
    info = {}

    # Problem name (prefer English)
    names = root.find('names')
    name = None
    for n in names.findall('name'):
        if n.get('language') == 'english':
            name = n.get('value')
            break
    if name is None and len(names):
        name = names[0].get('value')
    info['name'] = name or 'Untitled'

    # I/O mode
    judging = root.find('judging')
    input_file  = judging.get('input-file')  or ''
    output_file = judging.get('output-file') or ''
    info['file_io']     = bool(input_file) and input_file != 'stdin'
    info['input_file']  = input_file
    info['output_file'] = output_file

    # Invariant 5: prefer testset named "tests"; fall back to first node.
    testsets = judging.findall('testset')
    testset = (
        next((t for t in testsets if t.get('name') == 'tests'), None)
        or testsets[0]
    )

    info['time_limit']      = round(int(testset.find('time-limit').text) / 1000.0, 3)
    info['memory_limit_kb'] = int(testset.find('memory-limit').text) // 1024
    info['test_count']      = int(testset.find('test-count').text)

    tests_node = testset.find('tests')
    points_list = []
    if tests_node is not None:
        for t in tests_node.findall('test'):
            points_list.append(float(t.get('points', 0)))
    if len(points_list) != info['test_count']:
        points_list = [0.0] * info['test_count']

    # Invariant 2: fail loudly if all points are zero, unless explicitly opted-in.
    if sum(points_list) == 0:
        if not allow_zero_points:
            raise RuntimeError(
                f"Problem '{info['name']}' (Polygon) không có test nào được set "
                "points. Import bằng script này giả định các bài đã set điểm sẵn "
                "trên Polygon — nếu đây là bài sample/demo, hãy set points cho "
                "từng test trên Polygon trước, hoặc chạy lại với "
                "--allow-zero-points để tự chia đều điểm (không khuyến khích cho "
                "bài chính thức)."
            )
        log.warning(
            "[convert] ⚠️  '%s' không có points trên Polygon — "
            "tự chia đều điểm theo --allow-zero-points.", info['name']
        )
        base = 100 // info['test_count']
        points_list = [base] * info['test_count']
        points_list[-1] += 100 - base * info['test_count']

    info['points_list'] = points_list

    # Checker
    checker_node = root.find('assets/checker')
    checker_source, checker_lang = None, 'CPP17'
    if checker_node is not None:
        src = checker_node.find('source')
        if src is not None:
            checker_source = src.get('path')
            checker_lang   = LANG_EXT_MAP.get(src.get('type'), 'CPP17')
    info['checker_source'] = checker_source
    info['checker_lang']   = checker_lang

    # Invariant 3: accept MA / main / Main as "main solution" tags.
    main_solution      = None
    main_solution_type = None
    for sol in root.findall('assets/solutions/solution'):
        if sol.get('tag') in MAIN_SOLUTION_TAGS:
            src = sol.find('source')
            if src is not None:
                main_solution      = src.get('path')
                main_solution_type = src.get('type')
            break
    info['main_solution']      = main_solution
    info['main_solution_type'] = main_solution_type

    return info


# ---------------------------------------------------------------------------
# Model solution compile + run
# ---------------------------------------------------------------------------

def compile_cpp(src_path, out_path, solution_type=None):
    """Compile a C++ source file.

    Invariant 3 (partial): if solution_type indicates a non-C++ language,
    raise a clear error instead of letting g++ fail cryptically.
    """
    if solution_type is not None and solution_type not in COMPILABLE_CPP_TYPES:
        raise RuntimeError(
            f"Model solution '{src_path}' có type Polygon là '{solution_type}', "
            "không phải C++. Script này chỉ tự compile được model solution viết "
            "bằng C++ để sinh đáp án. Hãy tự cung cấp file .a cho các test còn "
            "thiếu, hoặc đổi model solution 'main' trên Polygon sang C++."
        )
    result = subprocess.run(
        ['g++', '-O2', '-std=c++17', '-o', out_path, src_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Compile failed for {src_path}:\n{result.stderr}")


def run_solution_for_answer(binary_path, input_path, answer_path,
                            file_io_names, test_label=''):
    """Run the compiled model solution to produce an answer file.

    Invariant 4: check returncode; raise RuntimeError instead of silently
    writing empty/wrong output.
    """
    if file_io_names:
        input_file, output_file = file_io_names
        with tempfile.TemporaryDirectory() as tmpdir:
            shutil.copy(input_path, os.path.join(tmpdir, input_file))
            proc = subprocess.run(
                [os.path.abspath(binary_path)],
                cwd=tmpdir, capture_output=True, timeout=30,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"Model solution thoát với mã lỗi {proc.returncode} khi sinh "
                    f"đáp án cho test {test_label}. "
                    f"stderr:\n{proc.stderr.decode(errors='replace')}"
                )
            out_path = os.path.join(tmpdir, output_file)
            if not os.path.exists(out_path):
                raise RuntimeError(
                    f"Model solution không tạo ra file output '{output_file}' cho "
                    f"test {test_label} (file I/O mode)."
                )
            shutil.copy(out_path, answer_path)
    else:
        with open(input_path, 'rb') as fin:
            proc = subprocess.run(
                [os.path.abspath(binary_path)],
                stdin=fin, capture_output=True, timeout=30,
            )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Model solution thoát với mã lỗi {proc.returncode} khi sinh đáp "
                f"án cho test {test_label}. "
                f"stderr:\n{proc.stderr.decode(errors='replace')}"
            )
        with open(answer_path, 'wb') as fout:
            fout.write(proc.stdout)


# ---------------------------------------------------------------------------
# Full package conversion
# ---------------------------------------------------------------------------

def _tests_complete(pkg_dir, test_count):
    """Return True if all test inputs AND answer files exist in pkg_dir/tests/."""
    tests_dir = os.path.join(pkg_dir, 'tests')
    for i in range(1, test_count + 1):
        idx = f"{i:02d}"
        if not os.path.exists(os.path.join(tests_dir, idx)):
            return False
        if not os.path.exists(os.path.join(tests_dir, f"{idx}.a")):
            return False
    return True


def convert_package(pkg_dir, problem_dir, points_total=100,
                    partial=False, allow_zero_points=False,
                    wineprefix=None):
    """Convert a Polygon package directory into a VNOJ problem directory.

    Copies test inputs/answers, checker, testlib.h and writes init.yml.
    Returns the parsed info dict from parse_problem_xml.

    For Polygon 'standard' packages (no pre-generated test data), pass
    ``wineprefix`` pointing to an initialised Wine prefix.  If any test
    input or answer file is missing AND doall.sh is present in pkg_dir,
    this function will run ``bash doall.sh`` via Wine to regenerate the
    full test suite before proceeding.

    Args:
        pkg_dir:          Path to the extracted Polygon package directory.
        problem_dir:      Destination VNOJ problem directory (created if absent).
        points_total:     Total point budget to scale Polygon points into.
        partial:          Unused flag (passed through to init.yml metadata).
        allow_zero_points: If True, distribute points evenly when Polygon has none.
        wineprefix:       Path to WINEPREFIX for Wine-based test regeneration.
                          Required when the package is 'standard' type.
    """
    info = parse_problem_xml(pkg_dir, allow_zero_points=allow_zero_points)
    os.makedirs(problem_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Step 1: Wine-based test regeneration for standard packages
    #
    # A 'standard' package contains doall.sh but no pre-generated test
    # data (inputs for 'generated' tests and .a answer files for all tests
    # are absent).  We detect this by checking whether the full test suite
    # exists before doing anything else.  If tests are missing:
    #   - If doall.sh is present → run it via Wine to regenerate.
    #   - If doall.sh is absent  → raise an informative error.
    # We re-check after regeneration and raise with the last 2000 chars of
    # the doall log if files are still missing.
    # ------------------------------------------------------------------
    doall_sh = os.path.join(pkg_dir, 'doall.sh')
    if not _tests_complete(pkg_dir, info['test_count']):
        if os.path.exists(doall_sh):
            if not wineprefix:
                raise RuntimeError(
                    f"Package thiếu test data và có doall.sh, nhưng wineprefix "
                    "chưa được cung cấp. Truyền --wine-prefix (CLI) hoặc tham số "
                    "wineprefix= (API) trỏ đến một Wine prefix đã khởi tạo."
                )
            from polysync.wine_regen import regenerate_tests_via_doall
            log.info(
                "[convert] Package thiếu test data — chạy doall.sh qua Wine "
                "(wineprefix=%s)...", wineprefix,
            )
            returncode, doall_log = regenerate_tests_via_doall(
                pkg_dir, wineprefix=wineprefix
            )
            log.info(
                "[convert] doall.sh hoàn thành (returncode=%d). "
                "Log (2000 ký tự cuối):\n%s",
                returncode, doall_log[-2000:],
            )
            # Re-check: even if returncode != 0 some generators succeed
            # partially; we verify by file presence rather than trusting rc.
            if not _tests_complete(pkg_dir, info['test_count']):
                raise RuntimeError(
                    f"doall.sh chạy xong (rc={returncode}) nhưng vẫn thiếu test "
                    f"data trong {pkg_dir}/tests/ . "
                    f"2000 ký tự cuối của log doall:\n{doall_log[-2000:]}"
                )
        else:
            # No doall.sh — not a standard package; we can't recover.
            missing_in = [
                f"{i:02d}" for i in range(1, info['test_count'] + 1)
                if not os.path.exists(os.path.join(pkg_dir, 'tests', f"{i:02d}"))
            ]
            missing_ans = [
                f"{i:02d}.a" for i in range(1, info['test_count'] + 1)
                if not os.path.exists(
                    os.path.join(pkg_dir, 'tests', f"{i:02d}.a")
                )
            ]
            raise RuntimeError(
                f"Package thiếu test data và không có doall.sh để regenerate. "
                f"Test inputs thiếu: {missing_in or 'none'}. "
                f"Answer files thiếu: {missing_ans or 'none'}."
            )
    else:
        log.info("[convert] Tất cả test data đã đủ, bỏ qua bước wine regen.")

    # ------------------------------------------------------------------
    # Step 2: Compile model solution (fallback for still-missing .a files)
    #
    # This is ONLY needed when some .a files are absent even after the wine
    # regen step (e.g. linux/windows packages where a few generated tests
    # were not pre-built).  When all .a files already exist (common after a
    # successful doall.sh run) we skip compilation entirely — this avoids
    # spurious errors on Windows-specific compiler types like
    # 'cpp.gcc14-64-msys2-g++23' that cannot be compiled with native g++
    # but are not needed since wine already produced all answers.
    # ------------------------------------------------------------------
    tests_dir = os.path.join(pkg_dir, 'tests')
    missing_answers = [
        i for i in range(1, info['test_count'] + 1)
        if not os.path.exists(os.path.join(tests_dir, f"{i:02d}.a"))
    ]

    solution_binary = None
    if missing_answers and info['main_solution']:
        sol_src = os.path.join(pkg_dir, info['main_solution'])
        if os.path.exists(sol_src):
            solution_binary = os.path.join(problem_dir, '.model_solution_bin')
            log.info(
                "[convert] %d test(s) still missing .a — compiling model solution: %s (type=%s)",
                len(missing_answers), info['main_solution'], info['main_solution_type'],
            )
            compile_cpp(sol_src, solution_binary,
                        solution_type=info['main_solution_type'])

    file_io_names = (
        (info['input_file'], info['output_file']) if info['file_io'] else None
    )

    # Scale each test's raw Polygon points into the target VNOJ point pool.
    raw_total = sum(info['points_list'])   # guaranteed > 0 at this point
    scale = points_total / raw_total

    # ------------------------------------------------------------------
    # Step 3: Copy tests, normalising CRLF → LF
    # ------------------------------------------------------------------
    test_cases = []
    for i in range(1, info['test_count'] + 1):
        idx    = f"{i:02d}"
        in_src = os.path.join(pkg_dir, 'tests', idx)
        in_dst = os.path.join(problem_dir, f"{i}.in")
        out_dst = os.path.join(problem_dir, f"{i}.out")
        # Normalise CRLF → LF: standard/Windows packages may have \r\n
        _copy_normalised(in_src, in_dst)

        ans_src = os.path.join(pkg_dir, 'tests', f"{idx}.a")
        if os.path.exists(ans_src):
            _copy_normalised(ans_src, out_dst)
        else:
            if not solution_binary:
                raise RuntimeError(
                    f"Missing answer for test {idx} and no model solution available."
                )
            log.info("[convert] Generating answer for test %s via model solution...", idx)
            run_solution_for_answer(
                solution_binary, in_dst, out_dst, file_io_names, test_label=idx
            )

        pts = round(info['points_list'][i - 1] * scale, 2)
        pts = int(pts) if pts == int(pts) else pts
        test_cases.append({'points': pts, 'in': f"{i}.in", 'out': f"{i}.out"})

    if solution_binary and os.path.exists(solution_binary):
        os.remove(solution_binary)

    # Copy checker + testlib
    checker_dst_name = None
    if info['checker_source']:
        shutil.copy(
            os.path.join(pkg_dir, info['checker_source']),
            os.path.join(problem_dir, 'checker.cpp'),
        )
        checker_dst_name = 'checker.cpp'
        testlib_src = os.path.join(pkg_dir, 'files', 'testlib.h')
        if os.path.exists(testlib_src):
            shutil.copy(testlib_src, os.path.join(problem_dir, 'testlib.h'))

    write_init_yml(problem_dir, info, test_cases, checker_dst_name)
    return info


def write_init_yml(problem_dir, info, test_cases, checker_dst_name):
    """Write init.yml for a VNOJ problem directory.

    Invariant 1: checker is written FLAT:
        checker: bridged
        checker_args:
          files: checker.cpp
          lang: CPP17
          type: testlib
    NOT nested under a single 'checker' key.
    """
    lines = [
        f"time_limit: {info['time_limit']}",
        f"memory_limit: {info['memory_limit_kb']}",
    ]
    if info['file_io']:
        lines += [
            'file_io:',
            f"  input: {info['input_file']}",
            f"  output: {info['output_file']}",
        ]
    if checker_dst_name:
        lines += [
            'checker: bridged',
            'checker_args:',
            f'  files: {checker_dst_name}',
            f"  lang: {info['checker_lang']}",
            '  type: testlib',
        ]
    lines.append('test_cases:')
    for tc in test_cases:
        lines += [
            f"  - in: {tc['in']}",
            f"    out: {tc['out']}",
            f"    points: {tc['points']}",
        ]
    with open(os.path.join(problem_dir, 'init.yml'), 'w') as f:
        f.write('\n'.join(lines) + '\n')
