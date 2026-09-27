# 14 — The Other Bonus Challenges (Not Implemented — How You Would)

Bonus 5 (persistence) and Bonus 6 (streaming) are implemented and documented in docs 12
and 13. The remaining four are small once the core is solid. Sketches, not code.

## Bonus 1 — Event images

*Backend:* keep `image_url` in `EventSummary` (Ticketmaster `images[]`, pick `ratio ==
"16_9"`). It is already there in the sample as `cover_url`.
*Frontend:* the model's text answer does not carry URLs reliably. Two options:
* after each `/chat`, call `GET /events/{id}` for the ids mentioned… (you would need the ids
  → add an `events: list[EventSummary]` field to `ChatResponse` populated from the last
  `ToolMessage` of a search tool, like `_extract_handoff_url` does for the URL), or
* simplest: pass `image_url` as an argument to `proceed_to_booking` and show it on the
  approval card via `pending_action.arguments`.

## Bonus 2 — Geolocation search ("within 10 miles of Wembley")

Ticketmaster supports `latlong=51.556,-0.279&radius=10&unit=miles`. Add
`search_events_near(place: str, radius_miles: int)`:
1. `search_venues(keyword=place)` → first venue's `location.latitude/longitude`
2. `GET /events.json?latlong=...&radius=...`
Two service calls inside one tool is fine — the tool is still "one job" from the LLM's view.

## Bonus 3 — Better recommendations ("rock, budget £80, Saturday evening")

Nothing new to build. Ensure results carry `genre`, `price_min`, `date`, `time`, ask for
`size=10`, and add to the system prompt: *"When the user states preferences (genre, budget,
time), retrieve a broader set and then reason about which options fit; explain why."* The
LLM does the filtering in its answer. Optionally add `classificationName` and
`startDateTime`/`endDateTime` params to `search_events` so it can pre-filter.

## Bonus 4 — Event comparison ("compare 1 and 3")

Also mostly prompt + memory. The model already has both events' JSON in its history
(doc 06). Add: *"When asked to compare, call `get_event_details` for each if you lack
details, then present a table with price, venue, date, distance, category."* If you want
distance, both venues have `latitude/longitude` — a `haversine()` helper in the service and
a `distance_between_venues(venue_id_a, venue_id_b)` tool.

## Priorities

If you have time for two: **Bonus 5** (already done pattern, high marks-per-hour) and
**Bonus 3** (pure prompt work, impressive in a demo). Bonus 6 is done too. Images are quick
if you take the approval-card route. Geolocation needs the most new code.
