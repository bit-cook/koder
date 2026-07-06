"""Tests wiring the -i/--image CLI flag to the multimodal input helper."""

from __future__ import annotations

import base64

import pytest

from koder_agent.cli import _build_cli_parser
from koder_agent.utils.image_input import ImageInputError, build_multimodal_input

_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC"
)


def _parser():
    # first_arg is None for the normal prompt path (adds the ``prompt`` positional).
    return _build_cli_parser(None)


class TestImageFlagParsing:
    def test_short_flag_repeatable(self):
        args = _parser().parse_args(["-i", "a.png", "-i", "b.png", "hello"])
        assert args.image == ["a.png", "b.png"]
        assert args.prompt == ["hello"]

    def test_long_flag(self):
        args = _parser().parse_args(["--image", "diagram.png", "explain"])
        assert args.image == ["diagram.png"]
        assert args.prompt == ["explain"]

    def test_default_empty_when_absent(self):
        args = _parser().parse_args(["just a prompt"])
        assert args.image == []

    def test_flag_with_print_mode(self):
        args = _parser().parse_args(["-i", "shot.png", "-p", "what is this"])
        assert args.image == ["shot.png"]
        assert args.print_prompt == ["what is this"]


class TestFlagToHelperIntegration:
    def test_parsed_image_builds_multimodal_message(self, tmp_path):
        path = tmp_path / "tiny.png"
        path.write_bytes(_PNG_1X1)

        args = _parser().parse_args(["-i", str(path), "describe", "this"])
        text = " ".join(args.prompt)
        result = build_multimodal_input(text, args.image)

        assert isinstance(result, list)
        content = result[0]["content"]
        assert content[0]["type"] == "input_image"
        assert content[-1] == {"type": "input_text", "text": "describe this"}

    def test_no_image_flag_leaves_plain_text_path(self, tmp_path):
        args = _parser().parse_args(["hello", "world"])
        text = " ".join(args.prompt)
        result = build_multimodal_input(text, args.image)
        assert result == "hello world"

    def test_nonexistent_image_errors_clearly(self, tmp_path):
        args = _parser().parse_args(["-i", str(tmp_path / "missing.png"), "hi"])
        with pytest.raises(ImageInputError) as exc:
            build_multimodal_input(" ".join(args.prompt), args.image)
        assert "does not exist" in str(exc.value)
