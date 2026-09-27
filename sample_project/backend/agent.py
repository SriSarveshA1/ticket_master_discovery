"""
Agent factory.

THE MOST IMPORTANT FILE TO UNDERSTAND. Read alongside docs/05 and docs/07.

WHAT `create_agent` BUILDS
--------------------------
LangChain 1.x's `create_agent` compiles a LangGraph state machine that runs the
classic tool-calling ("ReAct") loop:

        ┌──────────────────────────────────────────────┐
        │  state = {"messages": [...]}                 │
        └──────────────────────────────────────────────┘
                     │
                     ▼
              ┌────────────┐   LLM sees system prompt + history + tool schemas
              │   model    │   and replies with EITHER plain text OR tool_calls
              └────────────┘
                     │
        tool_calls?  │  no  ──────────────────────────► END (return final AIMessage)
                     │ yes
                     ▼
              ┌────────────┐   runs each requested tool, appends ToolMessage(s)
              │   tools    │   to state, then loops back to `model`
              └────────────┘

The LLM decides *which* tool, *what arguments*, and *whether another call is
needed* -- there is no if/else on the user's text anywhere in this project.

THREE ADD-ONS MAKE IT A REAL APP
--------------------------------
1. `system_prompt`  -> behaviour and formatting rules (prompts/system_prompt.py)
2. `checkpointer`   -> saves the state after every step, keyed by `thread_id`.
                       Pass the same thread_id (= our session_id) and the whole
                       history is restored => conversational memory for free.
3. `HumanInTheLoopMiddleware` -> runs AFTER the model node. If the model asked
                       for a tool in `interrupt_on`, it calls `interrupt()`:
                       the graph stops, state is checkpointed, and `invoke`
                       returns `{"__interrupt__": [...]}`. Later, invoking the
                       SAME thread with `Command(resume={"decisions": [...]})`
                       continues exactly where it stopped.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from backend.config import settings
from backend.prompts.system_prompt import build_system_prompt
from backend.tools import ALL_TOOLS, TOOLS_REQUIRING_APPROVAL

logger = logging.getLogger(__name__)


def describe_action(tool_call: dict[str, Any], state: Any, runtime: Any) -> str:
    """
    Text shown to the human when approval is requested.

    The middleware calls this with the tool call the LLM proposed. We format the
    *arguments* into the confirmation card the assignment asks for
    ("You selected: ... Continue?"). Because the tool has not run yet, this is
    the only information we have -- which is exactly why proceed_to_borrow takes
    title/author/year as arguments.
    """
    args = tool_call.get("args", {})
    year = f" ({args['year']})" if args.get("year") else ""
    return (
        "You selected:\n"
        f"  Title:  {args.get('title', 'Unknown title')}\n"
        f"  Author: {args.get('author', 'Unknown author')}{year}\n"
        f"  Id:     {args.get('work_key', '?')}\n\n"
        "Continue to Open Library to read or borrow this book?"
    )


def build_checkpointer() -> BaseCheckpointSaver:
    """
    Where conversation state is stored.

    memory -> fast, zero setup, lost on restart (fine for the core assignment).
    sqlite -> survives restarts (Bonus 5). See main.py for the async variant
              that must be opened inside the app lifespan.
    """
    return InMemorySaver()


def build_agent(
    model: BaseChatModel | str | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """
    Assemble the agent. Parameters exist so tests can inject a fake LLM and a
    fresh checkpointer -- dependency injection, nothing fancy.
    """
    if model is None:
        # "openai:gpt-4o-mini" -> ChatOpenAI(model="gpt-4o-mini"); reads OPENAI_API_KEY from env.
        try:
            model = init_chat_model(settings.llm_model)
        except Exception as exc:  # noqa: BLE001 - fail fast with a helpful message at startup
            raise RuntimeError(
                f"Could not initialise LLM '{settings.llm_model}'. "
                "Check LLM_MODEL and the provider API key (e.g. OPENAI_API_KEY) in your .env file. "
                f"Underlying error: {type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
            ) from exc

    hitl = HumanInTheLoopMiddleware(
        interrupt_on={
            name: {
                # We only offer approve/reject to keep the UI simple. The
                # middleware also supports "edit" (change args) and "respond".
                "allowed_decisions": ["approve", "reject"],
                "description": describe_action,
            }
            for name in TOOLS_REQUIRING_APPROVAL
        },
    )

    agent = create_agent(
        model,
        tools=ALL_TOOLS,
        system_prompt=build_system_prompt(),
        middleware=[hitl],
        checkpointer=checkpointer or build_checkpointer(),
        name="book-discovery-agent",
    )
    logger.info(
        "Agent built with %d tools (%d need approval): %s",
        len(ALL_TOOLS),
        len(TOOLS_REQUIRING_APPROVAL),
        ", ".join(sorted(TOOLS_REQUIRING_APPROVAL)),
    )
    return agent
