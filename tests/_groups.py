"""Shared helpers for the group test modules.

Imported by ``tests/test_group_*.py``; the leading underscore keeps pytest from
collecting this file as a test module.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

GROUPS_URL = "/api/v1/groups"


def create_group(
    client: TestClient,
    headers: dict,
    *,
    name: str = "Hikers",
    visibility: str = "private",
    description: str | None = None,
) -> dict:
    payload: dict = {"name": name, "visibility": visibility}
    if description is not None:
        payload["description"] = description
    response = client.post(GROUPS_URL, json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def invite(
    client: TestClient,
    headers: dict,
    group_id: int,
    *,
    username: str | None = None,
    user_id: int | None = None,
) -> dict:
    payload: dict = {}
    if username is not None:
        payload["username"] = username
    if user_id is not None:
        payload["user_id"] = user_id
    response = client.post(
        f"{GROUPS_URL}/{group_id}/invitations", json=payload, headers=headers
    )
    return response


def accept(
    client: TestClient, headers: dict, group_id: int, invitation_id: int
) -> dict:
    response = client.post(
        f"{GROUPS_URL}/{group_id}/invitations/{invitation_id}/accept",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def decline(
    client: TestClient, headers: dict, group_id: int, invitation_id: int
) -> dict:
    response = client.post(
        f"{GROUPS_URL}/{group_id}/invitations/{invitation_id}/decline",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def add_member(
    client: TestClient,
    owner_headers: dict,
    member_headers: dict,
    group_id: int,
    *,
    username: str,
) -> dict:
    invitation = invite(
        client, owner_headers, group_id, username=username
    )
    assert invitation.status_code == 201, invitation.text
    return accept(client, member_headers, group_id, invitation.json()["id"])


def send_group_message(
    client: TestClient, headers: dict, group_id: int, body: str
) -> dict:
    response = client.post(
        f"{GROUPS_URL}/{group_id}/messages",
        json={"body": body},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def messages(client: TestClient, headers: dict, group_id: int, **params) -> dict:
    response = client.get(
        f"{GROUPS_URL}/{group_id}/messages", headers=headers, params=params
    )
    assert response.status_code == 200, response.text
    return response.json()
