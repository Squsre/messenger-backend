"""Message/media integration: send, attach, history, realtime and statuses."""

from __future__ import annotations

from sqlalchemy import select

from app.core.security import MESSAGE_MAX_LENGTH
from app.db.models import Message
from tests._media import upload_one
from tests._realtime import conversation_between, message_frame, open_socket


def _pair(client, api):
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    conversation_id = conversation_between(client, alice_headers, "bob")
    return conversation_id, alice_headers, bob_headers


def _send(client, headers, conversation_id, payload):
    return client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json=payload,
    )


def _history(client, headers, conversation_id):
    response = client.get(
        f"/api/v1/conversations/{conversation_id}/messages", headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_text_only_messages_keep_the_same_shape(client, api) -> None:
    conversation_id, alice_headers, _ = _pair(client, api)

    message = _send(client, alice_headers, conversation_id, {"body": "plain text"})
    assert message.status_code == 201, message.text
    assert message.json()["body"] == "plain text"
    assert message.json()["media"] is None

    history = _history(client, alice_headers, conversation_id)
    assert history["items"][0]["body"] == "plain text"
    assert history["items"][0]["media"] is None


def test_send_text_and_media_and_return_it_everywhere(client, api) -> None:
    conversation_id, alice_headers, bob_headers = _pair(client, api)
    media = upload_one(client, alice_headers, conversation_id)

    response = _send(
        client,
        alice_headers,
        conversation_id,
        {"body": "  look at this  ", "media_id": media["id"]},
    )

    assert response.status_code == 201, response.text
    message = response.json()
    assert message["body"] == "look at this"
    assert message["media"]["id"] == media["id"]
    assert message["media"]["message_id"] == message["id"]
    assert message["media"]["uploader_username"] == "alice"

    history = _history(client, bob_headers, conversation_id)
    assert history["items"][0]["media"]["id"] == media["id"]

    conversations = client.get("/api/v1/conversations", headers=bob_headers).json()
    assert conversations["items"][0]["last_message"]["media"]["id"] == media["id"]
    assert conversations["items"][0]["unread_count"] == 1

    metadata = client.get(f"/api/v1/media/{media['id']}", headers=bob_headers).json()
    assert metadata["message_id"] == message["id"]

    # Phase-9 statuses still work for media messages.
    receipt = client.post(
        f"/api/v1/conversations/{conversation_id}/read",
        headers=bob_headers,
        json={"last_message_id": message["id"]},
    )
    assert receipt.status_code == 200
    assert receipt.json()["unread_count"] == 0
    assert _history(client, alice_headers, conversation_id)["items"][0]["read_at"]


def test_media_only_message_is_supported(client, api) -> None:
    conversation_id, alice_headers, _ = _pair(client, api)
    media = upload_one(client, alice_headers, conversation_id)

    response = _send(client, alice_headers, conversation_id, {"media_id": media["id"]})

    assert response.status_code == 201, response.text
    message = response.json()
    assert message["body"] is None
    assert message["media"]["id"] == media["id"]

    history = _history(client, alice_headers, conversation_id)
    assert history["items"][0]["body"] is None
    assert history["items"][0]["media"]["id"] == media["id"]


def test_attach_media_to_an_existing_message_and_broadcast(client, api) -> None:
    conversation_id, alice_headers, bob_headers = _pair(client, api)
    sent = _send(client, alice_headers, conversation_id, {"body": "before"})
    assert sent.status_code == 201
    message_id = sent.json()["id"]
    media = upload_one(client, alice_headers, conversation_id)

    with open_socket(client, api.login("bob@example.com")) as bob:
        response = client.post(
            f"/api/v1/media/{media['id']}/attach",
            headers=alice_headers,
            json={"message_id": message_id},
        )
        assert response.status_code == 200, response.text
        event = bob.receive_json()

    assert event["type"] == "message_updated"
    assert event["message"]["id"] == message_id
    assert event["message"]["media"]["id"] == media["id"]

    history = _history(client, bob_headers, conversation_id)
    assert history["items"][0]["body"] == "before"
    assert history["items"][0]["media"]["id"] == media["id"]
    assert response.json()["message_id"] == message_id

    # A message can hold only one attachment, and an attached file is history.
    again = client.post(
        f"/api/v1/media/{media['id']}/attach",
        headers=alice_headers,
        json={"message_id": message_id},
    )
    assert again.status_code == 409
    delete = client.delete(f"/api/v1/media/{media['id']}", headers=alice_headers)
    assert delete.status_code == 409


def test_send_rejects_fake_foreign_or_cross_conversation_media(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    api.create_user("carol@example.com")
    bob_conversation = conversation_between(client, alice_headers, "bob")
    carol_conversation = conversation_between(client, alice_headers, "carol")

    fake = _send(
        client,
        alice_headers,
        bob_conversation,
        {"body": "x", "media_id": "0" * 32},
    )
    assert fake.status_code == 404

    bob_media = upload_one(client, bob_headers, bob_conversation)
    foreign = _send(
        client,
        alice_headers,
        bob_conversation,
        {"body": "x", "media_id": bob_media["id"]},
    )
    assert foreign.status_code == 404

    other_conversation_media = upload_one(
        client, alice_headers, carol_conversation
    )
    cross = _send(
        client,
        alice_headers,
        bob_conversation,
        {"body": "x", "media_id": other_conversation_media["id"]},
    )
    assert cross.status_code == 404

    # The uploader's own file cannot be attached to a message in another
    # conversation either.
    message = _send(client, alice_headers, bob_conversation, {"body": "mine"}).json()
    attach = client.post(
        f"/api/v1/media/{other_conversation_media['id']}/attach",
        headers=alice_headers,
        json={"message_id": message["id"]},
    )
    assert attach.status_code == 404


def test_attach_rejects_another_users_message(client, api) -> None:
    conversation_id, alice_headers, bob_headers = _pair(client, api)
    alice_message = _send(
        client, alice_headers, conversation_id, {"body": "alice"}
    ).json()
    bob_media = upload_one(client, bob_headers, conversation_id)

    response = client.post(
        f"/api/v1/media/{bob_media['id']}/attach",
        headers=bob_headers,
        json={"message_id": alice_message["id"]},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_attach_rejects_foreign_or_unknown_media(client, api) -> None:
    conversation_id, alice_headers, bob_headers = _pair(client, api)
    message = _send(client, alice_headers, conversation_id, {"body": "hi"}).json()
    bob_media = upload_one(client, bob_headers, conversation_id)

    foreign = client.post(
        f"/api/v1/media/{bob_media['id']}/attach",
        headers=alice_headers,
        json={"message_id": message["id"]},
    )
    assert foreign.status_code == 404

    unknown = client.post(
        f"/api/v1/media/{'0' * 32}/attach",
        headers=alice_headers,
        json={"message_id": message["id"]},
    )
    assert unknown.status_code == 404


def test_attach_is_refused_after_a_block(client, api) -> None:
    conversation_id, alice_headers, bob_headers = _pair(client, api)
    message = _send(client, alice_headers, conversation_id, {"body": "before"}).json()
    media = upload_one(client, alice_headers, conversation_id)

    blocked = client.post(
        "/api/v1/blocks", json={"username": "bob"}, headers=alice_headers
    )
    assert blocked.status_code == 201

    response = client.post(
        f"/api/v1/media/{media['id']}/attach",
        headers=alice_headers,
        json={"message_id": message["id"]},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    # A refused attachment must leave the upload untouched.
    metadata = client.get(f"/api/v1/media/{media['id']}", headers=alice_headers)
    assert metadata.status_code == 200
    assert metadata.json()["message_id"] is None


def test_websocket_send_with_media_and_statuses(client, api) -> None:
    conversation_id, alice_headers, bob_headers = _pair(client, api)
    media = upload_one(client, alice_headers, conversation_id)

    with open_socket(client, api.login("bob@example.com")) as bob, open_socket(
        client, api.login("alice@example.com")
    ) as alice:
        alice.send_json(message_frame(conversation_id, "caption", media_id=media["id"]))
        ack = alice.receive_json()
        assert ack["type"] == "ack"
        assert ack["message"]["media"]["id"] == media["id"]
        received = bob.receive_json()
        assert received["type"] == "message"
        assert received["message"]["media"]["id"] == media["id"]

        alice.send_json(message_frame(conversation_id, None, media_id=media["id"]))
        # The same file cannot be attached twice; the socket reports a conflict
        # and stays usable.
        duplicate = alice.receive_json()
        assert duplicate["type"] == "error"
        assert duplicate["code"] == "conflict"

    message_id = ack["message"]["id"]
    receipt = client.post(
        f"/api/v1/conversations/{conversation_id}/read",
        headers=bob_headers,
        json={"last_message_id": message_id},
    )
    assert receipt.status_code == 200
    history = _history(client, alice_headers, conversation_id)
    assert history["items"][0]["media"]["id"] == media["id"]
    assert history["items"][0]["read_at"] is not None


def test_websocket_media_only_message(client, api) -> None:
    conversation_id, alice_headers, _ = _pair(client, api)
    media = upload_one(client, alice_headers, conversation_id)

    with open_socket(client, api.login("alice@example.com")) as alice:
        alice.send_json(message_frame(conversation_id, None, media_id=media["id"]))
        ack = alice.receive_json()

    assert ack["type"] == "ack"
    assert ack["message"]["body"] is None
    assert ack["message"]["media"]["id"] == media["id"]


def test_idempotent_websocket_send_compares_text_and_media(client, api) -> None:
    conversation_id, alice_headers, _ = _pair(client, api)
    first_media = upload_one(client, alice_headers, conversation_id)
    second_media = upload_one(client, alice_headers, conversation_id)

    with open_socket(client, api.login("alice@example.com")) as alice:
        frame = message_frame(
            conversation_id, "same", "retry-media", media_id=first_media["id"]
        )
        alice.send_json(frame)
        first = alice.receive_json()
        assert first["type"] == "ack"
        assert first["duplicate"] is False

        alice.send_json(frame)
        replay = alice.receive_json()
        assert replay["type"] == "ack"
        assert replay["duplicate"] is True
        assert replay["message"]["id"] == first["message"]["id"]

        alice.send_json(
            message_frame(
                conversation_id, "same", "retry-media", media_id=second_media["id"]
            )
        )
        changed_media = alice.receive_json()
        assert changed_media["type"] == "error"
        assert changed_media["code"] == "conflict"

        alice.send_json(message_frame(conversation_id, "same", "retry-media"))
        changed_text = alice.receive_json()
        assert changed_text["type"] == "error"
        assert changed_text["code"] == "conflict"

    history = _history(client, alice_headers, conversation_id)
    assert len(history["items"]) == 1
    assert len(history["items"][0]["body"]) <= MESSAGE_MAX_LENGTH
    assert history["items"][0]["media"]["id"] == first_media["id"]
    # The second upload remains unattached after the rejected attempt.
    metadata = client.get(
        f"/api/v1/media/{second_media['id']}", headers=alice_headers
    ).json()
    assert metadata["message_id"] is None


def test_validation_still_rejects_empty_messages(client, api) -> None:
    conversation_id, alice_headers, _ = _pair(client, api)

    missing = _send(client, alice_headers, conversation_id, {})
    assert missing.status_code == 422

    blank = _send(client, alice_headers, conversation_id, {"body": "   "})
    assert blank.status_code == 422

    too_long = _send(
        client,
        alice_headers,
        conversation_id,
        {"body": "x" * (MESSAGE_MAX_LENGTH + 1)},
    )
    assert too_long.status_code == 422


def test_media_only_message_is_stored_with_a_null_body(client, api, db_session) -> None:
    conversation_id, alice_headers, _ = _pair(client, api)
    media = upload_one(client, alice_headers, conversation_id)
    message = _send(client, alice_headers, conversation_id, {"media_id": media["id"]}).json()

    row = db_session.execute(
        select(Message).where(Message.id == message["id"])
    ).scalar_one()
    assert row.body is None
