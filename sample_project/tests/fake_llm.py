"""
A scripted fake chat model for tests.

CONCEPT: You can test the ENTIRE backend -- routes, session manager, HITL
interrupt/resume, tool execution -- without an LLM API key. The fake model just
replays a list of AIMessages you give it. Each call to the model pops the next
one. If a message has `tool_calls`, the agent will execute those tools for real
(so we also patch the service layer to avoid network; see conftest.py).

This is also a great debugging trick: script the exact tool call sequence you
expect and check that your plumbing handles it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk


class ScriptedLLM(GenericFakeChatModel):
    """GenericFakeChatModel that pretends to support tool binding and can stream."""

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001 - signature mirrors BaseChatModel
        # A real model would attach tool schemas here. We ignore them because
        # our replies are pre-scripted.
        return self

    def _stream(self, messages, stop=None, run_manager=None, **kwargs) -> Iterator[ChatGenerationChunk]:  # noqa: ANN001
        """
        Streaming version used by /chat/stream tests.

        Real models stream a text answer token by token, and stream a tool call
        as a single chunk carrying `tool_call_chunks`. We imitate both so the
        streaming code path is exercised exactly like production.
        """
        message = next(self.messages)
        if isinstance(message, str):
            message = AIMessage(content=message)

        if message.tool_calls:
            chunk = AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {"name": tc["name"], "args": json.dumps(tc["args"]), "id": tc["id"], "index": i}
                    for i, tc in enumerate(message.tool_calls)
                ],
            )
            yield ChatGenerationChunk(message=chunk)
            return

        # Split on whitespace but keep it, so tokens re-join into the original text.
        for token in re.split(r"(\s+)", str(message.content)):
            if not token:
                continue
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=token))
            if run_manager:
                run_manager.on_llm_new_token(token, chunk=chunk)
            yield chunk


def scripted(*messages: AIMessage) -> ScriptedLLM:
    return ScriptedLLM(messages=iter(messages))


def ai_text(text: str) -> AIMessage:
    return AIMessage(content=text)


def ai_tool_call(name: str, args: dict, call_id: str = "call-1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def replies(messages: Iterable[AIMessage]) -> ScriptedLLM:
    return ScriptedLLM(messages=iter(messages))
