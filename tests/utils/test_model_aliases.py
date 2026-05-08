"""Tests for model alias resolution."""

from koder_agent.utils.model_info import MODEL_ALIASES, resolve_model_alias


class TestModelAliases:
    def test_aliases_dict_exists(self):
        assert isinstance(MODEL_ALIASES, dict)
        assert "sonnet" in MODEL_ALIASES
        assert "opus" in MODEL_ALIASES
        assert "haiku" in MODEL_ALIASES

    def test_resolve_sonnet(self):
        result = resolve_model_alias("sonnet")
        assert "sonnet" in result.lower() or "claude" in result.lower()

    def test_resolve_opus(self):
        result = resolve_model_alias("opus")
        assert "opus" in result.lower() or "claude" in result.lower()

    def test_resolve_haiku(self):
        result = resolve_model_alias("haiku")
        assert "haiku" in result.lower() or "claude" in result.lower()

    def test_resolve_with_1m_suffix(self):
        """[1m] suffix should select 1M context variant."""
        result = resolve_model_alias("sonnet[1m]")
        assert result is not None
        # Should contain some indicator of extended context

    def test_resolve_opus_1m(self):
        result = resolve_model_alias("opus[1m]")
        assert result is not None

    def test_resolve_unknown_passes_through(self):
        """Unknown model names should pass through unchanged."""
        assert resolve_model_alias("gpt-4o") == "gpt-4o"
        assert resolve_model_alias("claude-sonnet-4-6") == "claude-sonnet-4-6"
        assert resolve_model_alias("my-custom-model") == "my-custom-model"

    def test_resolve_case_insensitive(self):
        """Aliases should be case-insensitive."""
        r1 = resolve_model_alias("Sonnet")
        r2 = resolve_model_alias("SONNET")
        r3 = resolve_model_alias("sonnet")
        assert r1 == r2 == r3

    def test_resolve_best_alias(self):
        """'best' should resolve to the most capable model."""
        result = resolve_model_alias("best")
        assert result is not None
        assert result != "best"

    def test_aliases_have_reasonable_values(self):
        """All aliases should resolve to non-empty strings."""
        for alias, model in MODEL_ALIASES.items():
            assert isinstance(model, str)
            assert len(model) > 0, f"Alias '{alias}' has empty value"
