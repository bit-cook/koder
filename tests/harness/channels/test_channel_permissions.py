"""Tests for channel permission relay."""

import pytest

from koder_agent.harness.channels.permissions import (
    ID_ALPHABET,
    PERMISSION_REPLY_RE,
    ChannelPermissionCallbacks,
    _fnv1a_hash,
    _hash_to_id,
    create_channel_permission_callbacks,
    short_request_id,
    truncate_for_preview,
)


class TestFnv1aHash:
    def test_empty_string(self):
        assert _fnv1a_hash("") == 0x811C9DC5

    def test_known_vector(self):
        """FNV-1a('a') should match the standard test vector."""
        # FNV-1a of 'a': 0x811c9dc5 ^ 0x61 = 0x811c9da4, * prime & mask
        h = (0x811C9DC5 ^ ord("a")) * 0x01000193 & 0xFFFFFFFF
        assert _fnv1a_hash("a") == h

    def test_deterministic(self):
        assert _fnv1a_hash("hello") == _fnv1a_hash("hello")

    def test_different_inputs(self):
        assert _fnv1a_hash("abc") != _fnv1a_hash("abd")


class TestHashToId:
    def test_returns_5_chars(self):
        result = _hash_to_id("test")
        assert len(result) == 5

    def test_only_alphabet_chars(self):
        result = _hash_to_id("test")
        assert all(c in ID_ALPHABET for c in result)

    def test_no_l_in_output(self):
        """'l' is excluded from ID_ALPHABET."""
        assert "l" not in ID_ALPHABET
        # Generate many IDs and check
        for i in range(100):
            result = _hash_to_id(f"input-{i}")
            assert "l" not in result

    def test_deterministic(self):
        assert _hash_to_id("same") == _hash_to_id("same")


class TestShortRequestId:
    def test_returns_5_lowercase_letters(self):
        result = short_request_id("toolu_abc123")
        assert len(result) == 5
        assert result == result.lower()
        assert all(c in ID_ALPHABET for c in result)

    def test_deterministic(self):
        assert short_request_id("id1") == short_request_id("id1")

    def test_different_inputs_different_ids(self):
        assert short_request_id("id1") != short_request_id("id2")

    def test_avoids_profanity(self):
        """If first hash contains a blocklisted substring, it retries."""
        # We can't easily force a profanity collision, but we can verify
        # the function always returns a string without blocklisted substrings
        from koder_agent.harness.channels.permissions import ID_AVOID_SUBSTRINGS

        for i in range(200):
            result = short_request_id(f"test-input-{i}")
            for bad in ID_AVOID_SUBSTRINGS:
                assert bad not in result, f"ID {result!r} contains blocklisted '{bad}'"


class TestTruncateForPreview:
    def test_short_string(self):
        result = truncate_for_preview({"key": "value"})
        assert result == '{"key": "value"}'

    def test_long_string_truncated(self):
        data = {"key": "x" * 300}
        result = truncate_for_preview(data, max_len=50)
        assert len(result) == 51  # 50 chars + ellipsis
        assert result.endswith("\u2026")

    def test_exact_length(self):
        data = "x" * 198  # JSON: '"' + 198 + '"' = 200
        result = truncate_for_preview(data, max_len=200)
        assert len(result) == 200
        assert not result.endswith("\u2026")

    def test_unserializable(self):
        assert truncate_for_preview(object()) == "(unserializable)"


class TestPermissionReplyRegex:
    @pytest.mark.parametrize(
        "text,expected_verdict,expected_id",
        [
            ("yes abcde", "yes", "abcde"),
            ("y abcde", "y", "abcde"),
            ("no abcde", "no", "abcde"),
            ("n abcde", "n", "abcde"),
            ("  YES ABCDE  ", "YES", "ABCDE"),
            ("y  tbxkq", "y", "tbxkq"),
        ],
    )
    def test_valid_replies(self, text, expected_verdict, expected_id):
        m = PERMISSION_REPLY_RE.match(text)
        assert m is not None
        assert m.group(1) == expected_verdict
        assert m.group(2) == expected_id

    @pytest.mark.parametrize(
        "text",
        [
            "yes",  # no ID
            "abcde",  # no verdict
            "yes abcle",  # 'l' not in alphabet
            "yes abcdef",  # 6 chars
            "yes abc",  # 3 chars
            "approve abcde",  # wrong verdict word
            "yes abc1e",  # digit in ID
        ],
    )
    def test_invalid_replies(self, text):
        assert PERMISSION_REPLY_RE.match(text) is None


class TestChannelPermissionCallbacks:
    def test_register_and_resolve(self):
        cbs = create_channel_permission_callbacks()
        received = []
        cbs.on_response("ABCDE", lambda r: received.append(r))

        resolved = cbs.resolve("abcde", "allow", "plugin:slack:123")
        assert resolved is True
        assert len(received) == 1
        assert received[0].behavior == "allow"
        assert received[0].from_server == "plugin:slack:123"

    def test_resolve_unknown_id(self):
        cbs = ChannelPermissionCallbacks()
        assert cbs.resolve("unknown", "allow", "s") is False

    def test_resolve_deletes_before_calling(self):
        """Entry should be gone even if handler re-enters."""
        cbs = ChannelPermissionCallbacks()
        inside_count = [0]

        def handler(resp):
            inside_count[0] = cbs.pending_count

        cbs.on_response("abc", handler)
        assert cbs.pending_count == 1
        cbs.resolve("abc", "deny", "s")
        assert inside_count[0] == 0  # deleted before handler ran

    def test_unsubscribe(self):
        cbs = ChannelPermissionCallbacks()
        unsub = cbs.on_response("abc", lambda r: None)
        assert cbs.pending_count == 1
        unsub()
        assert cbs.pending_count == 0
        assert cbs.resolve("abc", "allow", "s") is False

    def test_case_insensitive_matching(self):
        cbs = ChannelPermissionCallbacks()
        received = []
        cbs.on_response("AbCdE", lambda r: received.append(r))
        assert cbs.resolve("abcde", "allow", "s") is True
        assert len(received) == 1

    def test_no_double_fire(self):
        cbs = ChannelPermissionCallbacks()
        count = [0]
        cbs.on_response("abc", lambda r: count.__setitem__(0, count[0] + 1))
        cbs.resolve("abc", "allow", "s")
        cbs.resolve("abc", "allow", "s")  # second should be no-op
        assert count[0] == 1
