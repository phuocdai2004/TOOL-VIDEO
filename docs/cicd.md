# CI/CD tự động

Hệ thống tự động hóa gồm ba bước:

1. Mỗi lần đẩy code lên nhánh `main`, workflow `Unit Tests & Coverage` chạy toàn bộ test với MongoDB 8.
2. Nếu test thành công, workflow `Build and Deploy` build Docker image đa kiến trúc và đẩy lên GHCR với các tag `latest`, `main`, `sha-...`.
3. Nếu đã bật triển khai SSH, máy chủ tự động lấy đúng commit vừa qua kiểm thử và image cùng mã SHA, sau đó khởi động lại bằng Docker Compose.

## Cấu hình GitHub

Vào `Settings > Secrets and variables > Actions` của repository.

Tạo repository variable:

| Tên | Giá trị |
| --- | --- |
| `DEPLOY_ENABLED` | `true` để bật deploy SSH; bỏ trống hoặc `false` nếu chỉ cần build image |
| `DEPLOY_PORT` | Cổng SSH, thường là `22` |

Tạo repository secrets:

| Tên | Nội dung |
| --- | --- |
| `DEPLOY_HOST` | IP hoặc domain của máy chủ |
| `DEPLOY_USER` | Tài khoản SSH |
| `DEPLOY_SSH_KEY` | Private key dùng để SSH |
| `DEPLOY_PATH` | Thư mục đã clone dự án trên máy chủ |

## Chuẩn bị máy chủ

Máy chủ cần có Git, Docker và Docker Compose. Tại `DEPLOY_PATH`, tạo file `.env` riêng cho production:

```dotenv
APP_IMAGE=ghcr.io/phuocdai2004/tool-video:latest
MONGODB_URI=mongodb+srv://USER:PASSWORD@HOST/
MONGODB_DB=agnes_video
PUBLIC_BASE_URL=https://ten-mien-cua-ban.vn
AUTH_COOKIE_SECURE=true
AGNES_API_KEY=
GEMINI_API_KEY=
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM=
SMTP_USE_TLS=true
```

Không đưa `.env`, API key, mật khẩu MongoDB hoặc mật khẩu email lên Git. Nếu GHCR package đang private, đăng nhập một lần trên máy chủ bằng token có quyền `read:packages`:

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u TEN_GITHUB --password-stdin
```

Sau khi cấu hình xong, chỉ cần `git push origin main`. Có thể chạy lại thủ công trong tab `Actions > Build and Deploy > Run workflow`.
