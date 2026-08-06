"""MongoDB-backed accounts, sessions, and role-based access control."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from bson import ObjectId
from pymongo import ASCENDING, AsyncMongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError


SESSION_COOKIE = "agnes_session"
SESSION_DAYS = 7
PASSWORD_RESET_MINUTES = 30
PASSWORD_RESET_COOLDOWN_SECONDS = 90
ROLES = {"superadmin", "admin", "user"}
USER_STATUSES = {"active", "disabled"}
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class AuthError(ValueError):
    """Raised for safe, user-facing authentication failures."""


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


def normalize_email(email: str) -> str:
    """Normalize and validate an account email address."""
    value = str(email or "").strip().casefold()
    if len(value) > 254 or not EMAIL_PATTERN.fullmatch(value):
        raise AuthError("Email không hợp lệ")
    return value


def validate_display_name(name: str) -> str:
    """Normalize and validate a display name."""
    value = re.sub(r"\s+", " ", str(name or "")).strip()
    if not 2 <= len(value) <= 80:
        raise AuthError("Tên hiển thị phải có từ 2 đến 80 ký tự")
    return value


def validate_password(password: str) -> str:
    """Apply the minimum password policy."""
    value = str(password or "")
    if len(value) < 8 or len(value) > 200:
        raise AuthError("Mật khẩu phải có từ 8 đến 200 ký tự")
    if not any(char.isalpha() for char in value) or not any(char.isdigit() for char in value):
        raise AuthError("Mật khẩu phải có cả chữ và số")
    return value


def hash_password(password: str) -> str:
    """Hash a password with scrypt and a unique random salt."""
    value = validate_password(password)
    salt = secrets.token_bytes(16)
    n, r, p = 2**14, 8, 1
    digest = hashlib.scrypt(value.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=64)
    return "$".join(
        (
            "scrypt",
            str(n),
            str(r),
            str(p),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    """Verify a password without leaking comparison timing."""
    try:
        algorithm, n, r, p, salt_text, digest_text = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.scrypt(
            str(password or "").encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def hash_session_token(token: str) -> str:
    """Return the irreversible identifier stored for a session cookie."""
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def hash_password_reset_token(token: str) -> str:
    """Return the irreversible identifier stored for a password-reset token."""
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def public_user(document: dict[str, Any]) -> dict[str, Any]:
    """Serialize an account without password or database internals."""
    def serialize_datetime(value: Any) -> Any:
        return value.isoformat() if isinstance(value, datetime) else value

    return {
        "id": str(document.get("_id") or document.get("id") or ""),
        "email": document.get("email", ""),
        "name": document.get("name", ""),
        "role": document.get("role", "user"),
        "status": document.get("status", "active"),
        "created_at": serialize_datetime(document.get("created_at")),
        "last_login_at": serialize_datetime(document.get("last_login_at")),
    }


class MongoAuthService:
    """Account and session repository using the official async PyMongo driver."""

    def __init__(self, uri: str = "", database_name: str = "") -> None:
        self.uri = uri or os.environ.get(
            "MONGODB_URI",
            "mongodb://127.0.0.1:27017/?serverSelectionTimeoutMS=5000",
        )
        self.database_name = database_name or os.environ.get("MONGODB_DB", "agnes_video")
        self.client: Optional[AsyncMongoClient] = None
        self.db = None
        self._registration_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Connect and create correctness/security indexes."""
        self.client = AsyncMongoClient(self.uri)
        await self.client.admin.command("ping")
        self.db = self.client[self.database_name]
        await self.db.users.create_index([("email", ASCENDING)], unique=True)
        await self.db.sessions.create_index([("token_hash", ASCENDING)], unique=True)
        await self.db.sessions.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0)
        await self.db.sessions.create_index([("user_id", ASCENDING)])
        await self.db.password_resets.create_index([("token_hash", ASCENDING)], unique=True)
        await self.db.password_resets.create_index(
            [("expires_at", ASCENDING)],
            expireAfterSeconds=0,
        )
        await self.db.password_resets.create_index([("user_id", ASCENDING)])

    async def close(self) -> None:
        """Close the database client."""
        if self.client is not None:
            await self.client.close()
        self.client = None
        self.db = None

    def _require_db(self):
        if self.db is None:
            raise RuntimeError("MongoDB authentication service is not connected")
        return self.db

    async def registration_open(self) -> bool:
        """Return whether public account registration is enabled."""
        value = os.environ.get("ALLOW_REGISTRATION", "true").strip().lower()
        return value not in {"0", "false", "no", "off"}

    async def register(self, email: str, password: str, name: str) -> dict[str, Any]:
        """Create an account; the first account becomes superadmin."""
        db = self._require_db()
        if not await self.registration_open():
            raise AuthError("Đăng ký tài khoản đang tạm khóa")
        clean_email = normalize_email(email)
        clean_name = validate_display_name(name)
        password_hash = hash_password(password)
        async with self._registration_lock:
            first_account = await db.users.count_documents({}, limit=1) == 0
            configured_admin = os.environ.get("ADMIN_EMAIL", "").strip().casefold()
            role = "superadmin" if first_account or clean_email == configured_admin else "user"
            now = utc_now()
            document = {
                "email": clean_email,
                "name": clean_name,
                "password_hash": password_hash,
                "role": role,
                "status": "active",
                "created_at": now,
                "updated_at": now,
                "last_login_at": None,
            }
            try:
                result = await db.users.insert_one(document)
            except DuplicateKeyError as exc:
                raise AuthError("Email này đã được đăng ký") from exc
        document["_id"] = result.inserted_id
        return public_user(document)

    async def authenticate(self, email: str, password: str) -> Optional[dict[str, Any]]:
        """Validate credentials and return the active public account."""
        db = self._require_db()
        try:
            clean_email = normalize_email(email)
        except AuthError:
            return None
        document = await db.users.find_one({"email": clean_email})
        if not document or not verify_password(password, document.get("password_hash", "")):
            return None
        if document.get("status") != "active":
            raise AuthError("Tài khoản đã bị khóa")
        now = utc_now()
        await db.users.update_one(
            {"_id": document["_id"]},
            {"$set": {"last_login_at": now, "updated_at": now}},
        )
        document["last_login_at"] = now
        return public_user(document)

    async def create_session(self, user_id: str) -> str:
        """Create a revocable opaque session and return its raw cookie token."""
        db = self._require_db()
        token = secrets.token_urlsafe(48)
        now = utc_now()
        await db.sessions.insert_one(
            {
                "token_hash": hash_session_token(token),
                "user_id": ObjectId(user_id),
                "created_at": now,
                "expires_at": now + timedelta(days=SESSION_DAYS),
            }
        )
        return token

    async def delete_session(self, token: str) -> None:
        """Revoke one session token."""
        if not token:
            return
        db = self._require_db()
        await db.sessions.delete_one({"token_hash": hash_session_token(token)})

    async def get_user_by_session(self, token: str) -> Optional[dict[str, Any]]:
        """Resolve an unexpired session to an active account."""
        if not token:
            return None
        db = self._require_db()
        session = await db.sessions.find_one(
            {
                "token_hash": hash_session_token(token),
                "expires_at": {"$gt": utc_now()},
            }
        )
        if not session:
            return None
        user = await db.users.find_one({"_id": session["user_id"], "status": "active"})
        return public_user(user) if user else None

    async def create_password_reset(
        self,
        email: str,
    ) -> Optional[tuple[dict[str, Any], str]]:
        """Create a short-lived one-time reset token for an active account."""
        db = self._require_db()
        try:
            clean_email = normalize_email(email)
        except AuthError:
            return None
        user = await db.users.find_one({"email": clean_email, "status": "active"})
        if not user:
            return None

        now = utc_now()
        recent = await db.password_resets.find_one(
            {
                "user_id": user["_id"],
                "created_at": {
                    "$gt": now - timedelta(seconds=PASSWORD_RESET_COOLDOWN_SECONDS)
                },
                "used_at": None,
                "expires_at": {"$gt": now},
            }
        )
        if recent:
            return None

        token = secrets.token_urlsafe(48)
        await db.password_resets.delete_many({"user_id": user["_id"]})
        await db.password_resets.insert_one(
            {
                "token_hash": hash_password_reset_token(token),
                "user_id": user["_id"],
                "created_at": now,
                "expires_at": now + timedelta(minutes=PASSWORD_RESET_MINUTES),
                "used_at": None,
            }
        )
        return public_user(user), token

    async def revoke_password_reset(self, token: str) -> None:
        """Delete a reset token when email delivery fails."""
        if not token:
            return
        await self._require_db().password_resets.delete_one(
            {"token_hash": hash_password_reset_token(token)}
        )

    async def reset_password(self, token: str, new_password: str) -> dict[str, Any]:
        """Consume a valid reset token, update the password, and revoke sessions."""
        db = self._require_db()
        raw_token = str(token or "").strip()
        if len(raw_token) < 32:
            raise AuthError("Liên kết đặt lại mật khẩu không hợp lệ hoặc đã hết hạn")
        password_hash = hash_password(new_password)
        now = utc_now()
        reset = await db.password_resets.find_one_and_update(
            {
                "token_hash": hash_password_reset_token(raw_token),
                "used_at": None,
                "expires_at": {"$gt": now},
            },
            {"$set": {"used_at": now}},
            return_document=ReturnDocument.BEFORE,
        )
        if not reset:
            raise AuthError("Liên kết đặt lại mật khẩu không hợp lệ hoặc đã hết hạn")

        user = await db.users.find_one({"_id": reset["user_id"], "status": "active"})
        if not user:
            raise AuthError("Tài khoản không tồn tại hoặc đã bị khóa")
        await db.users.update_one(
            {"_id": user["_id"]},
            {"$set": {"password_hash": password_hash, "updated_at": now}},
        )
        await db.sessions.delete_many({"user_id": user["_id"]})
        await db.password_resets.delete_many({"user_id": user["_id"]})
        return public_user(user)

    async def list_users(self) -> list[dict[str, Any]]:
        """Return all accounts for the admin console."""
        db = self._require_db()
        users: list[dict[str, Any]] = []
        async for document in db.users.find({}).sort("created_at", ASCENDING):
            users.append(public_user(document))
        return users

    async def update_user(
        self,
        actor: dict[str, Any],
        user_id: str,
        *,
        role: str = "",
        status: str = "",
    ) -> dict[str, Any]:
        """Update an account while preserving superadmin invariants."""
        db = self._require_db()
        try:
            target_id = ObjectId(user_id)
        except Exception as exc:
            raise AuthError("Tài khoản không hợp lệ") from exc
        target = await db.users.find_one({"_id": target_id})
        if not target:
            raise AuthError("Không tìm thấy tài khoản")
        target_public = public_user(target)
        if target_public["id"] == actor.get("id") and status == "disabled":
            raise AuthError("Bạn không thể tự khóa tài khoản đang đăng nhập")
        if target.get("role") == "superadmin" and actor.get("id") != target_public["id"]:
            raise AuthError("Không thể thay đổi tài khoản superadmin khác")
        if actor.get("role") != "superadmin" and (
            target.get("role") != "user" or (role and role != target.get("role"))
        ):
            raise AuthError("Chỉ superadmin được thay đổi vai trò quản trị")

        if (
            target.get("role") == "superadmin"
            and role
            and role != "superadmin"
            and await db.users.count_documents({"role": "superadmin"}, limit=2) <= 1
        ):
            raise AuthError("Hệ thống phải còn ít nhất một superadmin")

        changes: dict[str, Any] = {"updated_at": utc_now()}
        if role:
            if role not in ROLES:
                raise AuthError("Vai trò không hợp lệ")
            if role == "superadmin" and actor.get("role") != "superadmin":
                raise AuthError("Không đủ quyền cấp superadmin")
            changes["role"] = role
        if status:
            if status not in USER_STATUSES:
                raise AuthError("Trạng thái tài khoản không hợp lệ")
            changes["status"] = status
        await db.users.update_one({"_id": target_id}, {"$set": changes})
        if changes.get("status") == "disabled":
            await db.sessions.delete_many({"user_id": target_id})
        updated = await db.users.find_one({"_id": target_id})
        return public_user(updated)

    async def delete_user(
        self,
        actor: dict[str, Any],
        user_id: str,
    ) -> dict[str, Any]:
        """Delete a non-superadmin account and revoke all authentication state."""
        db = self._require_db()
        if actor.get("role") != "superadmin":
            raise AuthError("Chỉ superadmin được xóa tài khoản")
        try:
            target_id = ObjectId(user_id)
        except Exception as exc:
            raise AuthError("Tài khoản không hợp lệ") from exc
        target = await db.users.find_one({"_id": target_id})
        if not target:
            raise AuthError("Không tìm thấy tài khoản")
        target_public = public_user(target)
        if target_public["id"] == actor.get("id"):
            raise AuthError("Bạn không thể tự xóa tài khoản đang đăng nhập")
        if target.get("role") == "superadmin":
            raise AuthError("Không thể xóa tài khoản superadmin")

        await db.sessions.delete_many({"user_id": target_id})
        await db.password_resets.delete_many({"user_id": target_id})
        await db.users.delete_one({"_id": target_id})
        return target_public

    async def count_users(self) -> int:
        """Return the account count for setup and admin stats."""
        return await self._require_db().users.count_documents({})

    async def health(self) -> bool:
        """Return whether MongoDB responds to a ping."""
        try:
            if self.client is None:
                return False
            await self.client.admin.command("ping")
            return True
        except PyMongoError:
            return False
