#!/usr/bin/env python3
"""
polygon_import.py — End-to-end: Polygon API -> VNOJ problem, in one command.

CHẠY TRÊN SERVER (cần quyền docker exec vào container vnoj_site, và quyền ghi
vào thư mục problems/ của vnoj-docker).

Cấu hình qua biến môi trường (đặt 1 lần, ví dụ trong ~/.bashrc):
    export POLYGON_API_KEY="..."
    export POLYGON_API_SECRET="..."

Cách dùng:
    python3 polygon_import.py <polygon_problem_id> <vnoj_code> [options]

Ví dụ:
    python3 polygon_import.py 123456 hhy-jam --points 100 --partial

Options:
    --points N          Điểm tối đa của bài trên VNOJ (mặc định 100)
    --partial           Cho phép chấm điểm từng phần (nên bật nếu checker có
                         quitf(_points, ...) chấm thành phần)
    --group NAME        Tên ProblemGroup có sẵn trên VNOJ (mặc định "Uncategorized")
    --type NAME         Tên ProblemType có sẵn trên VNOJ (mặc định "uncategorized")
    --languages LIST    Danh sách ngôn ngữ cho phép, phân cách bởi dấu phẩy
                         (mặc định: tất cả ngôn ngữ compile được, không gồm SCRATCH/TEXT/OUTPUT)
    --public            Publish bài ngay (mặc định: tạo ở dạng private, is_public=False)
    --problems-dir DIR  Thư mục problems/ của vnoj-docker
                         (mặc định: ~/vnoj-docker/dmoj/problems)
    --site-container N  Tên container site (mặc định: vnoj_site)
    --allow-zero-points Cho phép import bài không có points trên Polygon
                         (tự chia đều 100 điểm cho các test). Mặc định TẮT —
                         script sẽ raise lỗi thay vì tạo bài 0 điểm âm thầm.

Quy trình:
    1. Gọi Polygon API (problem.packages + problem.package) để tải package mới nhất.
    2. Gọi Polygon API (problem.statements) để lấy đề bài dạng JSON (legend/input/output/notes).
    3. Convert test data + checker sang format VNOJ (tái sử dụng logic polygon2vnoj).
    4. Copy thư mục problem vào <problems-dir>/<code>/ (judge tự nhận qua
       problem_storage_globs, không cần upload qua web UI).
    5. Tạo (hoặc update) Problem trên DB qua `docker exec -i <site-container>
       python3 manage.py shell`, đánh dấu is_manually_managed=True (vì data
       nằm sẵn trên judge, không qua site quản lý).

Giới hạn cần biết:
    - Statement Polygon dùng LaTeX (\\begin{itemize}, \\item...). Script tự
      convert "best-effort" các cú pháp phổ biến (itemize, enumerate, textbf,
      textit, includegraphics -> ghi chú) sang Markdown. Công thức toán ($...$)
      giữ nguyên vì Martor/MathJax hiểu được. NÊN xem lại statement sau khi
      import, đặc biệt nếu đề dùng cú pháp LaTeX phức tạp hoặc bảng biểu.
    - Chỉ hỗ trợ checker non-interactive dùng testlib.h (đúng giới hạn của
      VNOJ được ghi trong tài liệu chính thức).
    - Cần có sẵn 1 model solution tag chính (Polygon gắn "MA", hiển thị trên
      UI là "Main") trong Polygon nếu package không có sẵn file đáp án (.a)
      — dùng để tự sinh đáp án. Script chỉ hỗ trợ compile solution viết bằng
      C/C++; solution ngôn ngữ khác sẽ báo lỗi rõ ràng thay vì crash mập mờ.
"""

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import string
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

POLYGON_API_BASE = 'https://polygon.codeforces.com/api'

LANG_EXT_MAP = {
    'cpp.g++17': 'CPP17',
    'cpp.g++14': 'CPP14',
    'cpp.g++11': 'CPP11',
    'cpp.ms': 'CPP17',
    'cpp.g++': 'CPP17',
    'cpp.g++0x': 'CPP11',
}

# Extensions Polygon uses for "type" attribute that we can safely g++-compile
# as a model solution to auto-generate answer files.
COMPILABLE_CPP_TYPES = set(LANG_EXT_MAP.keys())

# Polygon tags a solution's role with short codes. "MA" (Main) is the one we
# want; the human-readable "main" string sometimes also appears depending on
# export version, so we accept both.
MAIN_SOLUTION_TAGS = {'MA', 'main', 'Main'}

DEFAULT_LANGUAGES = ['C', 'C11', 'CPP03', 'CPP11', 'CPP14', 'CPP17', 'CPP20',
                     'JAVA', 'JAVA8', 'KOTLIN', 'PAS', 'PY2', 'PY3', 'PYPY', 'PYPY3']


# ---------------------------------------------------------------------------
# Polygon API client
# ---------------------------------------------------------------------------

def polygon_sign(method_name, params, api_key, api_secret):
    rand = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    params = dict(params)
    params['apiKey'] = api_key
    params['time'] = str(int(__import__('time').time()))
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


def download_polygon_package(problem_id, api_key, api_secret, dest_dir):
    print(f"[polygon] Fetching package list for problem {problem_id}...")
    packages = polygon_call('problem.packages', {'problemId': problem_id}, api_key, api_secret)
    ready = [p for p in packages if p['state'] == 'READY']
    if not ready:
        raise RuntimeError("No READY package found for this problem. "
                            "Build a package on Polygon first (Package tab -> Create package).")
    latest = max(ready, key=lambda p: p['revision'])
    package_id = latest['id']
    print(f"[polygon] Downloading package #{package_id} (revision {latest['revision']})...")

    zip_bytes = polygon_call('problem.package',
                              {'problemId': problem_id, 'packageId': package_id, 'type': 'linux'},
                              api_key, api_secret, raw=True)
    # problem.package returns the file content directly when successful,
    # but on error it returns a JSON error body. Detect that case.
    if zip_bytes[:1] in (b'{',):
        result = json.loads(zip_bytes)
        raise RuntimeError(f"Polygon API error on problem.package: {result.get('comment')}")

    zip_path = os.path.join(dest_dir, 'package.zip')
    with open(zip_path, 'wb') as f:
        f.write(zip_bytes)

    extract_dir = os.path.join(dest_dir, 'extracted')
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(extract_dir)
    print(f"[polygon] Extracted to {extract_dir}")
    return extract_dir


def fetch_statement(problem_id, api_key, api_secret, lang='english'):
    print("[polygon] Fetching statement (problem.statements)...")
    statements = polygon_call('problem.statements', {'problemId': problem_id}, api_key, api_secret)
    if lang not in statements:
        if not statements:
            return None
        lang = next(iter(statements))
    return statements[lang]


# ---------------------------------------------------------------------------
# LaTeX -> Markdown, best-effort
# ---------------------------------------------------------------------------

def latex_to_markdown(text):
    if not text:
        return ''
    text = text.replace('\\r\\n', '\n').replace('\r\n', '\n')

    # itemize / enumerate -> markdown bullet/numbered lists
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

    text = re.sub(r'\\begin\{itemize\}(.*?)\\end\{itemize\}', convert_itemize, text, flags=re.S)
    text = re.sub(r'\\begin\{enumerate\}(.*?)\\end\{enumerate\}', convert_enumerate, text, flags=re.S)

    # simple text styling
    text = re.sub(r'\\textbf\{([^}]*)\}', r'**\1**', text)
    text = re.sub(r'\\textit\{([^}]*)\}', r'*\1*', text)
    text = re.sub(r'\\emph\{([^}]*)\}', r'*\1*', text)

    # images: leave a visible note, since the image file needs manual upload
    text = re.sub(r'\\includegraphics(\[[^\]]*\])?\{([^}]*)\}',
                  r'\n> ⚠️ [Hình ảnh gốc: \2 — cần tự upload lại qua Martor image tool]\n', text)

    # \\ line breaks -> markdown line break
    text = text.replace('\\\\', '  \n')

    # leftover \begin{center}/\end{center} etc — just strip the wrapper, keep content
    text = re.sub(r'\\begin\{center\}(.*?)\\end\{center\}', r'\1', text, flags=re.S)

    # LaTeX-escaped punctuation that should just render as plain characters
    for esc, plain in [(r'\"', '"'), (r'\%', '%'), (r'\&', '&'),
                       (r'\_', '_'), (r'\#', '#'), (r'\{', '{'), (r'\}', '}')]:
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
# Package -> VNOJ data conversion (test cases + checker + init.yml)
# ---------------------------------------------------------------------------

def parse_problem_xml(pkg_dir, allow_zero_points=False):
    tree = ET.parse(os.path.join(pkg_dir, 'problem.xml'))
    root = tree.getroot()
    info = {}

    names = root.find('names')
    name = None
    for n in names.findall('name'):
        if n.get('language') == 'english':
            name = n.get('value')
            break
    if name is None and len(names):
        name = names[0].get('value')
    info['name'] = name or 'Untitled'

    judging = root.find('judging')
    input_file = judging.get('input-file') or ''
    output_file = judging.get('output-file') or ''
    info['file_io'] = bool(input_file) and input_file != 'stdin'
    info['input_file'] = input_file
    info['output_file'] = output_file

    # A problem can have multiple <testset> nodes (e.g. "tests", "pretests").
    # Prefer the one literally named "tests"; fall back to the first one.
    testsets = judging.findall('testset')
    testset = next((t for t in testsets if t.get('name') == 'tests'), None) or testsets[0]

    info['time_limit'] = round(int(testset.find('time-limit').text) / 1000.0, 3)
    info['memory_limit_kb'] = int(testset.find('memory-limit').text) // 1024
    info['test_count'] = int(testset.find('test-count').text)

    tests_node = testset.find('tests')
    points_list = []
    if tests_node is not None:
        for t in tests_node.findall('test'):
            points_list.append(float(t.get('points', 0)))
    if len(points_list) != info['test_count']:
        points_list = [0.0] * info['test_count']

    # A Polygon problem with no per-test points set (common for plain "sample"
    # problems, or ICPC-style problems not configured for scoring) will end up
    # with points_list summing to 0. Silently scaling by "0 * anything" makes
    # every test worth 0 points, i.e. the problem becomes uncompletable while
    # still showing e.g. "100 điểm" on the site. Fail loudly instead, unless
    # the caller explicitly opted into an even split via --allow-zero-points.
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
        print(f"[convert] ⚠️  '{info['name']}' không có points trên Polygon — "
              "tự chia đều điểm theo --allow-zero-points.")
        base = 100 // info['test_count']
        points_list = [base] * info['test_count']
        points_list[-1] += 100 - base * info['test_count']

    info['points_list'] = points_list

    checker_node = root.find('assets/checker')
    checker_source, checker_lang = None, 'CPP17'
    if checker_node is not None:
        src = checker_node.find('source')
        if src is not None:
            checker_source = src.get('path')
            checker_lang = LANG_EXT_MAP.get(src.get('type'), 'CPP17')
    info['checker_source'] = checker_source
    info['checker_lang'] = checker_lang

    main_solution = None
    main_solution_type = None
    for sol in root.findall('assets/solutions/solution'):
        if sol.get('tag') in MAIN_SOLUTION_TAGS:
            src = sol.find('source')
            if src is not None:
                main_solution = src.get('path')
                main_solution_type = src.get('type')
            break
    info['main_solution'] = main_solution
    info['main_solution_type'] = main_solution_type

    return info


def compile_cpp(src_path, out_path, solution_type=None):
    if solution_type is not None and solution_type not in COMPILABLE_CPP_TYPES:
        raise RuntimeError(
            f"Model solution '{src_path}' có type Polygon là '{solution_type}', "
            "không phải C++. Script này chỉ tự compile được model solution viết "
            "bằng C++ để sinh đáp án. Hãy tự cung cấp file .a cho các test còn "
            "thiếu, hoặc đổi model solution 'main' trên Polygon sang C++."
        )
    result = subprocess.run(['g++', '-O2', '-std=c++17', '-o', out_path, src_path],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Compile failed for {src_path}:\n{result.stderr}")


def run_solution_for_answer(binary_path, input_path, answer_path, file_io_names, test_label=''):
    if file_io_names:
        input_file, output_file = file_io_names
        with tempfile.TemporaryDirectory() as tmpdir:
            shutil.copy(input_path, os.path.join(tmpdir, input_file))
            proc = subprocess.run([os.path.abspath(binary_path)], cwd=tmpdir,
                                   capture_output=True, timeout=30)
            if proc.returncode != 0:
                raise RuntimeError(
                    f"Model solution thoát với mã lỗi {proc.returncode} khi sinh "
                    f"đáp án cho test {test_label}. stderr:\n{proc.stderr.decode(errors='replace')}"
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
            proc = subprocess.run([os.path.abspath(binary_path)], stdin=fin,
                                   capture_output=True, timeout=30)
        if proc.returncode != 0:
            raise RuntimeError(
                f"Model solution thoát với mã lỗi {proc.returncode} khi sinh đáp "
                f"án cho test {test_label}. stderr:\n{proc.stderr.decode(errors='replace')}"
            )
        with open(answer_path, 'wb') as fout:
            fout.write(proc.stdout)


def convert_package(pkg_dir, problem_dir, points_total=100, partial=False, allow_zero_points=False):
    info = parse_problem_xml(pkg_dir, allow_zero_points=allow_zero_points)
    os.makedirs(problem_dir, exist_ok=True)

    solution_binary = None
    if info['main_solution']:
        sol_src = os.path.join(pkg_dir, info['main_solution'])
        if os.path.exists(sol_src):
            solution_binary = os.path.join(problem_dir, '.model_solution_bin')
            print(f"[convert] Compiling model solution: {info['main_solution']} "
                  f"(type={info['main_solution_type']})")
            compile_cpp(sol_src, solution_binary, solution_type=info['main_solution_type'])

    file_io_names = (info['input_file'], info['output_file']) if info['file_io'] else None

    # Scale each test's raw Polygon points into the target VNOJ point pool
    raw_total = sum(info['points_list'])  # guaranteed > 0 at this point
    scale = points_total / raw_total

    test_cases = []
    for i in range(1, info['test_count'] + 1):
        idx = f"{i:02d}"
        in_src = os.path.join(pkg_dir, 'tests', idx)
        in_dst = os.path.join(problem_dir, f"{i}.in")
        out_dst = os.path.join(problem_dir, f"{i}.out")
        shutil.copy(in_src, in_dst)

        ans_src = os.path.join(pkg_dir, 'tests', f"{idx}.a")
        if os.path.exists(ans_src):
            shutil.copy(ans_src, out_dst)
        else:
            if not solution_binary:
                raise RuntimeError(f"Missing answer for test {idx} and no model solution available.")
            print(f"[convert] Generating answer for test {idx} via model solution...")
            run_solution_for_answer(solution_binary, in_dst, out_dst, file_io_names, test_label=idx)

        pts = round(info['points_list'][i - 1] * scale, 2)
        pts = int(pts) if pts == int(pts) else pts
        test_cases.append({'points': pts, 'in': f"{i}.in", 'out': f"{i}.out"})

    if solution_binary and os.path.exists(solution_binary):
        os.remove(solution_binary)

    checker_dst_name = None
    if info['checker_source']:
        shutil.copy(os.path.join(pkg_dir, info['checker_source']),
                    os.path.join(problem_dir, 'checker.cpp'))
        checker_dst_name = 'checker.cpp'
        testlib_src = os.path.join(pkg_dir, 'files', 'testlib.h')
        if os.path.exists(testlib_src):
            shutil.copy(testlib_src, os.path.join(problem_dir, 'testlib.h'))

    write_init_yml(problem_dir, info, test_cases, checker_dst_name)
    return info


def write_init_yml(problem_dir, info, test_cases, checker_dst_name):
    lines = [f"time_limit: {info['time_limit']}", f"memory_limit: {info['memory_limit_kb']}"]
    if info['file_io']:
        lines += ["file_io:", f"  input: {info['input_file']}", f"  output: {info['output_file']}"]
    if checker_dst_name:
        # DMOJ/VNOJ expects `checker` as a nested mapping with `name` and `args`
        # sub-keys — NOT a flat string with a sibling `checker_args` key.
        # The flat form causes "TypeError: check() missing 1 required positional
        # argument: 'files'" on the judge.  Confirmed against real VNOJ-generated
        # init.yml (uploaded via web UI).
        lines += [
            "checker:",
            "  name: bridged",
            "  args:",
            f"    files: {checker_dst_name}",
            f"    lang: {info['checker_lang']}",
            "    type: testlib",
        ]
    lines.append("test_cases:")
    for tc in test_cases:
        lines += [f"  - in: {tc['in']}", f"    out: {tc['out']}", f"    points: {tc['points']}"]
    with open(os.path.join(problem_dir, 'init.yml'), 'w') as f:
        f.write('\n'.join(lines) + '\n')


# ---------------------------------------------------------------------------
# Push to VNOJ DB via `manage.py shell`
# ---------------------------------------------------------------------------

CREATE_PROBLEM_SCRIPT = r'''
import json
from judge.models import Problem, ProblemGroup, ProblemType, Language

with open('/problems/.polygon_import_payload.json') as f:
    payload = json.load(f)

group, _ = ProblemGroup.objects.get_or_create(
    name=payload['group'], defaults={'full_name': payload['group']})
ptype, _ = ProblemType.objects.get_or_create(
    name=payload['type'], defaults={'full_name': payload['type']})

problem, created = Problem.objects.get_or_create(
    code=payload['code'],
    defaults={
        'name': payload['name'],
        'description': payload['description'],
        'time_limit': payload['time_limit'],
        'memory_limit': payload['memory_limit'],
        'points': payload['points'],
        'partial': payload['partial'],
        'group': group,
        'is_public': payload['is_public'],
        'is_manually_managed': True,
    },
)
if not created:
    problem.name = payload['name']
    problem.description = payload['description']
    problem.time_limit = payload['time_limit']
    problem.memory_limit = payload['memory_limit']
    problem.points = payload['points']
    problem.partial = payload['partial']
    problem.is_manually_managed = True
    problem.save()

problem.types.set([ptype])
langs = Language.objects.filter(key__in=payload['languages'])
problem.allowed_languages.set(langs)

print(f"{'CREATED' if created else 'UPDATED'} problem code={problem.code} id={problem.id}")
'''


def push_to_vnoj(problems_dir, site_container, payload):
    payload_path = os.path.join(problems_dir, '.polygon_import_payload.json')
    with open(payload_path, 'w') as f:
        json.dump(payload, f)

    script_path = os.path.join(tempfile.gettempdir(), 'create_problem.py')
    with open(script_path, 'w') as f:
        f.write(CREATE_PROBLEM_SCRIPT)

    print(f"[vnoj] Creating/updating Problem via {site_container} manage.py shell...")
    with open(script_path, 'rb') as f:
        result = subprocess.run(
            ['docker', 'exec', '-i', site_container, 'python3', '/site/manage.py', 'shell'],
            stdin=f, capture_output=True, text=True,
        )
    os.remove(payload_path)
    print(result.stdout)
    if result.returncode != 0 or 'Traceback' in result.stderr:
        print(result.stderr, file=sys.stderr)
        raise RuntimeError("Failed to create Problem on VNOJ. See traceback above.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('problem_id', help='Polygon problem ID (số, xem trên URL Polygon)')
    ap.add_argument('code', help='Code của problem trên VNOJ (chữ thường, số, gạch dưới)')
    ap.add_argument('--points', type=float, default=100)
    ap.add_argument('--partial', action='store_true')
    ap.add_argument('--group', default='Uncategorized')
    ap.add_argument('--type', dest='ptype', default='uncategorized')
    ap.add_argument('--languages', default=','.join(DEFAULT_LANGUAGES))
    ap.add_argument('--public', action='store_true')
    ap.add_argument('--problems-dir', default=os.path.expanduser('~/vnoj-docker/dmoj/problems'))
    ap.add_argument('--site-container', default='vnoj_site')
    ap.add_argument('--allow-zero-points', action='store_true',
                     help='Cho phép import bài không có points trên Polygon (tự chia đều).')
    args = ap.parse_args()

    api_key = os.environ.get('POLYGON_API_KEY')
    api_secret = os.environ.get('POLYGON_API_SECRET')
    if not api_key or not api_secret:
        sys.exit("Thiếu POLYGON_API_KEY / POLYGON_API_SECRET trong biến môi trường.")

    if not re.match(r'^[a-z0-9_]+$', args.code):
        sys.exit("Code phải khớp ^[a-z0-9_]+$ (chữ thường, số, gạch dưới).")

    with tempfile.TemporaryDirectory() as tmp:
        pkg_dir = download_polygon_package(args.problem_id, api_key, api_secret, tmp)
        statement = fetch_statement(args.problem_id, api_key, api_secret)
        description = build_description_markdown(statement)

        problem_dir = os.path.join(args.problems_dir, args.code)
        if os.path.exists(problem_dir):
            print(f"[convert] {problem_dir} đã tồn tại, sẽ ghi đè test data.")
            shutil.rmtree(problem_dir)

        info = convert_package(pkg_dir, problem_dir, points_total=args.points,
                                partial=args.partial, allow_zero_points=args.allow_zero_points)

        payload = {
            'code': args.code,
            'name': info['name'],
            'description': description,
            'time_limit': info['time_limit'],
            'memory_limit': info['memory_limit_kb'],
            'points': args.points,
            'partial': args.partial,
            'group': args.group,
            'type': args.ptype,
            'is_public': args.public,
            'languages': args.languages.split(','),
        }
        push_to_vnoj(args.problems_dir, args.site_container, payload)

    print(f"\nHoàn tất! Xem bài tại: /problem/{args.code}")
    print("Nhớ kiểm tra lại statement (LaTeX -> Markdown chỉ convert best-effort).")


if __name__ == '__main__':
    main()