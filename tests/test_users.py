from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_admin_can_manage_users(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))

    current = client.get("/api/v1/users/current").json()
    assert current["id"] == "root"
    assert current["role"] == "admin"

    created_response = client.post(
        "/api/v1/users",
        json={
            "name": "张三",
            "phone": "13800000001",
            "email": "zhangsan@example.com",
            "role": "user",
            "department": "交付部",
            "position": "交付经理",
            "remark": "负责客户交付确认",
        },
    )

    assert created_response.status_code == 201
    created = created_response.json()
    assert created["id"].startswith("user_")
    assert created["name"] == "张三"
    assert created["phone"] == "13800000001"
    assert created["email"] == "zhangsan@example.com"
    assert created["role"] == "user"
    assert created["status"] == "active"

    updated_response = client.put(
        f"/api/v1/users/{created['id']}",
        json={
            "phone": "13800000002",
            "position": "高级交付经理",
            "status": "disabled",
        },
    )

    assert updated_response.status_code == 200
    updated = updated_response.json()
    assert updated["phone"] == "13800000002"
    assert updated["position"] == "高级交付经理"
    assert updated["status"] == "disabled"

    users = client.get("/api/v1/users").json()
    assert any(user["id"] == created["id"] for user in users)

    delete_response = client.delete(f"/api/v1/users/{created['id']}")
    assert delete_response.status_code == 204
    assert all(user["id"] != created["id"] for user in client.get("/api/v1/users").json())


def test_normal_user_cannot_manage_full_user_list(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    user = client.post(
        "/api/v1/users",
        json={
            "name": "李四",
            "phone": "13800000003",
            "email": "lisi@example.com",
            "role": "user",
        },
    ).json()
    headers = {"X-User-Id": user["id"]}

    assert client.get("/api/v1/users", headers=headers).status_code == 403
    assert client.post(
        "/api/v1/users",
        headers=headers,
        json={"name": "王五", "role": "user"},
    ).status_code == 403
    assert client.put(
        f"/api/v1/users/{user['id']}",
        headers=headers,
        json={"name": "李四-修改"},
    ).status_code == 403
    assert client.delete(f"/api/v1/users/{user['id']}", headers=headers).status_code == 403


def test_assignable_users_returns_names_for_workflow_assignment(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    active_user = client.post(
        "/api/v1/users",
        json={
            "name": "赵六",
            "phone": "13800000004",
            "email": "zhaoliu@example.com",
            "role": "user",
            "department": "运营部",
        },
    ).json()
    disabled_user = client.post(
        "/api/v1/users",
        json={
            "name": "停用用户",
            "role": "user",
            "status": "disabled",
        },
    ).json()

    response = client.get("/api/v1/users/assignable", headers={"X-User-Id": active_user["id"]})

    assert response.status_code == 200
    assignable = response.json()
    assert {"id": active_user["id"], "name": "赵六", "role": "user"} in assignable
    assert all(user["id"] != disabled_user["id"] for user in assignable)
