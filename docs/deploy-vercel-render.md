# Deploy miễn phí với Vercel và Render

Kiến trúc triển khai:

- Vercel phục vụ thư mục `static/` qua CDN.
- Mọi request `/api/*` trên Vercel được proxy sang Render, vì vậy cookie đăng nhập vẫn là same-origin.
- Render chạy Docker image đã được GitHub Actions kiểm thử và build trên GHCR.
- MongoDB Atlas giữ tài khoản và phiên đăng nhập.

## 1. Tạo backend Render

Trước tiên, bảo đảm `render.yaml` đã nằm trên nhánh `main`, sau đó mở:

<https://dashboard.render.com/blueprint/new?repo=https://github.com/phuocdai2004/TOOL-VIDEO>

Khi Render yêu cầu, nhập trực tiếp các biến bí mật sau trong Dashboard:

- `MONGODB_URI`
- `AGNES_API_KEY`
- `GEMINI_API_KEY`
- `ADMIN_EMAIL`
- `PUBLIC_BASE_URL` (điền URL Vercel sau khi tạo frontend)
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_FROM`

Không gửi các giá trị này qua chat và không ghi chúng vào Git.

Blueprint tạo backend tại địa chỉ dự kiến:

`https://tool-video-api-phuocdai2004.onrender.com`

Mở `/api/auth/status` để kiểm tra backend. Nếu Render đổi tên service vì tên đã tồn tại, cập nhật URL tương ứng trong `vercel.json` rồi push lại.

## 2. Tạo frontend Vercel

Trong Vercel Dashboard, chọn `Add New > Project`, import repository `phuocdai2004/TOOL-VIDEO`, giữ Root Directory là thư mục gốc rồi deploy.

Vercel đọc `vercel.json`, xuất bản `static/` và proxy `/api/*` sang Render. Sau khi có URL Vercel, quay lại Render và đặt:

```text
PUBLIC_BASE_URL=https://ten-project.vercel.app
```

Biến này được dùng để tạo liên kết khôi phục mật khẩu đúng domain.

## 3. Bật cập nhật Render tự động

Trong Render service, mở `Settings > Deploy Hook` và sao chép URL hook. Trong GitHub repository, vào `Settings > Secrets and variables > Actions`, tạo secret:

```text
RENDER_DEPLOY_HOOK=<URL hook từ Render>
```

Từ lần push `main` tiếp theo, chuỗi tự động là:

```text
GitHub test -> build GHCR image theo SHA -> gọi Render Deploy Hook -> Render kéo image mới
```

Vercel tự deploy frontend khi repository có commit mới.

## Giới hạn gói miễn phí

- Render ngủ sau 15 phút không có request; giao diện Vercel vẫn mở ngay nhưng API có thể cần khoảng một phút để thức dậy.
- Render Free dùng ổ đĩa tạm. Hãy tải video hoàn thành về máy trước khi service ngủ, restart hoặc deploy lại.
- Trong lúc tạo video, giữ trang tiến trình mở để polling tiếp tục gửi request đến Render.
