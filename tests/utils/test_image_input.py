"""Tests for multimodal image input helpers (koder_agent.utils.image_input)."""

from __future__ import annotations

import base64

import pytest

from koder_agent.utils.image_input import (
    SUPPORTED_IMAGE_EXTENSIONS,
    ImageInputError,
    build_image_content_block,
    build_image_content_blocks,
    build_multimodal_input,
    detect_image_mime_type,
    encode_image_data_url,
    model_supports_vision,
)

# A minimal valid 1x1 transparent PNG.
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC"
)
# A minimal JPEG SOI + APP0 header (enough for magic-byte detection).
_JPEG_HEAD = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01"
# A GIF89a header.
_GIF_HEAD = b"GIF89a\x01\x00\x01\x00"
# A WEBP (RIFF....WEBP) header.
_WEBP_HEAD = b"RIFF\x24\x00\x00\x00WEBPVP8 "


def _write(tmp_path, name: str, data: bytes):
    path = tmp_path / name
    path.write_bytes(data)
    return path


class TestEncodeDataUrl:
    def test_encodes_png_to_data_url(self, tmp_path):
        path = _write(tmp_path, "tiny.png", _PNG_1X1)
        url = encode_image_data_url(str(path))
        assert url.startswith("data:image/png;base64,")
        payload = url.split(",", 1)[1]
        assert base64.b64decode(payload) == _PNG_1X1

    def test_jpeg_detected_by_magic(self, tmp_path):
        path = _write(tmp_path, "photo.jpg", _JPEG_HEAD)
        url = encode_image_data_url(str(path))
        assert url.startswith("data:image/jpeg;base64,")

    def test_gif_detected_by_magic(self, tmp_path):
        path = _write(tmp_path, "anim.gif", _GIF_HEAD)
        url = encode_image_data_url(str(path))
        assert url.startswith("data:image/gif;base64,")

    def test_webp_detected_by_magic(self, tmp_path):
        path = _write(tmp_path, "pic.webp", _WEBP_HEAD)
        url = encode_image_data_url(str(path))
        assert url.startswith("data:image/webp;base64,")

    def test_magic_bytes_win_over_extension(self, tmp_path):
        # Bytes are a real PNG but the file is named .jpg — trust the bytes.
        path = _write(tmp_path, "mislabeled.jpg", _PNG_1X1)
        url = encode_image_data_url(str(path))
        assert url.startswith("data:image/png;base64,")


class TestErrors:
    def test_missing_path_raises_clear_error(self, tmp_path):
        missing = tmp_path / "nope.png"
        with pytest.raises(ImageInputError) as exc:
            encode_image_data_url(str(missing))
        assert "does not exist" in str(exc.value)
        assert "nope.png" in str(exc.value)

    def test_directory_path_raises(self, tmp_path):
        with pytest.raises(ImageInputError) as exc:
            encode_image_data_url(str(tmp_path))
        assert "not a file" in str(exc.value)

    def test_empty_file_raises(self, tmp_path):
        path = _write(tmp_path, "empty.png", b"")
        with pytest.raises(ImageInputError) as exc:
            encode_image_data_url(str(path))
        assert "empty" in str(exc.value)

    def test_non_image_with_image_extension_raises(self, tmp_path):
        # .png extension but content is plain text — reject up front.
        path = _write(tmp_path, "fake.png", b"this is not an image")
        with pytest.raises(ImageInputError) as exc:
            encode_image_data_url(str(path))
        assert "valid image" in str(exc.value)

    def test_unsupported_extension_raises(self, tmp_path):
        path = _write(tmp_path, "notes.txt", b"hello world")
        with pytest.raises(ImageInputError) as exc:
            encode_image_data_url(str(path))
        assert "Unsupported image type" in str(exc.value)

    def test_detect_mime_type_helper_rejects_garbage(self, tmp_path):
        path = tmp_path / "x.dat"
        with pytest.raises(ImageInputError):
            detect_image_mime_type(path, b"\x00\x01\x02\x03")


class TestContentBlocks:
    def test_build_single_block(self, tmp_path):
        path = _write(tmp_path, "tiny.png", _PNG_1X1)
        block = build_image_content_block(str(path))
        assert block["type"] == "input_image"
        assert block["detail"] == "auto"
        assert block["image_url"].startswith("data:image/png;base64,")

    def test_build_block_custom_detail(self, tmp_path):
        path = _write(tmp_path, "tiny.png", _PNG_1X1)
        block = build_image_content_block(str(path), detail="high")
        assert block["detail"] == "high"

    def test_build_blocks_multiple(self, tmp_path):
        p1 = _write(tmp_path, "a.png", _PNG_1X1)
        p2 = _write(tmp_path, "b.jpg", _JPEG_HEAD)
        blocks = build_image_content_blocks([str(p1), str(p2)])
        assert len(blocks) == 2
        assert blocks[0]["image_url"].startswith("data:image/png")
        assert blocks[1]["image_url"].startswith("data:image/jpeg")

    def test_build_blocks_empty_or_none(self):
        assert build_image_content_blocks([]) == []
        assert build_image_content_blocks(None) == []

    def test_build_blocks_first_bad_path_raises(self, tmp_path):
        good = _write(tmp_path, "a.png", _PNG_1X1)
        with pytest.raises(ImageInputError):
            build_image_content_blocks([str(good), str(tmp_path / "missing.png")])


class TestBuildMultimodalInput:
    def test_no_images_returns_plain_text_unchanged(self):
        # This proves the "no --image = unchanged plain-text path" requirement.
        result = build_multimodal_input("hello world", [])
        assert result == "hello world"
        assert isinstance(result, str)

    def test_no_images_no_text_returns_none(self):
        assert build_multimodal_input(None, []) is None
        assert build_multimodal_input(None, None) is None

    def test_image_plus_text_builds_multimodal_message(self, tmp_path):
        path = _write(tmp_path, "tiny.png", _PNG_1X1)
        result = build_multimodal_input("describe this", [str(path)])

        assert isinstance(result, list)
        assert len(result) == 1
        message = result[0]
        assert message["role"] == "user"

        content = message["content"]
        assert isinstance(content, list)
        # image block(s) come first, then the text block
        assert content[0]["type"] == "input_image"
        assert content[0]["image_url"].startswith("data:image/png;base64,")
        assert content[-1] == {"type": "input_text", "text": "describe this"}

    def test_multiple_images_preserve_order_then_text(self, tmp_path):
        p1 = _write(tmp_path, "a.png", _PNG_1X1)
        p2 = _write(tmp_path, "b.gif", _GIF_HEAD)
        result = build_multimodal_input("caption", [str(p1), str(p2)])
        content = result[0]["content"]
        assert [c["type"] for c in content] == ["input_image", "input_image", "input_text"]
        assert content[0]["image_url"].startswith("data:image/png")
        assert content[1]["image_url"].startswith("data:image/gif")

    def test_image_without_text_has_only_image_block(self, tmp_path):
        path = _write(tmp_path, "tiny.png", _PNG_1X1)
        result = build_multimodal_input(None, [str(path)])
        content = result[0]["content"]
        assert len(content) == 1
        assert content[0]["type"] == "input_image"

    def test_bad_image_path_raises_before_dispatch(self, tmp_path):
        with pytest.raises(ImageInputError) as exc:
            build_multimodal_input("hi", [str(tmp_path / "ghost.png")])
        assert "does not exist" in str(exc.value)


def _litellm_vision_available() -> bool:
    """True only when the real litellm.supports_vision is usable.

    Other test modules stub ``litellm`` in ``sys.modules`` with a bare module
    that lacks ``supports_vision`` / a populated ``model_cost``; under that stub
    ``model_supports_vision`` correctly degrades to False. These capability
    assertions are only meaningful against real litellm, so skip otherwise.
    """
    try:
        import litellm

        return bool(litellm.supports_vision(model="gpt-4o"))
    except Exception:
        return False


class TestVisionCapability:
    @pytest.mark.skipif(
        not _litellm_vision_available(),
        reason="litellm is stubbed/unavailable in this run; capability check degrades to False",
    )
    def test_known_vision_model(self):
        assert model_supports_vision("gpt-4o") is True

    def test_known_non_vision_model(self):
        assert model_supports_vision("gpt-3.5-turbo") is False

    def test_empty_model_returns_false(self):
        assert model_supports_vision("") is False

    def test_unknown_model_returns_false(self):
        assert model_supports_vision("totally-made-up-model-xyz") is False


class TestSupportedExtensions:
    def test_common_formats_present(self):
        for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            assert ext in SUPPORTED_IMAGE_EXTENSIONS
