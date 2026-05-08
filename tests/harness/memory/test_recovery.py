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

from koder_agent.harness.memory.recovery import recover_partial_write
from koder_agent.harness.memory.transcript_store import TranscriptStore


def test_recover_partial_write_restores_last_known_good_state(tmp_path):
    runtime_db = tmp_path / "runtime.db"
    backup_db = tmp_path / "runtime.db.bak"

    store = TranscriptStore(runtime_db_path=runtime_db, legacy_db_path=tmp_path / "legacy.db")
    session_id = store.create_session("demo")
    store.append_user_message(session_id, "hello")
    store.close()

    backup_db.write_bytes(runtime_db.read_bytes())
    runtime_db.write_text("not a sqlite db", encoding="utf-8")

    result = recover_partial_write(runtime_db, backup_db)

    assert result.recovered is True
    reopened = TranscriptStore(runtime_db_path=runtime_db, legacy_db_path=tmp_path / "legacy.db")
    assert reopened.read_messages(session_id)[0].content == "hello"


def test_recover_partial_write_is_noop_when_primary_db_is_healthy(tmp_path):
    runtime_db = tmp_path / "runtime.db"
    backup_db = tmp_path / "runtime.db.bak"

    store = TranscriptStore(runtime_db_path=runtime_db, legacy_db_path=tmp_path / "legacy.db")
    store.create_session("demo")
    store.close()

    result = recover_partial_write(runtime_db, backup_db)

    assert result.recovered is False
