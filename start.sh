#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

echo "================================================"
echo "  Agnes Video Generator"
echo "================================================"
echo ""

# ── L5: Kiểm tra môi trường ──────────────────────────────────────────────

# Kiểm tra Python 3.10+
if ! command -v python3 &> /dev/null; then
    echo "❌ Không tìm thấy python3, vui lòng cài đặt Python 3.10+"
    echo "   macOS:   brew install python3"
    echo "   Ubuntu:  sudo apt install python3 python3-venv"
    exit 1
fi

python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null || {
    PY_VER=$(python3 --version 2>&1)
    echo "❌ Phiên bản Python quá thấp ($PY_VER), yêu cầu 3.10+"
    exit 1
}

# Kiểm tra ffmpeg (dùng cho ghép video và xử lý âm thanh)
if ! command -v ffmpeg &> /dev/null; then
    echo "❌ Không tìm thấy ffmpeg, ffmpeg cần cho xử lý video"
    echo "   macOS:   brew install ffmpeg"
    echo "   Ubuntu:  sudo apt install ffmpeg"
    exit 1
fi

# Kiểm tra cổng 8765 có bị chiếm không
if command -v lsof &> /dev/null; then
    PID=$(lsof -ti:8765 2>/dev/null || true)
    if [ -n "$PID" ]; then
        echo "⚠️  Cổng 8765 đã bị PID $PID chiếm"
        echo "   Thực hiện: kill $PID rồi thử lại, hoặc đổi cổng"
        exit 1
    fi
fi

echo "✓ Kiểm tra môi trường thành công"
echo ""

VENV_DIR=".venv"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "[1/3] Đang tạo môi trường ảo..."
    python3 -m venv "$VENV_DIR"
fi

echo "[2/3] Đang cài đặt phụ thuộc..."
$VENV_PIP install -q -r requirements.txt

echo "[3/3] Đang khởi động dịch vụ..."
echo ""
echo "  Trình duyệt sẽ tự động mở http://localhost:8765"
echo "  Nhấn Ctrl+C để dừng dịch vụ"
echo ""

sleep 1

if command -v open &> /dev/null; then
    (sleep 1.5 && open http://localhost:8765) &
elif command -v xdg-open &> /dev/null; then
    (sleep 1.5 && xdg-open http://localhost:8765) &
fi

$VENV_PYTHON server.py
