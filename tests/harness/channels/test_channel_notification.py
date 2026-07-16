"""Tests for channel notification handling and message wrapping."""

import asyncio

import pytest

from koder_agent.harness.channels.notification import (
    CHANNEL_NOTIFICATION_METHOD,
    CHANNEL_PERMISSION_METHOD,
    ChannelNotificationRouter,
    escape_xml_attr,
    validate_channel_message_params,
    validate_channel_permission_params,
    wrap_channel_message,
)


class TestEscapeXmlAttr:
    def test_no_escape_needed(self):
        assert escape_xml_attr("hello") == "hello"

    def test_ampersand(self):
        assert escape_xml_attr("a&b") == "a&amp;b"

    def test_double_quote(self):
        assert escape_xml_attr('a"b') == "a&quot;b"

    def test_single_quote(self):
        assert escape_xml_attr("a'b") == "a&apos;b"

    def test_less_than(self):
        assert escape_xml_attr("a<b") == "a&lt;b"

    def test_greater_than(self):
        assert escape_xml_attr("a>b") == "a&gt;b"

    def test_combined(self):
        assert escape_xml_attr('<"&">') == "&lt;&quot;&amp;&quot;&gt;"


class TestWrapChannelMessage:
    def test_basic_wrapping(self):
        result = wrap_channel_message("webhook", "hello world")
        assert result == '<channel source="webhook">\nhello world\n</channel>'

    def test_with_meta(self):
        result = wrap_channel_message("webhook", "body", {"chat_id": "123", "user": "alice"})
        assert 'source="webhook"' in result
        assert 'chat_id="123"' in result
        assert 'user="alice"' in result
        assert "\nbody\n" in result

    def test_meta_key_filtering(self):
        """Keys with hyphens or special chars are dropped."""
        result = wrap_channel_message("s", "b", {"good_key": "v", "bad-key": "x", "123bad": "y"})
        assert "good_key" in result
        assert "bad-key" not in result
        assert "123bad" not in result

    def test_meta_value_escaping(self):
        result = wrap_channel_message("s", "b", {"key": '<script>"xss"</script>'})
        assert "&lt;script&gt;" in result
        assert "&quot;xss&quot;" in result

    def test_source_escaping(self):
        result = wrap_channel_message('a"b', "content")
        assert 'source="a&quot;b"' in result

    def test_no_meta(self):
        result = wrap_channel_message("s", "content", None)
        assert result == '<channel source="s">\ncontent\n</channel>'


class TestValidateChannelMessageParams:
    def test_valid_params(self):
        content, meta = validate_channel_message_params({"content": "hello"})
        assert content == "hello"
        assert meta is None

    def test_valid_with_meta(self):
        content, meta = validate_channel_message_params({"content": "hi", "meta": {"k": "v"}})
        assert content == "hi"
        assert meta == {"k": "v"}

    def test_missing_content(self):
        with pytest.raises(ValueError, match="content"):
            validate_channel_message_params({})

    def test_non_string_content(self):
        with pytest.raises(ValueError, match="content"):
            validate_channel_message_params({"content": 123})

    def test_invalid_meta_type(self):
        with pytest.raises(ValueError, match="meta"):
            validate_channel_message_params({"content": "ok", "meta": "bad"})


class TestValidateChannelPermissionParams:
    def test_valid_allow(self):
        rid, behavior = validate_channel_permission_params(
            {"request_id": "abcde", "behavior": "allow"}
        )
        assert rid == "abcde"
        assert behavior == "allow"

    def test_valid_deny(self):
        rid, behavior = validate_channel_permission_params(
            {"request_id": "xyz", "behavior": "deny"}
        )
        assert behavior == "deny"

    def test_missing_request_id(self):
        with pytest.raises(ValueError, match="request_id"):
            validate_channel_permission_params({"behavior": "allow"})

    def test_invalid_behavior(self):
        with pytest.raises(ValueError, match="behavior"):
            validate_channel_permission_params({"request_id": "a", "behavior": "maybe"})


class TestChannelNotificationRouter:
    def test_dispatch_channel_message(self):
        router = ChannelNotificationRouter()
        received = []

        async def cb(server, content, meta):
            received.append((server, content, meta))

        router.on_channel_message(cb)
        asyncio.run(
            router.dispatch_raw_notification(
                "webhook",
                CHANNEL_NOTIFICATION_METHOD,
                {"content": "hello", "meta": {"k": "v"}},
            )
        )
        assert len(received) == 1
        assert received[0] == ("webhook", "hello", {"k": "v"})

    def test_dispatch_channel_permission(self):
        router = ChannelNotificationRouter()
        received = []

        async def cb(server, request_id, behavior):
            received.append((server, request_id, behavior))

        router.on_channel_permission(cb)
        asyncio.run(
            router.dispatch_raw_notification(
                "bot",
                CHANNEL_PERMISSION_METHOD,
                {"request_id": "abcde", "behavior": "allow"},
            )
        )
        assert len(received) == 1
        assert received[0] == ("bot", "abcde", "allow")

    def test_dispatch_unknown_method_returns_false(self):
        router = ChannelNotificationRouter()
        result = asyncio.run(router.dispatch_raw_notification("s", "notifications/other", {}))
        assert result is False

    def test_dispatch_invalid_params_logs_warning(self):
        """Invalid params should not raise; should return True (handled)."""
        router = ChannelNotificationRouter()
        result = asyncio.run(
            router.dispatch_raw_notification("s", CHANNEL_NOTIFICATION_METHOD, {"bad": "params"})
        )
        assert result is True

    def test_multiple_callbacks(self):
        router = ChannelNotificationRouter()
        counts = [0, 0]

        async def cb1(s, c, m):
            counts[0] += 1

        async def cb2(s, c, m):
            counts[1] += 1

        router.on_channel_message(cb1)
        router.on_channel_message(cb2)
        asyncio.run(
            router.dispatch_raw_notification("s", CHANNEL_NOTIFICATION_METHOD, {"content": "x"})
        )
        assert counts == [1, 1]

    def test_duplicate_callback_registrations_unregister_independently(self):
        router = ChannelNotificationRouter()
        received = []

        async def cb(server, content, meta):
            received.append((server, content, meta))

        unregister_first = router.on_channel_message(cb)
        unregister_second = router.on_channel_message(cb)
        unregister_first()
        unregister_first()

        asyncio.run(
            router.dispatch_raw_notification("s", CHANNEL_NOTIFICATION_METHOD, {"content": "x"})
        )
        assert received == [("s", "x", None)]

        unregister_second()
        asyncio.run(
            router.dispatch_raw_notification("s", CHANNEL_NOTIFICATION_METHOD, {"content": "y"})
        )
        assert received == [("s", "x", None)]

    def test_stale_unregister_handle_does_not_remove_new_session_callback(self):
        router = ChannelNotificationRouter()
        received = []

        async def old_cb(_server, content, _meta):
            received.append(("old", content))

        async def new_cb(_server, content, _meta):
            received.append(("new", content))

        unregister_old = router.on_channel_message(old_cb)
        unregister_old()
        unregister_new = router.on_channel_message(new_cb)
        unregister_old()

        asyncio.run(
            router.dispatch_raw_notification("s", CHANNEL_NOTIFICATION_METHOD, {"content": "x"})
        )
        assert received == [("new", "x")]

        unregister_new()
