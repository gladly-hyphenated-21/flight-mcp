"""
Flight Scanner MCP Server.

Exposes two tools:

- `find_flights_to_europe`: cheapest flights from a given origin (e.g. BOS, JFK)
  to every European airport over a date range. Great for "find me a cheap trip
  to Europe in June" prompts.
- `find_flights_from_europe`: cheapest return flights into a given home airport
  from every European airport over a date range.

Plus two helpers:

- `list_european_destinations`: the catalog of airport codes the scanner can hit.
- `list_european_countries`: the country labels usable in the `countries` filter.

Deployed to Natoma via:

    Procfile: web: uvicorn server:application --host 0.0.0.0 --port $PORT ...

`fast_flights` does the scraping; we don't talk to any auth-gated API, so no
secrets are required.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any, Dict, List, Optional

import uvicorn
from fast_flights import Passengers
from fastmcp import FastMCP

from airports import EURO_AIRPORTS, all_countries, filter_by_country
from flightwrapper import FlightInfo, FlightScanner


mcp = FastMCP(
    name="Cheap Europe Flights",
    instructions=(
        "Find cheap flights between a US (or any) origin and European "
        "destinations over a date range. The scanner sweeps every European "
        "airport in parallel and returns the cheapest results, sorted by "
        "price. Use `find_flights_to_europe` for outbound trips and "
        "`find_flights_from_europe` for return legs."
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"`{field}` must be in YYYY-MM-DD format, got {value!r}"
        ) from exc


def _select_destinations(
    countries: Optional[List[str]],
    destinations: Optional[List[str]],
) -> List[List[str]]:
    """Build the destination list given optional filters.

    - If `destinations` is provided, use just those codes (matched against the
      catalog so we keep their human-readable names).
    - Else if `countries` is provided, filter the catalog by country.
    - Else use the full catalog.
    """
    if destinations:
        wanted = {code.strip().upper() for code in destinations}
        catalog = {entry[0]: entry for entry in EURO_AIRPORTS}
        result: List[List[str]] = []
        for code in wanted:
            if code in catalog:
                result.append(catalog[code])
            else:
                # Unknown code — pass through with the code as the name.
                result.append([code, code])
        return result

    if countries:
        filtered = filter_by_country(countries)
        if not filtered:
            raise ValueError(
                f"No airports matched countries={countries}. "
                "Call list_european_countries for valid options."
            )
        return filtered

    return EURO_AIRPORTS


def _build_passengers(
    adults: int,
    children: int,
    infants_in_seat: int,
    infants_on_lap: int,
) -> Passengers:
    if adults < 1:
        raise ValueError("`adults` must be at least 1.")
    return Passengers(
        adults=adults,
        children=children,
        infants_in_seat=infants_in_seat,
        infants_on_lap=infants_on_lap,
    )


def _summarize(flights: List[FlightInfo], max_results: int) -> Dict[str, Any]:
    """Trim to `max_results` and add a small summary the agent can quote."""
    trimmed = flights[: max(1, max_results)]
    by_dest: Dict[str, int] = {}
    for f in trimmed:
        # Track cheapest price seen per destination across the trimmed slice.
        existing = by_dest.get(f.destination_name)
        if existing is None or f.price_value < existing:
            by_dest[f.destination_name] = f.price_value

    cheapest = trimmed[0] if trimmed else None
    return {
        "total_found": len(flights),
        "returned": len(trimmed),
        "cheapest": cheapest.to_dict() if cheapest else None,
        "cheapest_per_destination": [
            {"destination_name": name, "price_value": price}
            for name, price in sorted(by_dest.items(), key=lambda kv: kv[1])
        ],
        "flights": [f.to_dict() for f in trimmed],
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool
def find_flights_to_europe(
    origin: str,
    start_date: str,
    end_date: str,
    max_results: int = 25,
    countries: Optional[List[str]] = None,
    destinations: Optional[List[str]] = None,
    adults: int = 1,
    children: int = 0,
    infants_in_seat: int = 0,
    infants_on_lap: int = 0,
    seat: str = "economy",
) -> Dict[str, Any]:
    """Search cheap one-way flights from `origin` to European destinations.

    Sweeps every European airport (or a filtered subset) for every day in the
    `[start_date, end_date]` window in parallel, then returns the cheapest
    `max_results` flights, sorted by price ascending. Use this for prompts
    like "find me a cheap trip to Europe in June" — pass the user's home
    airport as `origin` and the month's bounds as the date range.

    Args:
        origin: IATA code of the departure airport (e.g. "BOS", "JFK", "LAX").
        start_date: First date to check, "YYYY-MM-DD".
        end_date: Last date to check (inclusive), "YYYY-MM-DD".
        max_results: How many cheapest flights to return. Default 25. The
            scanner may internally find hundreds; this just trims the response.
        countries: Optional list of country names to limit the search to
            (e.g. ["France", "Italy", "Spain"]). Match the labels returned by
            `list_european_countries`.
        destinations: Optional list of specific IATA codes to limit the
            search to (e.g. ["CDG", "FCO", "BCN"]). Overrides `countries`.
        adults: Number of adults (default 1, minimum 1).
        children: Number of children. Default 0.
        infants_in_seat: Number of infants in their own seat. Default 0.
        infants_on_lap: Number of infants on a lap. Default 0.
        seat: Cabin class ("economy", "premium-economy", "business", "first").

    Returns:
        A dict with `total_found`, `returned`, `cheapest`, the cheapest price
        per destination, and the trimmed list of flight dicts.
    """
    start = _parse_date(start_date, "start_date")
    end = _parse_date(end_date, "end_date")
    if end < start:
        raise ValueError("`end_date` must be on or after `start_date`.")

    dests = _select_destinations(countries, destinations)
    passengers = _build_passengers(adults, children, infants_in_seat, infants_on_lap)

    scanner = FlightScanner(passengers=passengers, seat=seat)
    flights = scanner.flyFromCity(
        origin_airport=origin.strip().upper(),
        destination_airports=dests,
        start_date=start,
        end_date=end,
        save_to_disk=False,
    )
    return _summarize(flights, max_results)


@mcp.tool
def find_flights_from_europe(
    destination: str,
    start_date: str,
    end_date: str,
    max_results: int = 25,
    countries: Optional[List[str]] = None,
    origins: Optional[List[str]] = None,
    adults: int = 1,
    children: int = 0,
    infants_in_seat: int = 0,
    infants_on_lap: int = 0,
    seat: str = "economy",
) -> Dict[str, Any]:
    """Search cheap one-way flights from European airports back to `destination`.

    The mirror of `find_flights_to_europe` — use this to price a return leg.
    Sweeps every European airport (or a filtered subset) for every day in
    `[start_date, end_date]`, returns the cheapest `max_results` flights.

    Args:
        destination: IATA code of the arrival airport (e.g. "BOS", "JFK").
        start_date: First date to check, "YYYY-MM-DD".
        end_date: Last date to check (inclusive), "YYYY-MM-DD".
        max_results: How many cheapest flights to return. Default 25.
        countries: Optional list of European country names to limit origins.
        origins: Optional list of specific IATA codes to limit origins
            (overrides `countries`).
        adults: Number of adults (default 1, minimum 1).
        children: Number of children. Default 0.
        infants_in_seat: Number of infants in their own seat. Default 0.
        infants_on_lap: Number of infants on a lap. Default 0.
        seat: Cabin class ("economy", "premium-economy", "business", "first").

    Returns:
        A dict with `total_found`, `returned`, `cheapest`, the cheapest price
        per origin, and the trimmed list of flight dicts.
    """
    start = _parse_date(start_date, "start_date")
    end = _parse_date(end_date, "end_date")
    if end < start:
        raise ValueError("`end_date` must be on or after `start_date`.")

    sources = _select_destinations(countries, origins)
    passengers = _build_passengers(adults, children, infants_in_seat, infants_on_lap)

    scanner = FlightScanner(passengers=passengers, seat=seat)
    flights = scanner.flyToCity(
        destination_airport=destination.strip().upper(),
        origin_airports=sources,
        start_date=start,
        end_date=end,
        save_to_disk=False,
    )
    return _summarize(flights, max_results)


@mcp.tool
def list_european_destinations() -> List[Dict[str, str]]:
    """Return the catalog of European airports the scanner can search.

    Use this to discover valid IATA codes for the `destinations` / `origins`
    arguments of the flight-search tools, or to show the user what's available.
    """
    return [{"code": code, "name": name} for code, name in EURO_AIRPORTS]


@mcp.tool
def list_european_countries() -> List[str]:
    """Return the sorted list of country labels usable in the `countries` filter.

    Match these exactly (case-insensitive) when passing the `countries` arg to
    the flight-search tools.
    """
    return all_countries()


# ---------------------------------------------------------------------------
# ASGI app — Natoma's Procfile points uvicorn at `application`.
# ---------------------------------------------------------------------------

application = mcp.streamable_http_app(path="/mcp")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(
        application,
        host="0.0.0.0",
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
