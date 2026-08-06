"""Authentication, MongoDB session, and task ownership tests."""

from __future__ import annotations

import uuid

import pytest

from core.auth import AuthError, MongoAuthService, hash_password, verify_password
from core.auth_context import current_user


def test_password_hash_is_salted_and_verifiable():
    first = hash_password("StrongPass123")
    second = hash_password("StrongPass123")

    assert first != second
    assert verify_password("StrongPass123", first)
    assert not verify_password("WrongPass123", first)


def test_password_policy_rejects_weak_password():
    with pytest.raises(AuthError):
        hash_password("onlyletters")


@pytest.mark.asyncio
async def test_mongodb_accounts_sessions_and_roles():
    database_name = f"agnes_auth_test_{uuid.uuid4().hex}"
    service = MongoAuthService(
        uri="mongodb://127.0.0.1:27017/?serverSelectionTimeoutMS=5000",
        database_name=database_name,
    )
    try:
        try:
            await service.connect()
        except Exception as exc:
            pytest.skip(f"MongoDB is unavailable: {exc}")

        owner = await service.register("owner@example.com", "OwnerPass123", "Owner")
        member = await service.register("member@example.com", "MemberPass123", "Member")
        assert owner["role"] == "superadmin"
        assert member["role"] == "user"

        with pytest.raises(AuthError):
            await service.register("OWNER@example.com", "OtherPass123", "Duplicate")

        authenticated = await service.authenticate("owner@example.com", "OwnerPass123")
        assert authenticated and authenticated["id"] == owner["id"]
        assert await service.authenticate("owner@example.com", "WrongPass123") is None

        session = await service.create_session(member["id"])
        assert (await service.get_user_by_session(session))["id"] == member["id"]

        reset_request = await service.create_password_reset("member@example.com")
        assert reset_request is not None
        reset_user, reset_token = reset_request
        assert reset_user["id"] == member["id"]
        assert await service.create_password_reset("member@example.com") is None
        await service.reset_password(reset_token, "MemberNewPass123")
        assert await service.get_user_by_session(session) is None
        assert await service.authenticate("member@example.com", "MemberPass123") is None
        assert await service.authenticate("member@example.com", "MemberNewPass123")
        with pytest.raises(AuthError):
            await service.reset_password(reset_token, "AnotherPass123")

        session = await service.create_session(member["id"])

        promoted = await service.update_user(owner, member["id"], role="admin")
        assert promoted["role"] == "admin"
        with pytest.raises(AuthError):
            await service.update_user(owner, owner["id"], role="user")

        await service.update_user(owner, member["id"], status="disabled")
        assert await service.get_user_by_session(session) is None
        with pytest.raises(AuthError):
            await service.delete_user(promoted, owner["id"])
        with pytest.raises(AuthError):
            await service.delete_user(owner, owner["id"])
        deleted = await service.delete_user(owner, member["id"])
        assert deleted["id"] == member["id"]
        assert all(user["id"] != member["id"] for user in await service.list_users())
    finally:
        if service.client is not None:
            await service.client.drop_database(database_name)
        await service.close()


def test_task_manager_enforces_owner(monkeypatch, tmp_path):
    import core.task_manager as task_manager_module
    from core.task_manager import TaskManager
    from models.task import SimpleVideoTask, TaskType

    monkeypatch.setattr(task_manager_module, "get_working_dir", lambda: str(tmp_path))
    owner = {"id": "owner-id", "role": "user"}
    other = {"id": "other-id", "role": "user"}
    admin = {"id": "admin-id", "role": "admin"}

    token = current_user.set(owner)
    try:
        manager = TaskManager("owned-task")
        state = manager.create(
            SimpleVideoTask(task_type=TaskType.SIMPLE, prompt="owner test")
        )
        assert state.owner_id == owner["id"]
        assert manager.load() is not None
    finally:
        current_user.reset(token)

    token = current_user.set(other)
    try:
        assert TaskManager("owned-task").load() is None
        assert TaskManager("_").list_tasks() == []
    finally:
        current_user.reset(token)

    token = current_user.set(admin)
    try:
        assert TaskManager("owned-task").load() is not None
        assert len(TaskManager("_").list_tasks()) == 1
    finally:
        current_user.reset(token)
