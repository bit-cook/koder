from __future__ import annotations

from koder_agent.harness.code_intelligence import run_code_intelligence


def test_document_symbols_uses_python_ast(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "sample.py"
    source.write_text(
        "class Greeter:\n"
        "    def greet(self, name):\n"
        "        return helper(name)\n"
        "\n"
        "def helper(value):\n"
        "    return value.upper()\n",
        encoding="utf-8",
    )

    result = run_code_intelligence("document_symbols", path=str(source))

    assert "Document symbols" in result
    assert "sample.py:1:1 class Greeter" in result
    assert "sample.py:2:5 method greet in Greeter" in result
    assert "sample.py:5:1 function helper" in result


def test_workspace_symbols_filters_by_query(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sample.py").write_text("def helper(value):\n    return value\n", encoding="utf-8")
    (tmp_path / "app.ts").write_text(
        "export function renderApp() { return true }\n", encoding="utf-8"
    )

    result = run_code_intelligence("workspaceSymbol", path=str(tmp_path), query="render")

    assert "Workspace symbols" in result
    assert "app.ts:1:17 function renderApp" in result
    assert "helper" not in result


def test_definition_can_extract_symbol_from_position(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "sample.py"
    source.write_text(
        "def helper(value):\n    return value\n\nresult = helper('x')\n",
        encoding="utf-8",
    )

    result = run_code_intelligence("goToDefinition", path=str(source), line=4, character=10)

    assert "Definitions for helper" in result
    assert "sample.py:1:1 function helper" in result


def test_references_reports_textual_matches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sample.py").write_text(
        "def helper(value):\n    return value\n\nresult = helper('x')\n",
        encoding="utf-8",
    )

    result = run_code_intelligence("references", path=str(tmp_path), query="helper")

    assert "References for helper" in result
    assert "sample.py:1:5 def helper(value):" in result
    assert "sample.py:4:10 result = helper('x')" in result


def test_diagnostics_reports_python_syntax_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "broken.py"
    source.write_text("def broken(:\n    pass\n", encoding="utf-8")

    result = run_code_intelligence("diagnostics", path=str(source))

    assert "Diagnostics (1 issue)" in result
    assert "broken.py:1:" in result
