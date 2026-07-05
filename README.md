# polysyncvnoj

Tool tự động đồng bộ đề bài từ [Polygon](https://polygon.codeforces.com)
sang VNOJ (fork của DMOJ), chạy định kỳ qua systemd timer trên server judge.

---

## Cài đặt

```bash
# 1. Clone repo
git clone <repo_url> ~/tools/polysyncvnoj
cd ~/tools/polysyncvnoj

# 2. Cài dependency
pip install -r requirements.txt   # chỉ cần pyyaml

# 3. Tạo .env từ bản mẫu
cp .env.example .env
# Sau đó mở .env và điền API key/secret lấy từ
# https://polygon.codeforces.com/settings/api

# 4. Tạo sync_config.yml từ bản mẫu
cp sync_config.example.yml sync_config.yml
# Mở sync_config.yml, thêm polygon_id + code của từng bài cần sync
```

---

## Cài đặt Wine (cần cho package loại `standard`)

Polygon có 3 loại package: **linux**, **windows**, và **standard**.
Đa số bài chỉ có sẵn package `standard` — loại này **không kèm test data đã
sinh sẵn** cho các test `method='generated'` và cũng không có file `.a` trả lời.
Thay vào đó, package chứa `doall.sh` — khi chạy sẽ dùng generator/solution/checker
(đóng gói dạng `.exe`) qua Wine để sinh đủ input + answer cho mọi test.

### Cài Wine (chạy 1 lần trên server)

```bash
sudo dpkg --add-architecture i386
sudo apt install -y wine32 wine64 wine
```

### Khởi tạo Wine prefix (chạy 1 lần)

```bash
export WINEPREFIX=~/tools/polysyncvnoj/.wineprefix
export WINEDEBUG=-all
wineboot --init
```

Sau bước này, thư mục `.wineprefix/` sẽ được tạo trong thư mục project.
Đây là giá trị mặc định cho `--wine-prefix` khi chạy `cli.py sync`.

> **Lưu ý**: package `linux` hoặc `windows` (nếu có) được ưu tiên hơn
> `standard` — chúng đã có sẵn test data, không cần Wine và xử lý nhanh hơn.
> Tool tự động chọn package tốt nhất có sẵn.

---

## Sử dụng

```bash
# Sync tất cả bài trong config (skip bài không đổi)
python cli.py sync

# Ép sync lại toàn bộ, bỏ qua check state
python cli.py sync --force

# Chỉ sync 1 bài theo code (bỏ qua check state)
python cli.py sync --only hhy_jam

# Xem trạng thái sync của từng bài
python cli.py status

# Liệt kê bài có trên Polygon (tham khảo, không dùng để tự động sync)
python cli.py list-remote
```

### Options cho `sync`

| Option | Mặc định | Mô tả |
|---|---|---|
| `--force` | off | Bỏ qua check state, ép sync lại hết |
| `--only CODE` | — | Chỉ sync 1 bài theo code |
| `--problems-dir DIR` | `~/vnoj-docker/dmoj/problems` | Thư mục problems/ của vnoj-docker |
| `--site-container NAME` | `vnoj_site` | Tên Docker container của site |
| `--allow-zero-points` | off | Cho phép import bài không set points trên Polygon (tự chia đều) |
| `--wine-prefix PATH` | `~/tools/polysyncvnoj/.wineprefix` | WINEPREFIX dùng để chạy doall.sh khi package là `standard` |

---

## Cơ chế detect thay đổi

polysyncvnoj **không** dùng `problems.list` để detect thay đổi (field ngày
sửa đổi chưa được xác nhận từ tài liệu công khai).

Thay vào đó, với mỗi bài trong config:

1. Gọi `problem.packages(problemId)` — rẻ, không tải file nào.
2. Lọc `state == 'READY'`, lấy bản có `revision` lớn nhất → `package_id` mới nhất.
3. So sánh với `package_id` đã lưu trong `state.json`.
4. Nếu giống → skip ("unchanged, skip").
5. Nếu khác (hoặc chưa từng sync) → chạy full pipeline, cập nhật `state.json`.

---

## `polysync status` — ý nghĩa từng cột

| Cột | Ý nghĩa |
|---|---|
| `CODE` | Code bài trên VNOJ |
| `POLYGON_ID` | ID bài trên Polygon |
| `LAST_SYNCED_AT` | Thời điểm sync thành công gần nhất (UTC ISO 8601) |
| `STATUS` | `synced` / `never-synced` / `locked` |

> **Lưu ý**: `status` chỉ phản ánh thông tin trong `state.json` (local).
> Cột này **không** gọi API để kiểm tra xem Polygon có bản mới hơn không —
> muốn biết có pending hay không, chạy `polysync sync --force` hoặc
> `polysync sync` (nó sẽ tự check và báo "unchanged, skip").

---

## Setup systemd timer (khuyến nghị)

Systemd timer ưu tiên hơn cron vì:
- Log tích hợp qua `journalctl -u polysync`
- `Persistent=true`: chạy bù nếu server tắt đúng giờ lẽ ra phải chạy

### Bước 1: Tạo service file

```ini
# /etc/systemd/system/polysync.service
[Unit]
Description=Polygon → VNOJ sync
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=ms24
WorkingDirectory=/home/ms24/tools/polysyncvnoj
ExecStart=/usr/bin/python3 /home/ms24/tools/polysyncvnoj/cli.py sync
StandardOutput=journal
StandardError=journal
# Exit code != 0 khi có bài FAILED — systemd sẽ đánh dấu run là "failed"
# và bạn có thể dùng OnFailure= để gửi alert nếu cần.
```

### Bước 2: Tạo timer file

```ini
# /etc/systemd/system/polysync.timer
[Unit]
Description=Run polysync every 30 minutes
Requires=polysync.service

[Timer]
OnBootSec=5min
OnUnitActiveSec=30min
Persistent=true        # Chạy bù nếu server tắt đúng giờ lẽ ra phải chạy

[Install]
WantedBy=timers.target
```

### Bước 3: Kích hoạt

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now polysync.timer

# Kiểm tra
systemctl status polysync.timer
journalctl -u polysync -f
```

---

## Bảo mật

- `.env` chứa API key/secret — **không commit git** (đã có trong `.gitignore`).
- `state.json` — không commit git (chứa thông tin nội bộ về revision).
- API key/secret **không bao giờ được log** ra file hay stdout, kể cả ở log debug.

---

## Chạy unit test

```bash
python -m pytest tests/ -v
```

Các test không gọi API thật hay docker exec thật — hoàn toàn offline.
