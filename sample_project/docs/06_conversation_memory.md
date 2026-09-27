# 06 — Conversational Context (Memory)

**Files:** `backend/agent.py` (checkpointer), `backend/sessions/manager.py` (`_config`), `backend/models/schemas.py` (`session_id`)

The mechanism below is "save and reload messages by id". For what a session *is*, why HTTP needs one, and how it differs from Streamlit's `st.session_state`, read [15 — Sessions](15_sessions.md).

## The problem

LLMs are stateless. Every call starts from nothing. "Tell me more about number 2" is
meaningless unless the model is shown the earlier turn where it listed the books.

## The solution: one line

```python
agent = create_agent(..., checkpointer=InMemorySaver())
```

and on every call:

```python
config = {"configurable": {"thread_id": session_id}}
await agent.ainvoke({"messages": [HumanMessage(text)]}, config=config)
```

That is it. The checkpointer:

1. **Before** the run: loads the saved state for `thread_id` (the whole message list).
2. Appends your new `HumanMessage`.
3. Runs the loop (model ↔ tools).
4. **After** every step: saves the new state under `thread_id`.

Next request with the same `thread_id` sees the entire history. Different `thread_id` → a
fresh, isolated conversation.

## `session_id` == `thread_id`

```python
# sessions/manager.py
def _config(session_id):
    return {"configurable": {"thread_id": session_id}, ...}
```

The frontend picks a `session_id` once (Streamlit: `st.session_state.session_id`) and sends
it with every request. The backend uses it verbatim as the LangGraph `thread_id`. Two users
with two ids never see each other's history. One user reloading with the same id gets their
history back (`GET /sessions/{id}`).

## What "context" the model actually has

After three turns the model receives, on the fourth, roughly:

```
SystemMessage   (prompt with rules)
HumanMessage    "Find sci-fi classics"
AIMessage       tool_calls=[search_books(...)]
ToolMessage     '[{"work_key":"OL893414W","title":"Dune",...},{"work_key":"OL46125W","title":"Foundation",...}]'
AIMessage       "1. **Dune** by Frank Herbert (1965)\n2. **Foundation** by Isaac Asimov (1951)"
HumanMessage    "Tell me more about number 2"
```

Notice the `ToolMessage` is still there. The model can see the **raw ids** it got earlier,
so it can call `get_book_details(work_key="OL46125W")` for "number 2". That is why:

* results must be numbered (system prompt rule), and
* results must carry ids (service model design).

"What about fantasy?" works the same way — the model sees the previous request was about
sci-fi *books* and infers "fantasy books".

## Inspecting and managing state

```python
state = await agent.aget_state(config)
state.values["messages"]   # the history
state.next                 # () if idle, ('HumanInTheLoopMiddleware.after_model',) if paused
state.interrupts           # pending approval requests (doc 07)

await checkpointer.adelete_thread(session_id)   # DELETE /sessions/{id}
```

`SessionManager.history()` reads `state.values["messages"]` and keeps only `HumanMessage`s
and `AIMessage`s with text — the tool chatter is internal.

## Memory backends

| Backend | Survives restart | Setup | When |
|---|---|---|---|
| `InMemorySaver` | no | none | core assignment, tests |
| `AsyncSqliteSaver` | yes | `pip install langgraph-checkpoint-sqlite` | Bonus 5 — see `main.py` lifespan |
| Postgres / Redis savers | yes | a server | production |

Because the checkpointer is an argument to `build_agent`, switching is a config change
(`CHECKPOINT_BACKEND=sqlite`), not a code change.

## Long conversations

Every turn sends the whole history to the model. After 30 turns with big tool outputs this
gets expensive. Mitigations, in order of simplicity: keep tool outputs small (doc 03), cap
`limit`, and if needed add LangChain's `SummarizationMiddleware` to `create_agent`. Not
required for the assignment; know that it exists.

## Exercises

1. Start two conversations with different `session_id`s in two browser tabs. Confirm they
   do not leak into each other.
2. Call `GET /sessions/{id}` after a search. Count the messages. Now temporarily remove the
   `checkpointer=` argument in `build_agent` and try a follow-up question. Observe the
   failure. Restore it.
3. Install `langgraph-checkpoint-sqlite`, set `CHECKPOINT_BACKEND=sqlite`, chat, restart the
   backend, and call `GET /sessions/{id}`. Your history is still there.
