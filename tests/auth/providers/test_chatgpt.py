import asyncio

from koder_agent.auth.providers.chatgpt import ChatGPTOAuthLLM


def test_chatgpt_request_preserves_max_reasoning_effort():
    llm = ChatGPTOAuthLLM()

    body = asyncio.run(
        llm._build_request_body(
            "gpt-5.1-codex-mini",
            [],
            reasoning={"effort": "max"},
        )
    )

    assert body["reasoning"]["effort"] == "max"
