# Triển khai EduAI lên Internet (link cố định, không cần chạy máy local)

Hướng dẫn này giúp bạn có **1 đường link duy nhất** để đăng nhập vào EduAI mọi lúc,
mọi nơi, không cần mở terminal hay bật máy tính chạy `uvicorn`/`http.server` nữa.

Cách làm: Backend deploy lên **Render** (miễn phí), Frontend deploy lên **Netlify**
(miễn phí, kéo-thả không cần biết code).

---

## Bước 1 — Đưa code lên GitHub (nếu chưa có)

1. Tạo tài khoản tại https://github.com (miễn phí).
2. Tạo repository mới, đặt tên `eduai`.
3. Upload toàn bộ thư mục `eduai/` (đã giải nén) lên repo đó — có thể dùng nút
   "Add file → Upload files" trên giao diện web GitHub, kéo-thả cả thư mục vào.

## Bước 2 — Deploy Backend lên Render

1. Vào https://render.com → Đăng ký/đăng nhập (dùng tài khoản GitHub cho nhanh).
2. Chọn **New +** → **Blueprint**.
3. Chọn repo `eduai` vừa tạo. Render sẽ tự đọc file `render.yaml` đã có sẵn trong
   project (mình đã chuẩn bị sẵn, không cần tự điền build command/start command).
4. Nhấn **Apply** / **Create**. Đợi vài phút để Render cài đặt và khởi động.
5. Sau khi xong, Render cấp cho bạn một link dạng:
   `https://eduai-backend-xxxx.onrender.com`
   → Mở link đó + `/health` (ví dụ `https://eduai-backend-xxxx.onrender.com/health`)
   để kiểm tra thấy `{"status":"ok"}` là backend đã chạy thành công trên Internet.

**Lưu ý về gói miễn phí của Render:**
- Nếu không có ai truy cập trong ~15 phút, server sẽ "ngủ"; lần truy cập đầu tiên
  sau đó sẽ mất khoảng 30–50 giây để "thức dậy" — đây là bình thường, không phải lỗi.
- Gói miễn phí không có ổ đĩa lưu trữ lâu dài, nên dữ liệu (tài khoản mới tạo, kết
  quả bài kiểm tra...) có thể bị reset về dữ liệu demo ban đầu mỗi khi Render khởi
  động lại server (redeploy, hoặc sau thời gian dài không hoạt động). Với mục đích
  demo/chấm điểm đồ án thì không ảnh hưởng gì; nếu cần lưu dữ liệu vĩnh viễn, cần
  nâng cấp sang Render Postgres hoặc gói trả phí có persistent disk.

## Bước 3 — Trỏ Frontend về Backend vừa deploy

1. Mở file `frontend/js/api.js` trong repo (sửa trực tiếp trên GitHub cũng được:
   vào file → nút bút chì "Edit").
2. Tìm dòng:
   ```js
   const API_BASE = window.EDUAI_API_BASE || "http://localhost:8000";
   ```
   Đổi thành đúng link Render ở Bước 2:
   ```js
   const API_BASE = window.EDUAI_API_BASE || "https://eduai-backend-xxxx.onrender.com";
   ```
3. Lưu (Commit changes) trên GitHub.

## Bước 4 — Deploy Frontend lên Netlify (kéo-thả, không cần git)

1. Vào https://app.netlify.com/drop
2. Kéo toàn bộ thư mục `frontend/` (đã sửa API_BASE ở Bước 3, tải lại về máy nếu
   sửa trên GitHub) vào ô thả file trên trang đó.
3. Netlify tự động cấp ngay một link dạng: `https://random-name-xxxx.netlify.app`
4. Mở link đó + `/pages/login.html`, ví dụ:
   `https://random-name-xxxx.netlify.app/pages/login.html`
   → Đây chính là link bạn cần — **bấm vào là đăng nhập được ngay, không cần chạy
   gì trên máy cả.**

(Muốn link đẹp hơn: trong Netlify → Site settings → Change site name, đổi thành
tên bạn muốn, ví dụ `eduai-chatbot.netlify.app`.)

---

## Tóm tắt link cuối cùng

- Link chia sẻ cho người khác (giảng viên, bạn bè...):
  `https://<tên-site-của-bạn>.netlify.app/pages/login.html`
- Sinh viên tự đăng ký ở trang đăng ký. Admin: đặt biến môi trường `EDUAI_ADMIN_PASSWORD` trên
  Render (Environment) trước khi deploy; đặt thêm `EDUAI_SECRET_KEY` là một chuỗi ngẫu nhiên dài.

Từ giờ, ai bấm vào link Netlify đó đều dùng được ngay — máy tính của bạn có tắt
cũng không ảnh hưởng, vì cả backend (Render) và frontend (Netlify) đều chạy trên
server của họ, không phải máy bạn.
