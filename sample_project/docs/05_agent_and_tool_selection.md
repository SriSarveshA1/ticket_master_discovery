# 05 — The Agent: How the LLM Decides Which Tool to Call

**Files:** `backend/agent.py`, `backend/prompts/system_prompt.py`

## The loop

There is no `if/else` on user text anywhere in this project. Instead, `create_agent` builds
this loop (LangChain 1.x compiles it to a LangGraph state machine):

```
             ┌──────────────────────────────┐
   input ───►│ model: LLM reads              │
             │  - system prompt              │
             │  - full message history       │
             │  - JSON schema of every tool  │
             └───────────┬──────────────────┘
                         │ AIMessage
        ┌────────────────┴─────────────────┐
        │ has tool_calls?                  │
        │  no  -> return final text  (END) │
        │  yes -> run tools               ─┼──► ToolMessage(s) appended ──► back to model
        └──────────────────────────────────┘
```

The LLM output is either plain text (done) or a structured list of `tool_calls`:

```python
AIMessage(content="", tool_calls=[{"name": "search_books", "args": {"query": "sci-fi", "limit": 5}, "id": "call_abc"}])
```

The framework executes the named tool with those args, appends the result as a
`ToolMessage`, and calls the model again. The model may call another tool or answer.

So the four decisions the assignment lists are all made by the LLM:


| Decision                       | How the model makes it                                             |
| ------------------------------ | ------------------------------------------------------------------ |
| Which tool                     | From tool names + descriptions (doc 04) + system prompt hints      |
| What parameters                | From the parameter schema and the conversation                     |
| Whether another call is needed | After seeing the `ToolMessage`, it decides to call again or answer |
| How to present results         | System prompt formatting rules                                     |

## Building the agent

```python
agent = create_agent(
    model,                              # init_chat_model("openai:gpt-4o-mini")
    tools=ALL_TOOLS,
    system_prompt=build_system_prompt(),
    middleware=[hitl],                  # doc 07
    checkpointer=InMemorySaver(),       # doc 06
)
```

`model` must support **tool calling** (all modern OpenAI/Anthropic/Gemini chat models do).
`init_chat_model("provider:model")` lets you switch providers via `.env`.

## What actually makes tool selection good

You control tool selection through three levers, in order of impact:

### Lever 1 — Tool descriptions (doc 04)

Distinct, example-rich docstrings. The model matches the user's intent to them.

### Lever 2 — The system prompt (`system_prompt.py`)

Where you resolve ambiguity between tools and set behaviour the tools cannot:

```
- Choose the most specific tool: a genre/period -> `search_books_by_subject`; a title/topic
  -> `search_books`; a writer's biography -> `search_authors` then `get_author_details`;
  "tell me more about #N" -> `get_book_details` with that book's work_key.
- You may call several tools in a row if a question needs it.
- Present search results as a NUMBERED list ...
- NEVER say the book "has been borrowed" ...
```

Read the whole prompt. Every line exists because of a specific behaviour we want or a
specific failure we are preventing. Prompts are code — keep them in a file, version them,
review them.

### Lever 3 — Injected runtime facts

`build_system_prompt(today)` puts today's date in the prompt. Books barely need it; an
**events** assistant absolutely does — "this weekend" is meaningless to a model without
today's date. See doc 11.

## Multi-step reasoning, for free

"Who wrote number 2 and what else did they write?" typically produces:

1. `get_book_details(work_key=...)` → authors: ["Isaac Asimov"]
2. `search_authors(name="Isaac Asimov")` → author_key
3. `get_author_details(author_key=...)` → notable works
4. Final answer

You wrote none of that orchestration. You wrote three tools with good descriptions.
`settings.max_agent_steps` (→ `recursion_limit`) caps runaway loops.

## Debugging what the model decided

Set `LOG_LEVEL=DEBUG` and watch the service calls, or in a script:

```python
result = await agent.ainvoke({"messages": [HumanMessage("find dune")]}, config={"configurable": {"thread_id": "dbg"}})
for m in result["messages"]:
    print(type(m).__name__, m.content or m.tool_calls)
```

You will see `HumanMessage → AIMessage(tool_calls) → ToolMessage → AIMessage(text)`.
If the model picks the wrong tool, fix the *description* first, the prompt second.

## What `create_agent` replaces

Older tutorials use `AgentExecutor`, `initialize_agent`, or hand-built LangGraph
`StateGraph`s with a `should_continue` edge. `create_agent` is the current (v1) API and is
itself a compiled LangGraph graph — so everything LangGraph offers (checkpointers,
`interrupt`, streaming, `get_state`) works on it. If you want to see the graph:

```python
print(agent.get_graph().draw_ascii())
```

## Exercises

1. Remove the "Choose the most specific tool" paragraph from the system prompt. Ask for
   "recent fantasy books". Which tool gets called now? Put it back.
2. Ask "what is the capital of France?" — no tool should be called. Then ask the agent to
   "book a flight". Watch it explain it cannot. That is the model choosing *no tool*.
3. Print `agent.get_graph().draw_ascii()` and find the `HumanInTheLoopMiddleware.after_model`
   node. That is where doc 07 happens.


