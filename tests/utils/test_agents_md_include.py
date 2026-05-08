"""Tests for AGENTS.md @include directive."""

from pathlib import Path

from koder_agent.utils.prompts import resolve_includes


class TestResolveIncludes:
    def test_no_includes(self, tmp_path):
        content = "# My Project\n\nSome instructions."
        result = resolve_includes(content, tmp_path)
        assert result == content

    def test_relative_include(self, tmp_path):
        # Create included file
        (tmp_path / "rules.md").write_text("# Rules\nDon't break things.")
        content = "# Project\n\n@./rules.md\n\nMore text."
        result = resolve_includes(content, tmp_path)
        assert "Don't break things." in result

    def test_bare_include(self, tmp_path):
        (tmp_path / "style.md").write_text("Use 4 spaces.")
        content = "# Project\n\n@style.md\n"
        result = resolve_includes(content, tmp_path)
        assert "Use 4 spaces." in result

    def test_nonexistent_file_ignored(self, tmp_path):
        content = "# Project\n\n@./nonexistent.md\n"
        result = resolve_includes(content, tmp_path)
        assert result == content

    def test_max_depth_prevents_deep_recursion(self, tmp_path):
        # Create a chain: a.md -> b.md -> c.md -> d.md -> e.md -> f.md -> g.md
        for letter in "abcdef":
            next_letter = chr(ord(letter) + 1)
            (tmp_path / f"{letter}.md").write_text(f"Content {letter}\n@./{next_letter}.md")
        (tmp_path / "g.md").write_text("Content g (deepest)")

        content = "@./a.md"
        result = resolve_includes(content, tmp_path, max_depth=3)
        assert "Content a" in result
        assert "Content b" in result
        assert "Content c" in result
        # Should stop at depth 3
        assert "Content g (deepest)" not in result

    def test_circular_reference_prevented(self, tmp_path):
        (tmp_path / "a.md").write_text("Content A\n@./b.md")
        (tmp_path / "b.md").write_text("Content B\n@./a.md")

        content = "@./a.md"
        result = resolve_includes(content, tmp_path)
        assert "Content A" in result
        assert "Content B" in result
        # Should not infinite loop

    def test_code_block_includes_skipped(self, tmp_path):
        (tmp_path / "secret.md").write_text("SECRET DATA")
        content = "# Project\n\n```\n@./secret.md\n```\n"
        result = resolve_includes(content, tmp_path)
        assert "SECRET DATA" not in result

    def test_absolute_path_include(self, tmp_path):
        included = tmp_path / "abs.md"
        included.write_text("Absolute content")
        content = f"# Project\n\n@{included}\n"
        result = resolve_includes(content, tmp_path)
        assert "Absolute content" in result

    def test_home_path_include(self, tmp_path, monkeypatch):
        # Create a file in a fake home dir
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        (fake_home / "global.md").write_text("Global rules")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        content = "# Project\n\n@~/global.md\n"
        result = resolve_includes(content, tmp_path)
        assert "Global rules" in result

    def test_multiple_includes(self, tmp_path):
        (tmp_path / "a.md").write_text("AAA")
        (tmp_path / "b.md").write_text("BBB")
        content = "# Project\n@./a.md\n@./b.md\n"
        result = resolve_includes(content, tmp_path)
        assert "AAA" in result
        assert "BBB" in result

    def test_inline_code_skipped(self, tmp_path):
        (tmp_path / "secret.md").write_text("SECRET DATA")
        content = "Use `@./secret.md` to include."
        result = resolve_includes(content, tmp_path)
        assert "SECRET DATA" not in result

    def test_included_content_appended_after(self, tmp_path):
        (tmp_path / "extra.md").write_text("EXTRA CONTENT")
        content = "# Main\n\n@./extra.md\n\nFooter."
        result = resolve_includes(content, tmp_path)
        # Original content comes first
        main_pos = result.index("# Main")
        footer_pos = result.index("Footer.")
        extra_pos = result.index("EXTRA CONTENT")
        assert main_pos < footer_pos < extra_pos

    def test_nested_include_resolves_relative_to_parent(self, tmp_path):
        subdir = tmp_path / "sub"
        subdir.mkdir()
        (subdir / "child.md").write_text("CHILD CONTENT")
        (tmp_path / "parent.md").write_text("PARENT\n@./sub/child.md")
        content = "@./parent.md"
        result = resolve_includes(content, tmp_path)
        assert "PARENT" in result
        assert "CHILD CONTENT" in result
