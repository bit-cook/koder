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

from koder_agent.harness.agents.service import AgentService


def test_agent_messages_preserve_order():
    service = AgentService.for_test()
    agent_id = service.spawn("default")
    service.send(agent_id, "one")
    service.send(agent_id, "two")
    assert [message.content for message in service.read_mailbox(agent_id)] == ["one", "two"]
