import sqlite3
import sys
import types
from pathlib import Path

# Stub litellm before importing koder_agent to avoid optional dependency issues
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.config.migration import migrate_config_file


def test_migration_preserves_legacy_db_bytes(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("model:\n  name: gpt-4.1\n", encoding="utf-8")
    legacy_db = tmp_path / "koder.db"
    conn = sqlite3.connect(legacy_db)
    conn.execute("CREATE TABLE demo (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO demo(name) VALUES ('kept')")
    conn.commit()
    conn.close()
    original_bytes = legacy_db.read_bytes()

    migrate_config_file(config_path, legacy_db_path=legacy_db)

    assert legacy_db.read_bytes() == original_bytes
