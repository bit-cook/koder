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

from koder_agent.harness.memory.transcript_store import TranscriptStore


def test_transcript_store_writes_runtime_session_without_touching_legacy_db(tmp_path):
    store = TranscriptStore.for_test(tmp_path)
    session_id = store.create_session("demo")
    store.append_user_message(session_id, "hello")

    messages = store.read_messages(session_id)
    assert messages[0].content == "hello"
    assert [message.role for message in messages] == ["user"]
    assert store.runtime_db_path.exists()


def test_transcript_store_persists_across_restart(tmp_path):
    store = TranscriptStore.for_test(tmp_path)
    session_id = store.create_session("demo")
    store.append_user_message(session_id, "hello")
    store.append_assistant_message(session_id, "world")
    store.close()

    reopened = TranscriptStore.for_test(tmp_path)
    messages = reopened.read_messages(session_id)

    assert [message.content for message in messages] == ["hello", "world"]


def test_transcript_store_rolls_back_failed_write(tmp_path):
    store = TranscriptStore.for_test(tmp_path)
    session_id = store.create_session("demo")
    store.append_user_message(session_id, "first")

    class Unserializable:
        pass

    try:
        store.append_message(session_id, "assistant", "second", metadata={"bad": Unserializable()})
    except TypeError:
        pass
    else:
        raise AssertionError("expected append_message to fail on unserializable metadata")

    messages = store.read_messages(session_id)
    assert [message.content for message in messages] == ["first"]
