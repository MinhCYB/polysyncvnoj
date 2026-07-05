# Prompt cho Claude Code — Build `polysyncvnoj`

Dán nguyên văn phần dưới đây (từ "## Bối cảnh" trở xuống) làm prompt đầu tiên cho
Claude Code trong thư mục `~/tools/polysyncvnoj/`. Trước khi chạy, nhớ đặt sẵn
file `polygon_import.py` (bản đã fix bug) vào thư mục đó — Claude Code sẽ đọc
và refactor file này thành module chứ không viết lại từ đầu.

---

## Bối cảnh

Mình đang build `polysyncvnoj` — tool tự động đồng bộ đề bài từ Polygon
(polygon.codeforces.com) sang VNOJ (fork của DMOJ, chạy trong Docker trên
server nhà mình), chạy định kỳ qua cron/systemd timer, không có người canh.

Trong thư mục hiện tại đã có sẵn `polygon_import.py` — một script CLI đã
chạy được, xử lý đúng 1 bài mỗi lần gọi: tải package từ Polygon, convert test
data + checker sang format VNOJ, ghi `init.yml`, copy vào thư mục problems của
judge, rồi tạo/update record `Problem` trên DB qua
`docker exec -i vnoj_site python3 manage.py shell`.

**Yêu cầu quan trọng nhất: đọc kỹ toàn bộ `polygon_import.py` trước, và khi
refactor thành module, PHẢI giữ nguyên các hành vi sau (đây là bug đã từng có
và đã fix, đừng viết lại theo bản năng và vô tình tái tạo lại bug cũ):**

1. Trong `init.yml`, checker phải ghi ở dạng **flat**:
   ```yaml
   checker: bridged
   checker_args:
     files: checker.cpp
     lang: CPP17
     type: testlib
   ```
   KHÔNG được viết dạng nested `checker: {name: bridged, args: {...}}` — đó là
   format sai, DMOJ/VNOJ sẽ không parse đúng.

2. Nếu tổng points của tất cả test case trong 1 bài Polygon = 0 (tức problem
   chưa set points cho từng test trên Polygon), **phải raise lỗi rõ ràng và
   dừng lại**, không được tự động chia đều 100 điểm một cách âm thầm — trừ khi
   người dùng chủ động bật cờ `--allow-zero-points`. Lý do: tạo bài với
   `points: 0` trên mọi test case trong khi DB vẫn ghi `problem.points = 100`
   sẽ khiến bài không ai chấm được điểm mà không có cảnh báo gì.

3. Model solution dùng để tự sinh đáp án (`.a` file) phải nhận diện đúng tag
   Polygon dùng cho "solution chính" — chấp nhận cả `"MA"` (mã Polygon dùng
   nội bộ) lẫn `"main"`/`"Main"`. Chỉ hỗ trợ compile solution viết bằng C++;
   nếu solution "main" là ngôn ngữ khác, raise lỗi rõ ràng thay vì để `g++`
   crash mập mờ.

4. Khi chạy model solution để sinh đáp án, phải check return code của process
   — nếu solution runtime-error/segfault, raise lỗi thay vì âm thầm ghi ra
   file `.out` rỗng hoặc sai.

5. `testset` trong `problem.xml` có thể có nhiều node (ví dụ có cả pretest) —
   ưu tiên lấy testset có `name="tests"`, fallback lấy node đầu tiên nếu không
   có testset tên đó.

Nếu có gì trong `polygon_import.py` không rõ ràng hoặc bạn nghi ngờ là bug,
hỏi mình trước khi tự ý sửa logic nghiệp vụ (không áp dụng cho refactor thuần
cấu trúc code, ví dụ tách hàm ra file khác thì cứ làm).

## Kiến trúc mong muốn

```
polysyncvnoj/
    polysync/
        __init__.py
        polygon_api.py   # polygon_sign, polygon_call, download_polygon_package,
                          # fetch_statement, fetch_latest_package_meta (mới, xem dưới)
        convert.py        # parse_problem_xml, convert_package, write_init_yml,
                           # latex_to_markdown, build_description_markdown
        vnoj.py             # push_to_vnoj, CREATE_PROBLEM_SCRIPT
        state.py            # đọc/ghi state.json, so sánh revision để quyết định skip
        config.py           # đọc/validate sync_config.yml
    sync_config.yml           # xem schema bên dưới — GIỮ FILE MẪU, đừng để trống
    sync_config.example.yml    # bản mẫu có comment, an toàn để commit git
    state.json                   # tự sinh khi chạy, KHÔNG commit git
    .env                            # POLYGON_API_KEY, POLYGON_API_SECRET — KHÔNG commit git
    .env.example                     # bản mẫu rỗng, commit được
    .gitignore                        # phải chặn .env và state.json
    cli.py                              # entrypoint chính, xem phần CLI bên dưới
    logs/                                 # log file mỗi lần chạy, KHÔNG commit git
    requirements.txt                       # pyyaml là dependency mới cần thêm
    README.md                                # hướng dẫn cài đặt + cron/systemd setup
    tests/
        test_state.py                          # unit test cho logic so sánh revision
        test_config.py                          # unit test validate config
        fixtures/
            sample_problem_xml/                    # problem.xml giả để test parse
```

## Cơ chế detect thay đổi (quan trọng, đây là lý do tách state.py riêng)

**Không dùng `problems.list` để detect thay đổi** — field ngày sửa đổi trong
response của method đó chưa được verify chắc chắn từ tài liệu công khai, rủi
ro cao nếu đoán sai tên field rồi tool âm thầm không detect được gì.

Thay vào đó, dùng lại đúng API đã chứng minh hoạt động trong
`polygon_import.py`: **`problem.packages`**. Với mỗi bài trong
`sync_config.yml`:

1. Gọi `problem.packages(problemId)`, lọc `state == 'READY'`, lấy bản có
   `revision` lớn nhất → được `package_id` mới nhất.
2. So `package_id` này với giá trị đã lưu trong `state.json` (key theo
   `polygon_id`).
3. Nếu giống nhau → skip bài này, log "unchanged, skip", không tải gì thêm.
4. Nếu khác (hoặc chưa từng sync) → chạy full pipeline (download → convert →
   push), rồi update `state.json` với `package_id` mới + timestamp.

Việc gọi `problem.packages` để check là rẻ (không tải file), nên hàm
`fetch_latest_package_meta(problem_id)` trong `polygon_api.py` nên tách riêng
khỏi `download_polygon_package` (hàm cũ tải luôn cả zip) để dùng cho bước check
nhanh này mà không cần tải package nếu không cần thiết.

## `sync_config.yml` schema

```yaml
defaults:
  group: "Uncategorized"
  type: "uncategorized"
  languages: [CPP17, CPP20, PY3, PYPY3, JAVA]
  partial: false
  public: false

problems:
  - polygon_id: 123456
    code: hhy-jam           # bắt buộc, phải khớp ^[a-z0-9_]+$
    points: 100
    partial: true
    group: "HHY Contest 2026"
  - polygon_id: 234567
    code: another-one        # phần còn lại dùng defaults ở trên
    locked: true               # nếu true, sync bỏ qua bài này dù revision đổi
                                # (dùng khi bài đang có người thi trên VNOJ)
```

Validate: `code` phải unique trong file, phải khớp regex
`^[a-z0-9_]+$` (đã có sẵn logic check này trong `polygon_import.py`, tái sử
dụng lại). Nếu file có `code` trùng nhau, raise lỗi config rõ ràng ngay khi
load, đừng để chạy nửa chừng mới phát hiện.

## CLI (`cli.py`, dùng argparse hoặc click tùy bạn thấy hợp)

```
polysync sync                  # sync tất cả bài trong config, skip bài unchanged
polysync sync --force          # sync tất cả, bỏ qua check state (ép sync lại hết)
polysync sync --only hhy-jam   # chỉ sync 1 bài theo code, bỏ qua check state
polysync status                # in bảng: code | polygon_id | last_synced_at | trạng thái (up-to-date/pending/locked/never-synced)
polysync list-remote           # gọi problems.list để liệt kê bài có trên Polygon (tiện tham khảo, KHÔNG dùng để tự động sync)
```

Yêu cầu hành vi của lệnh `sync`:

- **Cách ly lỗi từng bài**: wrap try/except quanh từng bài trong vòng lặp.
  1 bài lỗi không được làm crash cả batch. Cuối cùng in báo cáo tổng kết dạng:
  ```
  ==== Sync report ====
  OK:      8 bài
  SKIPPED: 3 bài (unchanged)
  FAILED:  1 bài
    - hhy-jam: RuntimeError: Compile failed for solutions/std.cpp: ...
  ```
  Exit code: 0 nếu không có FAILED, khác 0 nếu có ít nhất 1 FAILED (để cron
  job / systemd có thể detect lỗi qua exit code).

- **Rate limit**: `time.sleep(1)` giữa các lần gọi Polygon API liên tiếp
  (an toàn, phòng Polygon giới hạn giống Codeforces API chính — tối đa
  khoảng 1 request/2 giây).

- **Bỏ qua bài `locked: true`** trong config, log rõ lý do skip.

- **Logging**: ghi log ra cả stdout (để xem trực tiếp khi chạy tay) và ra file
  `logs/sync-YYYYMMDD.log` (để xem lại khi chạy cron không ai canh). Dùng
  module `logging` chuẩn, không tự chế print + file write tay.

## Bảo mật / vệ sinh repo

- `.env` chứa `POLYGON_API_KEY`, `POLYGON_API_SECRET` — đọc bằng
  `python-dotenv` hoặc tự parse đơn giản, không hardcode.
- `.gitignore` phải chặn: `.env`, `state.json`, `logs/`, `__pycache__/`,
  `*.pyc`.
- Không log giá trị API key/secret ra log file dưới bất kỳ hình thức nào,
  kể cả log debug.

## Testing trước khi coi là xong

- Viết unit test cho `state.py` (so sánh revision, đọc/ghi state.json đúng
  format) và `config.py` (validate code trùng, regex code sai).
- Viết ít nhất 1 test dùng `problem.xml` giả (không gọi API thật) để confirm
  `convert.py` giữ đúng 5 hành vi đã liệt kê ở đầu prompt này (checker format,
  raise khi points=0, tag MA, check return code model solution, chọn đúng
  testset).
- KHÔNG cần test gọi Polygon API thật hay `docker exec` thật trong unit test
  — mock các phần đó.
- Sau khi code xong, chạy thử `polysync sync --only <code_thật>` với 1 bài
  thật trên server (mình sẽ tự làm bước này, không cần bạn có API key).

## README.md cần có

- Hướng dẫn cài đặt (`pip install -r requirements.txt`, tạo `.env` từ
  `.env.example`, tạo `sync_config.yml` từ `sync_config.example.yml`).
- Ví dụ setup **systemd timer** (ưu tiên hơn cron vì có log tích hợp qua
  `journalctl` và hỗ trợ `Persistent=true` để chạy bù nếu server tắt đúng giờ
  chạy) — gồm cả file `.service` và `.timer` mẫu.
- Giải thích ngắn gọn cơ chế detect thay đổi qua `problem.packages` + ý nghĩa
  từng cột trong output của `polysync status`.

---

Nếu bước nào ở trên chưa rõ hoặc bạn thấy có cách làm tốt hơn đáng cân nhắc
(ví dụ thư viện CLI, cấu trúc test), cứ hỏi lại trước khi code thay vì tự
quyết định luôn — đây là tool chạy trên server thật, tự động định kỳ, ảnh
hưởng trực tiếp tới đề thi trên VNOJ.