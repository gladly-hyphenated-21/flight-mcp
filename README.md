# flight-mcp
Google Flights MCP for Subconscious/Natoma Hackathon May 2026

A custom MCP server that finds cheap one-way flights between a user-supplied
origin airport and any (or all) European airports across a date range. Wraps
the `fast_flights` library and exposes it as Streamable HTTP MCP tools so
Natoma-connected agents can answer prompts like:

> "Find me flights to Europe in June."

## Tools

| Tool | Purpose |
|------|---------|
| `find_flights_to_europe` | Cheapest flights from `origin` to every European airport over a date range. |
| `find_flights_from_europe` | Cheapest return flights from every European airport into `destination`. |
| `list_european_destinations` | Catalog of European IATA codes the scanner supports. |
| `list_european_countries` | Country labels usable for the `countries` filter. |

Both search tools accept:

- `start_date`, `end_date` — `YYYY-MM-DD`, inclusive
- `max_results` — trim the response (default 25)
- `countries` / `destinations` (or `origins`) — optional filters
- `adults`, `children`, `infants_in_seat`, `infants_on_lap`, `seat`

The response always includes a `cheapest` flight, a `cheapest_per_destination`
summary, and the trimmed flight list sorted ascending by price.

## Layout

```
flight-mcp/
├── server.py          FastMCP app + 4 tools, exposes `application` (ASGI)
├── flightwrapper.py   FlightScanner (parallel fast_flights wrapper)
├── jsonhandler.py     Disk JSON writer — used only by the legacy CLI path
├── airports.py        EURO_AIRPORTS catalog + filter helpers
├── requirements.txt
├── Procfile
└── .python-version
```

  **Cold start.** A full-Europe sweep can take 30–60 s. The scanner is
  concurrent (25 workers by default) but `fast_flights` rate-limits, so
  the first request after a cold start may be slow.
