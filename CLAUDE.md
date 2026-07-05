# polysyncvnoj

Tool tự động sync đề từ Polygon sang VNOJ (fork DMOJ), chạy định kỳ qua
systemd timer trên server judge.

- Spec đầy đủ + các bug đã fix cần giữ nguyên: xem `docs/polysyncvnoj_prompt.md`
- `polygon_import.py` ở root là script gốc đã hoạt động — refactor thành
  module, KHÔNG viết lại từ đầu.
- Trước khi chạy bất kỳ lệnh nào đụng vào `docker exec`, `manage.py shell`,
  hoặc xoá thư mục trong `problems/`: LUÔN hỏi xác nhận trước, đây là server
  judge đang chạy thật.