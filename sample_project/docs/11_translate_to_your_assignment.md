# 11 — Translating This to Your Ticketmaster Assignment

You now understand every layer. This doc is the bridge: what to rename, what changes, what
is *specific* to events, and a checklist against the assignment's requirements.

## File-by-file mapping

| This sample | Your project | What changes |
|---|---|---|
| `services/openlibrary.py` | `services/ticketmaster.py` | **Most of the work.** New endpoints, `apikey` param, new result models, new parsing |
| `tools/book_tools.py` | `tools/event_tools.py` | `search_events`, `search_events_by_location`, `search_events_by_date`, `get_event_details` |
| `tools/author_tools.py` | `tools/venue_tools.py` | `search_venues`, `get_venue_details` |
| `tools/action_tools.py` | same | `proceed_to_booking(event_id, name, venue, date, time, price_from)` |
| `prompts/system_prompt.py` | same | Events persona; **date rules**; "never say booked" |
| `agent.py` | same | Rename `describe_action` fields; `interrupt_on={"proceed_to_booking": ...}` |
| `sessions/manager.py` | same | Rename `handoff_url` → `booking_url`; `TOOL_STATUS_LABELS` |
| `models/schemas.py` | same | Rename `handoff_url` → `booking_url` |
| `api/routes.py`, `main.py` | same | `/books/{id}` → `/events/{id}` |
| `config.py` | same | add `ticketmaster_api_key: str` (required, no default) |
| `frontend/` | same | labels and the link text |
| `tests/` | same | new fake payloads, same structure |

Everything in the middle (agent loop, memory, HITL, error mapping, SSE, SQLite) is
**unchanged**. That is the point of the layering.

## The Ticketmaster service layer

Base URL `https://app.ticketmaster.com/discovery/v2`, every request needs `apikey=`.

| Need | Endpoint | Useful params |
|---|---|---|
| search events | `GET /events.json` | `keyword`, `city`, `countryCode` (e.g. `GB`), `classificationName` (`music`, `sports`, `comedy`…), `startDateTime`, `endDateTime`, `size` (≤ 10 is plenty), `sort=date,asc`, `latlong`, `radius`, `unit=miles` |
| event details | `GET /events/{id}.json` | — |
| search venues | `GET /venues.json` | `keyword`, `city`, `countryCode` |
| venue details | `GET /venues/{id}.json` | — |

Two gotchas that will cost you an hour each if you do not know them:

1. **Date format is strict**: `startDateTime=2026-10-03T00:00:00Z` — ISO-8601, UTC `Z`,
   **no milliseconds**. Build it with `datetime.strftime("%Y-%m-%dT%H:%M:%SZ")`.
2. **Rate limit**: 5 requests/second, 5000/day on the free tier. HTTP 429 → add a
   `RateLimited(ExternalAPIError)` with a friendly `user_message`.

Response shape — the fields worth keeping (everything else is noise for the LLM):

```python
for ev in data.get("_embedded", {}).get("events", []):
    EventSummary(
        event_id   = ev["id"],
        name       = ev["name"],
        url        = ev.get("url"),                                   # <-- the booking URL
        date       = ev.get("dates", {}).get("start", {}).get("localDate"),
        time       = ev.get("dates", {}).get("start", {}).get("localTime"),
        venue      = (ev.get("_embedded", {}).get("venues") or [{}])[0].get("name"),
        city       = (ev.get("_embedded", {}).get("venues") or [{}])[0].get("city", {}).get("name"),
        genre      = (ev.get("classifications") or [{}])[0].get("genre", {}).get("name"),
        price_min  = (ev.get("priceRanges") or [{}])[0].get("min"),  # often missing! -> None
        price_max  = (ev.get("priceRanges") or [{}])[0].get("max"),
        currency   = (ev.get("priceRanges") or [{}])[0].get("currency"),
        image_url  = next((i["url"] for i in ev.get("images", []) if i.get("ratio") == "16_9"), None),
    )
```

`priceRanges` is frequently absent → the assignment's "No ticket pricing information
available" case. Keep it `None` and let the prompt tell the model to say so.

## Dates: the one genuinely new concept

Books do not care what day it is. Events do. "This weekend", "next Friday", "in October"
must become `startDateTime`/`endDateTime`. Two approaches — use both:

1. **Tell the model today's date** (already done in `build_system_prompt(today)`), and add
   rules: *"Convert relative dates to ISO dates before calling tools. 'This weekend' = the
   coming Saturday 00:00 to Sunday 23:59. If the user gives no date, do not filter by date."*
2. **Make tool parameters simple ISO dates**, not raw Ticketmaster strings:
   `search_events(keyword, city, start_date: "YYYY-MM-DD" | None, end_date: ...)` and let the
   *service* convert to `...T00:00:00Z`. LLMs are reliable at `YYYY-MM-DD`; less so at
   full ISO timestamps with `Z`.

Optionally add a tiny read-only tool `get_today()` for models that ignore the prompt date.

## Tool set to aim for

```python
READ_ONLY_TOOLS = [search_events, search_events_by_location, search_events_by_date,
                   get_event_details, search_venues, get_venue_details]
ACTION_TOOLS    = [proceed_to_booking]
```

`search_events_by_location` is the geolocation bonus in disguise: accept `latlong` +
`radius`, or a place name you geocode… or simply `city` + `radius` around the venue (see
Bonus 2 in doc 14).

## HITL, translated

```python
@tool
def proceed_to_booking(event_id: str, name: str, venue: str, date: str, time: str | None = None,
                       price_from: str | None = None, event_url: str = "") -> str:
    """Hand the user off to Ticketmaster to buy tickets for the SELECTED event.
    Call ONLY when the user has clearly chosen one event. Requires human approval.
    Returns the Ticketmaster purchase URL."""
    return json.dumps({"status": "approved", "url": event_url, ...})
```

`describe_action` becomes the assignment's exact card:

```
You selected:
  Event: Coldplay
  Venue: Wembley Stadium
  Date:  18 September, 7:30 PM
  Price: £85+
Continue to Ticketmaster to purchase tickets?
```

And the system prompt rule: *"After approval say: 'You have approved this event. Continue
to Ticketmaster to complete your purchase: <url>'. NEVER say the ticket is booked."*

Where does `event_url` come from? From the search result the model saw earlier (keep `url`
in `EventSummary`). The model passes it as an argument, exactly like `work_key` here.

## Requirements checklist

Tick each against your project, not this one:

- [ ] `POST /chat` with `{message, session_id}` → `{response, session_id, ...}`
- [ ] ≥ 4 distinct `@tool`s; HTTP only in `services/ticketmaster.py`; `requests`/`httpx`, no MCP
- [ ] LLM picks tools; **zero** `if "concert" in message` anywhere
- [ ] Follow-ups work: "number 2", "what about Manchester?", "only under £100"
      (numbered lists + ids in results + checkpointer)
- [ ] `proceed_to_booking` in `interrupt_on`; `/chat` returns `awaiting_approval` with the
      event card; `booking_url` is **null** until `/approve`
- [ ] `POST /approve`, `POST /reject` with `{session_id, action_id}`; 409 on stale/absent
- [ ] Never claims "booked"; final message contains the real Ticketmaster URL
- [ ] `.env` has `TICKETMASTER_API_KEY` and `OPENAI_API_KEY`; `.env` in `.gitignore`
- [ ] Friendly messages for: no events, API down, invalid city, no pricing, LLM down;
      correct HTTP codes (422/404/409/503); no tracebacks to the UI
- [ ] `GET /health`; optional `GET/DELETE /sessions/{id}`, `GET /events/{id}`
- [ ] Frontend → FastAPI only; Approve/Reject buttons; link shown only after approve
- [ ] Mandatory scenario runs end-to-end with the **real** API — record it

## Suggested build order (one evening each)

1. `config.py` + `services/ticketmaster.py` + a 10-line script that prints 5 events. Real key.
2. Tools + `agent.py` + a script that runs one `agent.ainvoke`. Watch the tool calls.
3. `schemas.py` + `routes.py` + `main.py`. Test `/chat` in `/docs`.
4. HITL: `proceed_to_booking`, middleware, `/approve`, `/reject`. Write the fake-LLM test first.
5. Streamlit. Then error handling pass. Then bonuses.

Rebuild from an empty folder. Keep this sample open for reference, not for copying.
