"""Group message history, per-member receipts, access control and media."""

from __future__ import annotations

from sqlalchemy import select

from app.core.security import MESSAGE_MAX_LENGTH
from app.db.models import Message, MessageReceipt
from tests._groups import (
    GROUPS_URL,
    add_member,
    create_group,
    messages,
    send_group_message,
)
from tests._media import upload_one


def _group_with_members(client, api):
    _, alice = api.create_user("alice@example.com", username="alice")
    _, bob = api.create_user("bob@example.com", username="bob")
    _, carol = api.create_user("carol@example.com", username="carol")
    group = create_group(client, alice, name="Receipt Group")
    add_member(client, alice, bob, group["id"], username="bob")
    add_member(client, alice, carol, group["id"], username="carol")
    return group, alice, bob, carol


def test_group_message_send_history_and_unread_counts(client, api) -> None:
    group, alice, bob, carol = _group_with_members(client, api)

    sent = send_group_message(client, alice, group["id"], "hello group")
    assert sent["conversation_id"] == group["id"]
    assert sent["sender_username"] == "alice"
    assert sent["body"] == "hello group"
    assert sent["media"] is None
    assert sent["member_count"] == 3
    assert sent["read_count"] == 0
    assert sent["delivered_count"] == 0
    assert sent["read_by_me"] is True
    assert sent["delivered_to_me"] is True

    bob_history = messages(client, bob, group["id"])
    assert bob_history["total"] == 1
    item = bob_history["items"][0]
    assert item["id"] == sent["id"]
    assert item["read_by_me"] is False
    assert item["delivered_to_me"] is False
    assert item["read_count"] == 0

    # Bob has one unread; Alice's own message is never unread for Alice.
    bob_groups = client.get(GROUPS_URL, headers=bob).json()
    assert bob_groups["items"][0]["unread_count"] == 1
    alice_groups = client.get(GROUPS_URL, headers=alice).json()
    assert alice_groups["items"][0]["unread_count"] == 0

    receipt = client.post(
        f"{GROUPS_URL}/{group['id']}/read",
        json={"last_message_id": sent["id"]},
        headers=bob,
    )
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["updated"] == 1
    assert receipt.json()["unread_count"] == 0
    assert receipt.json()["read_at"] is not None

    bob_history = messages(client, bob, group["id"])
    assert bob_history["items"][0]["read_by_me"] is True
    assert bob_history["items"][0]["delivered_to_me"] is True

    # One member reading does not mark the message read for Carol.
    carol_history = messages(client, carol, group["id"])
    assert carol_history["items"][0]["read_by_me"] is False
    assert carol_history["items"][0]["read_count"] == 1
    carol_groups = client.get(GROUPS_URL, headers=carol).json()
    assert carol_groups["items"][0]["unread_count"] == 1

    alice_history = messages(client, alice, group["id"])
    assert alice_history["items"][0]["read_count"] == 1
    assert alice_history["items"][0]["delivered_count"] == 1
    assert alice_history["items"][0]["read_by_me"] is True

    # A second read call is idempotent.
    again = client.post(
        f"{GROUPS_URL}/{group['id']}/read",
        json={"last_message_id": sent["id"]},
        headers=bob,
    )
    assert again.status_code == 200
    assert again.json()["updated"] == 0
    assert again.json()["read_at"] is None


def test_group_read_state_is_stored_per_member(client, api, db_session) -> None:
    group, alice, bob, carol = _group_with_members(client, api)
    sent = send_group_message(client, alice, group["id"], "receipt me")

    response = client.post(
        f"{GROUPS_URL}/{group['id']}/read",
        json={"last_message_id": sent["id"]},
        headers=bob,
    )
    assert response.status_code == 200

    receipts = db_session.execute(
        select(MessageReceipt).where(MessageReceipt.message_id == sent["id"])
    ).scalars().all()
    assert len(receipts) == 1
    assert receipts[0].read_at is not None
    assert receipts[0].delivered_at is not None

    # The direct-chat aggregate columns are not used for groups.
    message = db_session.get(Message, sent["id"])
    assert message.delivered_at is None
    assert message.read_at is None


def test_group_message_access_validation_and_cross_group_reads(client, api) -> None:
    _, alice = api.create_user("alice@example.com", username="alice")
    _, bob = api.create_user("bob@example.com", username="bob")
    _, outsider = api.create_user("outsider@example.com", username="outsider")
    group = create_group(client, alice)
    other = create_group(client, alice, name="Other Group")
    add_member(client, alice, bob, group["id"], username="bob")

    sent = send_group_message(client, alice, group["id"], "members only")

    assert (
        client.get(f"{GROUPS_URL}/{group['id']}/messages", headers=outsider).status_code
        == 404
    )
    assert (
        client.post(
            f"{GROUPS_URL}/{group['id']}/messages",
            json={"body": "not a member"},
            headers=outsider,
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{GROUPS_URL}/{group['id']}/read",
            json={"last_message_id": sent["id"]},
            headers=outsider,
        ).status_code
        == 404
    )

    for body in ("", "   ", "x" * (MESSAGE_MAX_LENGTH + 1)):
        response = client.post(
            f"{GROUPS_URL}/{group['id']}/messages",
            json={"body": body},
            headers=alice,
        )
        assert response.status_code == 422, (body[:10], response.text)

    # A message id from another group cannot be used as a read watermark.
    other_sent = send_group_message(client, alice, other["id"], "other")
    cross_read = client.post(
        f"{GROUPS_URL}/{group['id']}/read",
        json={"last_message_id": other_sent["id"]},
        headers=bob,
    )
    assert cross_read.status_code == 404

    # Reading a message from a group the caller has left is also rejected.
    assert client.post(f"{GROUPS_URL}/{group['id']}/leave", headers=bob).status_code == 204
    assert (
        client.post(
            f"{GROUPS_URL}/{group['id']}/read",
            json={"last_message_id": sent["id"]},
            headers=bob,
        ).status_code
        == 404
    )


def test_group_message_pagination_and_media(client, api) -> None:
    _, alice = api.create_user("alice@example.com", username="alice")
    _, bob = api.create_user("bob@example.com", username="bob")
    group = create_group(client, alice)
    add_member(client, alice, bob, group["id"], username="bob")

    media = upload_one(client, alice, group["id"])
    response = client.post(
        f"{GROUPS_URL}/{group['id']}/messages",
        json={"body": "  with a photo  ", "media_id": media["id"]},
        headers=alice,
    )
    assert response.status_code == 201, response.text
    message = response.json()
    assert message["body"] == "with a photo"
    assert message["media"]["id"] == media["id"]
    assert message["media"]["message_id"] == message["id"]

    history = messages(client, bob, group["id"])
    assert history["items"][0]["media"]["id"] == media["id"]

    for index in range(3):
        send_group_message(client, alice, group["id"], f"message {index}")

    page = messages(client, alice, group["id"], limit=2, offset=0)
    assert page["total"] == 4
    assert len(page["items"]) == 2
    page2 = messages(client, alice, group["id"], limit=2, offset=2)
    assert len(page2["items"]) == 2
    assert {item["id"] for item in page["items"]} & {
        item["id"] for item in page2["items"]
    } == set()

    # Media from another conversation cannot be attached to a group message.
    other = create_group(client, alice, name="Media Other")
    foreign_media = upload_one(client, alice, other["id"])
    foreign = client.post(
        f"{GROUPS_URL}/{group['id']}/messages",
        json={"media_id": foreign_media["id"]},
        headers=alice,
    )
    assert foreign.status_code == 404


def test_group_message_list_is_member_only_for_public_groups(client, api) -> None:
    _, alice = api.create_user("alice@example.com", username="alice")
    _, bob = api.create_user("bob@example.com", username="bob")
    group = create_group(client, alice, name="Public Chat", visibility="public")

    assert client.get(f"{GROUPS_URL}/{group['id']}", headers=bob).status_code == 200
    assert (
        client.get(f"{GROUPS_URL}/{group['id']}/messages", headers=bob).status_code
        == 404
    )

    joined = client.post(f"{GROUPS_URL}/{group['id']}/join", headers=bob)
    assert joined.status_code == 201
    assert (
        client.get(f"{GROUPS_URL}/{group['id']}/messages", headers=bob).status_code
        == 200
    )
