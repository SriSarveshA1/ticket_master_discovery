# 07 — Human-in-the-Loop (HITL)

**Files:** `backend/agent.py` (middleware + `describe_action`), `backend/sessions/manager.py`, `backend/tools/action_tools.py`, `backend/api/routes.py`

This is the assignment's Core Requirement 7 and the most conceptually new part. Read slowly.

## The requirement, restated

When the model decides to call `proceed_to_booking` (here `proceed_to_borrow`):

1. **Do not run it.**
2. Show the human what is about to happen ("You selected: Coldplay, Wembley, 18 Sep…").
3. **Stop.** Return to the frontend. Wait indefinitely.
4. When the human clicks Approve → run the tool → the model tells the user the URL.
   When they click Reject → do not run it → the model acknowledges.

"Wait indefinitely" across *separate HTTP requests* is the hard part. The graph has to
freeze mid-run, survive until the next request, and continue exactly where it stopped.

## The mechanism: `interrupt()` + checkpointer + `Command(resume=...)`

LangGraph gives us three primitives:

| Primitive | Meaning |
|---|---|
| `interrupt(value)` (called inside a node) | "Stop the graph here. Save state. Hand `value` to the caller." |
| checkpointer | The saved state (doc 06) — this is what makes the pause survive across requests |
| `Command(resume=answer)` (passed to `invoke`) | "Continue the paused graph; the `interrupt()` call returns `answer`." |

You *could* write the interrupting node yourself. LangChain ships one:
`HumanInTheLoopMiddleware`. We configure it in `agent.py`:

```python
hitl = HumanInTheLoopMiddleware(
    interrupt_on={
        "proceed_to_borrow": {
            "allowed_decisions": ["approve", "reject"],
            "description": describe_action,        # formats the "You selected:" card
        }
    },
)
agent = create_agent(model, tools=ALL_TOOLS, middleware=[hitl], checkpointer=...)
```

The middleware hooks in **after the model node**. When the model emits a tool call whose
name is in `interrupt_on`, it calls `interrupt(...)` instead of letting the tool run.

## The full lifecycle, step by step

### Step A — user chooses ("I want to read number 2")

```
POST /chat  -->  manager.chat()  -->  agent.ainvoke({"messages":[Human]})
    model -> AIMessage(tool_calls=[proceed_to_borrow(work_key=..., title="Foundation", ...)])
    HumanInTheLoopMiddleware.after_model sees "proceed_to_borrow" in interrupt_on
        -> calls interrupt({"action_requests":[{"name","args","description"}], "review_configs":[...]})
    graph STOPS. state checkpointed. ainvoke returns:
        {"messages": [...], "__interrupt__": [Interrupt(id="b40f...", value={...})]}
```

`SessionManager._build_turn` sees `__interrupt__` and returns:

```json
{
  "status": "awaiting_approval",
  "response": "You selected:\n  Title:  Foundation\n  Author: Isaac Asimov (1951)\n ...\nContinue to Open Library?",
  "pending_action": {"action_id": "b40f...", "tool_name": "proceed_to_borrow", "arguments": {...}, "description": "..."},
  "handoff_url": null
}
```

Key points:
* No tool ran. `handoff_url` is null. **The URL is not revealed before approval** — exactly
  what the assignment's "HITL Requirement" page demands.
* `action_id` is the interrupt's id. The frontend must echo it back.
* `response` is the description produced by `describe_action()` from the tool call's
  **arguments** — the tool has not run, so arguments are all we have. That is why
  `proceed_to_borrow` takes `title`/`author`/`year`.

### Step B — frontend shows Approve / Reject

`streamlit_app.py` sees `status == "awaiting_approval"`, stores `pending_action`, renders
the card and two buttons. Nothing else happens. Minutes can pass. The backend holds no
open connection — the paused state is just data in the checkpointer.

### Step C1 — Approve

```
POST /approve {session_id, action_id}
    manager.approve():
        state = agent.aget_state(config)         # is there really a pending interrupt?
        assert state.interrupts[0].id == action_id   # is it THIS one? (409 otherwise)
        agent.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
```

The graph wakes up inside the middleware; `interrupt()` returns our decision; the middleware
lets the tool call through → `proceed_to_borrow` runs → `ToolMessage({"status":"approved","url":...})`
→ model writes "You approved Foundation. Continue on Open Library: https://…" → END.

`SessionManager._extract_handoff_url` pulls the URL from that `ToolMessage` so the API
returns it as a proper field, not just buried in prose:

```json
{"status": "completed", "response": "You approved Foundation. Continue on Open Library: ...", "handoff_url": "https://openlibrary.org/works/OL46125W"}
```

### Step C2 — Reject

```python
agent.ainvoke(Command(resume={"decisions": [{"type": "reject", "message": "The user REJECTED this action. Do not proceed. Ask what they would like instead."}]}), config)
```

The middleware does **not** run the tool. Instead it inserts a `ToolMessage` whose content
is our `message`, so the model sees "user rejected" and responds gracefully. `handoff_url`
stays null. Phrase that message *for the model* — it is model-facing text.

## Why `action_id` matters

Without it, a stale Approve button (user clicked after the conversation moved on, or a
double-click) could approve the *wrong* action. `_assert_pending` checks the id against the
live interrupt and returns **409 Conflict** on mismatch or when nothing is pending.

## Edge case: new message while approval is pending

User sees the card, ignores it, types "actually show me fantasy". The graph is still paused,
so we cannot just push a new message. `SessionManager.chat()` resolves this by auto-rejecting
the pending action first, then processing the new message. The alternative (return 409 and
make the frontend force a decision) is equally valid — pick one and document it. The test
`test_new_message_while_pending_auto_rejects` pins the behaviour.

## Alternative implementation (no LangGraph interrupt)

The assignment allows a simpler design: the action tool does *not* return the URL; it
records a `PendingBooking` in a dict keyed by `session_id` and returns "awaiting approval".
`/approve` looks the record up and returns the URL directly, then optionally sends a
synthetic message to the agent. This works, but:

* the agent's own history does not know approval happened (unless you inject a message),
* you maintain a second state store beside the checkpointer,
* you cannot use `edit`/`respond` decisions.

The interrupt approach keeps *one* source of truth (the graph state). Understand both;
implement the interrupt one.

## Decision types you did not use (but should know)

`allowed_decisions` can also include:
* `"edit"` — human changes the tool arguments before it runs
  (`{"type":"edit","edited_action":{"name":..., "args":{...}}}`).
* `"respond"` — human answers *instead of* running the tool; the text becomes the ToolMessage.

## Exercises

1. In `agent.py` change `allowed_decisions` to `["approve", "edit", "reject"]`. Add a
   `POST /edit` endpoint that resumes with an `edit` decision swapping the `work_key`. Watch
   the tool run with the edited args.
2. Remove `"description": describe_action` and look at the default description the
   middleware generates. Why is ours better for a UI?
3. Temporarily add `search_books` to `TOOLS_REQUIRING_APPROVAL`. Every search now pauses.
   Feel how annoying it is — that is why read-only tools auto-run.
4. Read `test_reject_does_not_reveal_url` in `tests/test_api_flow.py`. Which assertion
   encodes the assignment's "Only after Approve should your backend return the booking URL"?
