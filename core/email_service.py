"""SMTP delivery for account recovery emails."""

from __future__ import annotations

import asyncio
import html
import os
import smtplib
import ssl
from email.message import EmailMessage


def _env_enabled(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def password_email_configured() -> bool:
    """Return whether all required password-email settings are present."""
    return bool(
        os.environ.get("SMTP_HOST", "").strip()
        and os.environ.get("SMTP_FROM", "").strip()
        and os.environ.get("PUBLIC_BASE_URL", "").strip()
    )


def public_base_url() -> str:
    """Return the trusted public application origin used in recovery links."""
    value = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not value.startswith(("http://", "https://")):
        raise RuntimeError("PUBLIC_BASE_URL chưa được cấu hình hợp lệ")
    return value


async def send_password_reset_email(
    recipient: str,
    display_name: str,
    reset_url: str,
) -> None:
    """Send a password-reset message without blocking the event loop."""
    await asyncio.to_thread(
        _send_password_reset_email_sync,
        recipient,
        display_name,
        reset_url,
    )


def _send_password_reset_email_sync(
    recipient: str,
    display_name: str,
    reset_url: str,
) -> None:
    host = os.environ.get("SMTP_HOST", "").strip()
    port = int(os.environ.get("SMTP_PORT", "587"))
    username = os.environ.get("SMTP_USERNAME", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("SMTP_FROM", "").strip()
    use_ssl = _env_enabled("SMTP_USE_SSL", False)
    use_tls = _env_enabled("SMTP_USE_TLS", not use_ssl)
    if not password_email_configured():
        raise RuntimeError("Email khôi phục mật khẩu chưa được cấu hình")

    safe_name = html.escape(display_name or "bạn")
    safe_url = html.escape(reset_url, quote=True)
    message = EmailMessage()
    message["Subject"] = "Đặt lại mật khẩu TOOL VIDEO"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        "Bạn đã yêu cầu đặt lại mật khẩu TOOL VIDEO.\n\n"
        f"Mở liên kết này trong vòng 30 phút: {reset_url}\n\n"
        "Nếu bạn không thực hiện yêu cầu này, hãy bỏ qua email."
    )
    message.add_alternative(
        f"""
        <html><body style="font-family:Arial,sans-serif;color:#172033">
          <h2>Đặt lại mật khẩu TOOL VIDEO</h2>
          <p>Xin chào {safe_name},</p>
          <p>Liên kết dưới đây chỉ dùng được một lần và hết hạn sau 30 phút.</p>
          <p><a href="{safe_url}" style="display:inline-block;padding:12px 18px;background:#b7f52a;color:#111;text-decoration:none;font-weight:700">Đặt lại mật khẩu</a></p>
          <p>Nếu bạn không thực hiện yêu cầu này, hãy bỏ qua email.</p>
        </body></html>
        """,
        subtype="html",
    )

    context = ssl.create_default_context()
    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=20, context=context) as smtp:
            if username:
                smtp.login(username, password)
            smtp.send_message(message)
        return

    with smtplib.SMTP(host, port, timeout=20) as smtp:
        smtp.ehlo()
        if use_tls:
            smtp.starttls(context=context)
            smtp.ehlo()
        if username:
            smtp.login(username, password)
        smtp.send_message(message)
