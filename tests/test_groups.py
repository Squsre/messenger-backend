"""Group creation, discovery, membership, roles and invitation tests."""

from __future__ import annotations

from sqlalchemy import func, select

from app.db.models import ConversationMember
from tests._groups import (
    GROUPS_URL,
    accept,
    add_member,
    create_group,
    decline,
    invite,
    messages,
)


def test_create_group_sets_owner_and_appears_in_my_list(client, api) -> None:
    _, alice = api.create_user("alice@example.com", username="alice")

    group = create_group(
        client,
        alice,
        name="Weekend Hikers",
        description="Saturday morning walks.",
    )

    assert group["id"] > 0
    assert group["name"] == "Weekend Hikers"
    assert group["description"] == "Saturday morning walks."
    assert group["visibility"] == "private"
    assert group["owner_username"] == "alice"
    assert group["member_count"] == 1
    assert group["is_member"] is True
    assert group["role"] == "owner"
    assert group["unread_count"] == 0
    assert group["last_message"] is None

    listed = client.get(GROUPS_URL, headers=alice)
    assert listed.status_code == 200, listed.text
    page = listed.json()
    assert page["total"] == 1
    assert page["items"][0]["id"] == group["id"]
    assert page["items"][0]["role"] == "owner"

    detail = client.get(f"{GROUPS_URL}/{group['id']}", headers=alice)
    assert detail.status_code == 200, detail.text
    assert detail.json()["name"] == "Weekend Hikers"

    members = client.get(f"{GROUPS_URL}/{group['id']}/members", headers=alice)
    assert members.status_code == 200, members.text
    assert members.json()["total"] == 1
    assert members.json()["items"][0]["username"] == "alice"
    assert members.json()["items"][0]["role"] == "owner"


def test_group_input_is_validated(client, api) -> None:
    _, alice = api.create_user("alice@example.com", username="alice")

    for payload in (
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 65},
        {"name": "ok", "description": "x" * 281},
        {"name": "ok", "visibility": "secret"},
        {"name": "ok", "unexpected": True},
    ):
        response = client.post(GROUPS_URL, json=payload, headers=alice)
        assert response.status_code == 422, (payload, response.text)

    group = create_group(client, alice)
    empty_patch = client.patch(f"{GROUPS_URL}/{group['id']}", json={}, headers=alice)
    assert empty_patch.status_code == 422
    null_visibility = client.patch(
        f"{GROUPS_URL}/{group['id']}", json={"visibility": None}, headers=alice
    )
    assert null_visibility.status_code == 422


def test_public_groups_are_searchable_and_private_groups_are_hidden(client, api) -> None:
    _, alice = api.create_user("alice@example.com", username="alice")
    _, bob = api.create_user("bob@example.com", username="bob")

    public = create_group(
        client, alice, name="Public Hikers", visibility="public"
    )
    private = create_group(
        client, alice, name="Private Hikers", visibility="private"
    )

    search = client.get(f"{GROUPS_URL}/search", params={"q": "hikers"}, headers=bob)
    assert search.status_code == 200, search.text
    found = search.json()
    assert found["total"] == 1
    assert [item["id"] for item in found["items"]] == [public["id"]]
    assert found["items"][0]["is_member"] is False
    assert found["items"][0]["role"] is None
    assert found["items"][0]["last_message"] is None

    hidden = client.get(
        f"{GROUPS_URL}/search", params={"q": "private"}, headers=bob
    )
    assert hidden.status_code == 200
    assert hidden.json()["items"] == []

    public_detail = client.get(f"{GROUPS_URL}/{public['id']}", headers=bob)
    assert public_detail.status_code == 200, public_detail.text
    assert public_detail.json()["visibility"] == "public"
    assert public_detail.json()["is_member"] is False
    assert public_detail.json()["last_message"] is None

    public_members = client.get(
        f"{GROUPS_URL}/{public['id']}/members", headers=bob
    )
    assert public_members.status_code == 200, public_members.text
    assert public_members.json()["total"] == 1

    # Public profile, private content: messages still need membership.
    assert (
        client.get(f"{GROUPS_URL}/{public['id']}/messages", headers=bob).status_code
        == 404
    )

    private_detail = client.get(f"{GROUPS_URL}/{private['id']}", headers=bob)
    assert private_detail.status_code == 404
    assert (
        client.get(f"{GROUPS_URL}/{private['id']}/members", headers=bob).status_code
        == 404
    )
    assert (
        client.get(f"{GROUPS_URL}/{private['id']}/messages", headers=bob).status_code
        == 404
    )
    assert (
        client.post(f"{GROUPS_URL}/{private['id']}/join", headers=bob).status_code
        == 403
    )


def test_public_group_join_is_idempotent_and_does_not_duplicate_membership(
    client, api, db_session
) -> None:
    _, alice = api.create_user("alice@example.com", username="alice")
    bob_account, bob = api.create_user("bob@example.com", username="bob")
    group = create_group(client, alice, name="Open Group", visibility="public")

    first = client.post(f"{GROUPS_URL}/{group['id']}/join", headers=bob)
    assert first.status_code == 201, first.text
    assert first.json()["role"] == "member"
    assert first.json()["member_count"] == 2

    second = client.post(f"{GROUPS_URL}/{group['id']}/join", headers=bob)
    assert second.status_code == 200, second.text
    assert second.json()["member_count"] == 2

    count = db_session.execute(
        select(func.count())
        .select_from(ConversationMember)
        .where(
            ConversationMember.conversation_id == group["id"],
            ConversationMember.user_id == bob_account["id"],
        )
    ).scalar_one()
    assert count == 1

    members = client.get(f"{GROUPS_URL}/{group['id']}/members", headers=bob)
    assert members.status_code == 200
    assert members.json()["total"] == 2


def test_invitation_accept_decline_revoke_and_reinvite(client, api) -> None:
    _, alice = api.create_user("alice@example.com", username="alice")
    bob_account, bob = api.create_user("bob@example.com", username="bob")
    charlie_account, charlie = api.create_user(
        "charlie@example.com", username="charlie"
    )
    group = create_group(client, alice)

    pending = invite(client, alice, group["id"], username="bob")
    assert pending.status_code == 201, pending.text
    invitation = pending.json()
    assert invitation["status"] == "pending"
    assert invitation["group_id"] == group["id"]
    assert invitation["inviter_username"] == "alice"
    assert invitation["invitee_username"] == "bob"

    duplicate = invite(client, alice, group["id"], username="bob")
    assert duplicate.status_code == 409

    mine = client.get(f"{GROUPS_URL}/invitations", headers=bob)
    assert mine.status_code == 200, mine.text
    assert mine.json()["total"] == 1
    assert mine.json()["items"][0]["id"] == invitation["id"]

    # Only the invitee may accept, and only through the matching group id.
    assert (
        client.post(
            f"{GROUPS_URL}/{group['id']}/invitations/{invitation['id']}/accept",
            headers=charlie,
        ).status_code
        == 404
    )
    accepted = accept(client, bob, group["id"], invitation["id"])
    assert accepted["status"] == "accepted"
    assert accepted["responded_at"] is not None

    # A repeated accept after membership is idempotent and does not duplicate
    # the member or change the accepted status.
    repeated = client.post(
        f"{GROUPS_URL}/{group['id']}/invitations/{invitation['id']}/accept",
        headers=bob,
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["status"] == "accepted"
    members_after_repeat = client.get(
        f"{GROUPS_URL}/{group['id']}/members", headers=bob
    )
    assert members_after_repeat.json()["total"] == 2

    # Bob is now a member and can read the group.
    assert client.get(f"{GROUPS_URL}/{group['id']}", headers=bob).status_code == 200
    assert (
        client.get(f"{GROUPS_URL}/{group['id']}/messages", headers=bob).status_code
        == 200
    )

    # A declined invitation may be reissued.
    first = invite(client, alice, group["id"], username="charlie")
    assert first.status_code == 201
    declined = decline(client, charlie, group["id"], first.json()["id"])
    assert declined["status"] == "declined"
    assert (
        client.get(f"{GROUPS_URL}/{group['id']}", headers=charlie).status_code
        == 404
    )

    reissued = invite(client, alice, group["id"], username="charlie")
    assert reissued.status_code == 201
    assert reissued.json()["status"] == "pending"

    revoked = client.post(
        f"{GROUPS_URL}/{group['id']}/invitations/{reissued.json()['id']}/revoke",
        headers=alice,
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "revoked"
    assert (
        client.get(f"{GROUPS_URL}/{group['id']}", headers=charlie).status_code
        == 404
    )

    # A revoked invitation may also be reissued.
    again = invite(client, alice, group["id"], username="charlie")
    assert again.status_code == 201

    # The status filter and DELETE form work too.
    pending_list = client.get(
        f"{GROUPS_URL}/invitations",
        params={"status": "pending"},
        headers=charlie,
    )
    assert pending_list.status_code == 200
    assert pending_list.json()["total"] == 1
    deleted = client.delete(
        f"{GROUPS_URL}/{group['id']}/invitations/{again.json()['id']}",
        headers=alice,
    )
    assert deleted.status_code == 204
    assert client.get(
        f"{GROUPS_URL}/{group['id']}", headers=charlie
    ).status_code == 404


def test_invitation_rejects_self_unknown_blocked_and_non_managers(client, api) -> None:
    _, alice = api.create_user("alice@example.com", username="alice")
    _, bob = api.create_user("bob@example.com", username="bob")
    _, charlie = api.create_user("charlie@example.com", username="charlie")
    group = create_group(client, alice)
    add_member(client, alice, bob, group["id"], username="bob")

    assert invite(client, alice, group["id"], username="alice").status_code == 400
    assert invite(client, alice, group["id"], username="ghost").status_code == 404
    assert invite(client, alice, group["id"], username="bob").status_code == 409

    # A normal member cannot invite or manage invitations.
    assert invite(client, bob, group["id"], username="charlie").status_code == 403
    assert (
        client.get(f"{GROUPS_URL}/{group['id']}/invitations", headers=bob).status_code
        == 403
    )

    # A block prevents the invitation; removing the block allows it again.
    blocked = client.post(
        "/api/v1/blocks", json={"username": "charlie"}, headers=alice
    )
    assert blocked.status_code == 201, blocked.text
    assert invite(client, alice, group["id"], username="charlie").status_code == 403
    assert (
        client.delete("/api/v1/blocks/charlie", headers=alice).status_code == 204
    )
    assert invite(client, alice, group["id"], username="charlie").status_code == 201

    # Managers can page through their group's invitations.
    listed = client.get(
        f"{GROUPS_URL}/{group['id']}/invitations", headers=alice
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 2
    assert {item["status"] for item in listed.json()["items"]} == {"accepted", "pending"}


def test_owner_and_admin_permissions(client, api) -> None:
    alice_account, alice = api.create_user("alice@example.com", username="alice")
    bob_account, bob = api.create_user("bob@example.com", username="bob")
    carol_account, carol = api.create_user("carol@example.com", username="carol")
    dave_account, dave = api.create_user("dave@example.com", username="dave")
    group = create_group(client, alice)
    add_member(client, alice, bob, group["id"], username="bob")
    add_member(client, alice, carol, group["id"], username="carol")
    add_member(client, alice, dave, group["id"], username="dave")

    self_role = client.patch(
        f"{GROUPS_URL}/{group['id']}/members/{alice_account['id']}",
        json={"role": "admin"},
        headers=alice,
    )
    assert self_role.status_code == 409

    promoted = client.patch(
        f"{GROUPS_URL}/{group['id']}/members/{bob_account['id']}",
        json={"role": "admin"},
        headers=alice,
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["role"] == "admin"

    # An admin can remove ordinary members.
    assert (
        client.delete(
            f"{GROUPS_URL}/{group['id']}/members/{carol_account['id']}",
            headers=bob,
        ).status_code
        == 204
    )

    # But not another admin, the owner, or roles.
    client.patch(
        f"{GROUPS_URL}/{group['id']}/members/{dave_account['id']}",
        json={"role": "admin"},
        headers=alice,
    )
    assert (
        client.delete(
            f"{GROUPS_URL}/{group['id']}/members/{dave_account['id']}",
            headers=bob,
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"{GROUPS_URL}/{group['id']}/members/{999999}",
            headers=bob,
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"{GROUPS_URL}/{group['id']}/members/{alice_account['id']}",
            headers=bob,
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"{GROUPS_URL}/{group['id']}/members/{dave_account['id']}",
            json={"role": "member"},
            headers=bob,
        ).status_code
        == 403
    )

    # The owner may demote an admin.
    demoted = client.patch(
        f"{GROUPS_URL}/{group['id']}/members/{bob_account['id']}",
        json={"role": "member"},
        headers=alice,
    )
    assert demoted.status_code == 200
    assert demoted.json()["role"] == "member"
    assert invite(client, bob, group["id"], username="carol").status_code == 403

    # Admin may edit name/description, but not visibility.
    client.patch(
        f"{GROUPS_URL}/{group['id']}/members/{dave_account['id']}",
        json={"role": "admin"},
        headers=alice,
    )
    renamed = client.patch(
        f"{GROUPS_URL}/{group['id']}",
        json={"name": "Renamed by admin"},
        headers=dave,
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Renamed by admin"
    assert (
        client.patch(
            f"{GROUPS_URL}/{group['id']}",
            json={"visibility": "public"},
            headers=dave,
        ).status_code
        == 403
    )


def test_owner_cannot_leave_without_transfer_and_transfer_works(client, api) -> None:
    alice_account, alice = api.create_user("alice@example.com", username="alice")
    bob_account, bob = api.create_user("bob@example.com", username="bob")
    carol_account, carol = api.create_user("carol@example.com", username="carol")
    group = create_group(client, alice)
    add_member(client, alice, bob, group["id"], username="bob")

    assert client.post(f"{GROUPS_URL}/{group['id']}/leave", headers=alice).status_code == 409
    assert (
        client.post(
            f"{GROUPS_URL}/{group['id']}/transfer-owner",
            json={"username": "alice"},
            headers=alice,
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"{GROUPS_URL}/{group['id']}/transfer-owner",
            json={"username": "carol"},
            headers=alice,
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{GROUPS_URL}/{group['id']}/transfer-owner",
            json={"username": "bob"},
            headers=carol,
        ).status_code
        == 404
    )

    transferred = client.post(
        f"{GROUPS_URL}/{group['id']}/transfer-owner",
        json={"username": "bob"},
        headers=alice,
    )
    assert transferred.status_code == 200, transferred.text
    assert transferred.json()["owner_username"] == "bob"
    assert transferred.json()["role"] == "admin"

    # The old owner is now an admin and can leave; the new owner cannot.
    assert client.post(f"{GROUPS_URL}/{group['id']}/leave", headers=alice).status_code == 204
    assert client.post(f"{GROUPS_URL}/{group['id']}/leave", headers=bob).status_code == 409
    assert (
        client.post(
            f"{GROUPS_URL}/{group['id']}/transfer-owner",
            json={"user_id": alice_account["id"]},
            headers=bob,
        ).status_code
        == 404
    )

    # New owner can transfer to a current member (carol joined by invitation).
    add_member(client, bob, carol, group["id"], username="carol")
    transferred_again = client.post(
        f"{GROUPS_URL}/{group['id']}/transfer-owner",
        json={"user_id": carol_account["id"]},
        headers=bob,
    )
    assert transferred_again.status_code == 200, transferred_again.text
    assert transferred_again.json()["owner_username"] == "carol"


def test_group_and_direct_ids_do_not_cross_endpoints(client, api) -> None:
    _, alice = api.create_user("alice@example.com", username="alice")
    _, bob = api.create_user("bob@example.com", username="bob")
    group = create_group(client, alice)

    direct = client.post(
        "/api/v1/conversations", json={"username": "bob"}, headers=alice
    )
    assert direct.status_code in (200, 201), direct.text
    direct_id = direct.json()["id"]

    assert client.get(f"{GROUPS_URL}/{direct_id}", headers=alice).status_code == 404
    assert (
        client.get(f"/api/v1/conversations/{group['id']}", headers=alice).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/conversations/{group['id']}/messages", headers=alice
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/conversations/{group['id']}/messages",
            json={"body": "wrong route"},
            headers=alice,
        ).status_code
        == 404
    )
    assert (
        client.get(f"{GROUPS_URL}/{direct_id}/messages", headers=alice).status_code
        == 404
    )


def test_group_list_and_search_pagination(client, api) -> None:
    _, alice = api.create_user("alice@example.com", username="alice")
    _, bob = api.create_user("bob@example.com", username="bob")

    for index in range(3):
        create_group(client, alice, name=f"Public {index}", visibility="public")
    private = create_group(client, alice, name="Private Only")

    page = client.get(GROUPS_URL, params={"limit": 2, "offset": 0}, headers=alice)
    assert page.status_code == 200
    assert page.json()["total"] == 4
    assert len(page.json()["items"]) == 2

    page2 = client.get(GROUPS_URL, params={"limit": 2, "offset": 2}, headers=alice)
    assert page2.status_code == 200
    assert len(page2.json()["items"]) == 2
    ids = {item["id"] for item in page.json()["items"]} | {
        item["id"] for item in page2.json()["items"]
    }
    assert private["id"] in ids

    search = client.get(
        f"{GROUPS_URL}/search", params={"q": "public", "limit": 2}, headers=bob
    )
    assert search.status_code == 200
    assert search.json()["total"] == 3
    assert len(search.json()["items"]) == 2

    assert (
        client.get(f"{GROUPS_URL}/search", params={"q": ""}, headers=bob).status_code
        == 422
    )


def test_member_leave_and_removed_member_lose_access(client, api) -> None:
    _, alice = api.create_user("alice@example.com", username="alice")
    bob_account, bob = api.create_user("bob@example.com", username="bob")
    carol_account, carol = api.create_user("carol@example.com", username="carol")
    group = create_group(client, alice)
    add_member(client, alice, bob, group["id"], username="bob")
    add_member(client, alice, carol, group["id"], username="carol")

    assert client.post(f"{GROUPS_URL}/{group['id']}/leave", headers=bob).status_code == 204
    assert client.get(f"{GROUPS_URL}/{group['id']}", headers=bob).status_code == 404
    assert (
        client.post(
            f"{GROUPS_URL}/{group['id']}/messages",
            json={"body": "after leaving"},
            headers=bob,
        ).status_code
        == 404
    )

    removed = client.delete(
        f"{GROUPS_URL}/{group['id']}/members/{carol_account['id']}",
        headers=alice,
    )
    assert removed.status_code == 204
    assert client.get(f"{GROUPS_URL}/{group['id']}", headers=carol).status_code == 404
    assert (
        client.get(f"{GROUPS_URL}/{group['id']}/messages", headers=carol).status_code
        == 404
    )
    assert (
        client.post(
            f"{GROUPS_URL}/{group['id']}/messages",
            json={"body": "after removal"},
            headers=carol,
        ).status_code
        == 404
    )

    # The removed account's id is no longer a member, so a second removal is 404.
    assert (
        client.delete(
            f"{GROUPS_URL}/{group['id']}/members/{carol_account['id']}",
            headers=alice,
        ).status_code
        == 404
    )


def test_blocked_user_cannot_join_a_public_group(client, api) -> None:
    _, alice = api.create_user("alice@example.com", username="alice")
    _, bob = api.create_user("bob@example.com", username="bob")
    group = create_group(client, alice, name="Blocked Join", visibility="public")

    assert (
        client.post(
            "/api/v1/blocks", json={"username": "bob"}, headers=alice
        ).status_code
        == 201
    )
    assert client.post(f"{GROUPS_URL}/{group['id']}/join", headers=bob).status_code == 403
    assert (
        client.delete("/api/v1/blocks/bob", headers=alice).status_code == 204
    )
    assert client.post(f"{GROUPS_URL}/{group['id']}/join", headers=bob).status_code == 201


def test_group_requires_authentication(client) -> None:
    assert client.get(GROUPS_URL).status_code == 401
    assert client.post(GROUPS_URL, json={"name": "No auth"}).status_code == 401
    assert client.get(f"{GROUPS_URL}/search", params={"q": "x"}).status_code == 401
    assert client.get(f"{GROUPS_URL}/invitations").status_code == 401
