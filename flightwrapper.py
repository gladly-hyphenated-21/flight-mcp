"""
Flight scanner wrapper for convenient access to fast_flights results.

Lightly adapted from the original CLI version: file writes are now opt-in
via `save_to_disk=True` so the MCP server doesn't litter the working
directory on every search.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import concurrent.futures
import logging
import time
from typing import Iterable, List, Optional, Tuple

from fast_flights import FlightData, Passengers, get_flights


# --- Configuration ---
LOG_LEVEL = logging.INFO

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FlightInfo:
    origin: str
    destination: str
    travel_date: str
    price_raw: str
    price_value: int
    duration: str
    airline: str
    destination_name: str
    departure: str
    arrival: str
    stops: int
    is_best: bool

    def __str__(self) -> str:
        return (
            f'{self.price_raw}: {self.duration} on "{self.airline}" '
            f'from "{self.origin}" to "{self.destination_name}" '
            f"({self.departure} -> {self.arrival})"
        )

    def to_dict(self) -> dict:
        return {
            "origin": self.origin,
            "destination": self.destination,
            "travel_date": self.travel_date,
            "price_raw": self.price_raw,
            "price_value": self.price_value,
            "duration": self.duration,
            "airline": self.airline,
            "destination_name": self.destination_name,
            "departure": self.departure,
            "arrival": self.arrival,
            "stops": self.stops,
            "is_best": self.is_best,
        }

    def dedupe_key(self) -> Tuple[str, str, int, str, str, str, str, str, int]:
        return (
            self.origin.strip().upper(),
            self.destination.strip().upper(),
            self.price_value,
            self.duration.strip().lower(),
            self.airline.strip().lower(),
            self.destination_name.strip().lower(),
            self.departure.strip().lower(),
            self.arrival.strip().lower(),
            self.stops,
        )


def parse_price(price_str: str) -> int:
    if not price_str:
        return 999999
    digits = "".join(ch for ch in str(price_str) if ch.isdigit())
    return int(digits) if digits else 999999


def get_date_range(start_date: date, end_date: date) -> List[str]:
    delta = (end_date - start_date).days
    return [
        (start_date + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(delta + 1)
    ]


class FlightScanner:
    def __init__(
        self,
        passengers: Optional[Passengers] = None,
        seat: str = "economy",
        fetch_mode: str = "fallback",
        max_workers: int = 25,
    ) -> None:
        self.passengers = passengers or Passengers(
            adults=1, children=0, infants_in_seat=0, infants_on_lap=0
        )
        self.seat = seat
        self.fetch_mode = fetch_mode
        self.max_workers = max_workers

    def _is_valid_flight(self, flight: FlightInfo) -> bool:
        if flight.price_value <= 0:
            return False
        if not flight.airline.strip():
            return False
        if not flight.duration.strip():
            return False
        if not flight.departure.strip() or not flight.arrival.strip():
            return False
        return True

    def _iter_flights(
        self,
        origin_airport: str,
        destination_airport: str,
        travel_date: str,
        destination_name: Optional[str] = None,
    ) -> Iterable[FlightInfo]:
        f_data = FlightData(
            date=travel_date,
            from_airport=origin_airport,
            to_airport=destination_airport,
        )

        result = None
        for attempt in range(4):
            try:
                result = get_flights(
                    flight_data=[f_data],
                    trip="one-way",
                    seat=self.seat,
                    passengers=self.passengers,
                    fetch_mode=self.fetch_mode,
                )
                break
            except Exception as e:
                err_str = str(e).lower()
                is_rate_limit = (
                    "429" in err_str
                    or "too many" in err_str
                    or "rate" in err_str
                    or "401" in err_str
                    or "token" in err_str
                )
                if is_rate_limit and attempt < 3:
                    wait = 2**attempt * 5  # 5s, 10s, 20s
                    logger.warning(
                        "Rate limited on %s -> %s on %s, retrying in %ds (attempt %d/4)",
                        origin_airport,
                        destination_airport,
                        travel_date,
                        wait,
                        attempt + 1,
                    )
                    time.sleep(wait)
                else:
                    logger.error(
                        "Error searching %s -> %s on %s: %s",
                        origin_airport,
                        destination_airport,
                        travel_date,
                        e,
                    )
                    return []

        if not result or not result.flights:
            return []

        destination_label = destination_name or destination_airport

        cleaned_flights: List[FlightInfo] = []
        for flight in result.flights:
            parsed = FlightInfo(
                origin=origin_airport,
                destination=destination_airport,
                travel_date=travel_date,
                price_raw=str(flight.price),
                price_value=parse_price(str(flight.price)),
                duration=flight.duration,
                airline=flight.name,
                destination_name=destination_label,
                departure=flight.departure,
                arrival=flight.arrival,
                stops=flight.stops,
                is_best=flight.is_best,
            )
            if self._is_valid_flight(parsed):
                cleaned_flights.append(parsed)

        return cleaned_flights

    def _run_tasks(
        self, tasks: List[Tuple[str, str, str, Optional[str]]]
    ) -> List[FlightInfo]:
        flights: List[FlightInfo] = []
        total_tasks = len(tasks)
        completed = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_task = {
                executor.submit(
                    self._iter_flights, task[0], task[1], task[2], task[3]
                ): task
                for task in tasks
            }
            for future in concurrent.futures.as_completed(future_to_task):
                completed += 1
                origin_airport, destination_airport, travel_date = future_to_task[future][:3]
                logger.info(
                    "Checked %s -> %s on %s (%d/%d)",
                    origin_airport,
                    destination_airport,
                    travel_date,
                    completed,
                    total_tasks,
                )
                flights.extend(list(future.result()))
        return self._dedupe_flights(flights)

    def _dedupe_flights(self, flights: Iterable[FlightInfo]) -> List[FlightInfo]:
        unique: List[FlightInfo] = []
        seen: set[Tuple[str, str, int, str, str, str, str, str, int]] = set()
        for flight in flights:
            key = flight.dedupe_key()
            if key in seen:
                continue
            seen.add(key)
            unique.append(flight)
        return unique

    def _write_flights(self, file_name: str, flights: List[FlightInfo]) -> None:
        seen_lines: set[str] = set()
        with open(file_name + ".txt", "w", encoding="utf-8") as handle:
            for flight in flights:
                line = str(flight)
                if line in seen_lines:
                    continue
                seen_lines.add(line)
                handle.write(line + "\n")

    def save_flights(self, file_name: str, flights: List[FlightInfo]) -> None:
        """Write `.txt` and `.json` next to the script. CLI-only."""
        self._write_flights(file_name, flights)
        from jsonhandler import JsonHandler

        handler = JsonHandler(file_name)
        handler.write_flight_list(file_name, flights)

    def flyFromCity(
        self,
        origin_airport: str,
        destination_airports: List,
        start_date: date,
        end_date: date,
        save_to_disk: bool = False,
    ) -> List[FlightInfo]:
        tasks: List[Tuple[str, str, str, Optional[str]]] = []
        for travel_date in get_date_range(start_date, end_date):
            for destination_airport in destination_airports:
                if (
                    isinstance(destination_airport, (list, tuple))
                    and len(destination_airport) >= 2
                ):
                    destination_code = str(destination_airport[0])
                    destination_name = str(destination_airport[1])
                else:
                    destination_code = str(destination_airport)
                    destination_name = destination_code
                tasks.append(
                    (origin_airport, destination_code, travel_date, destination_name)
                )

        flights = sorted(self._run_tasks(tasks), key=lambda flight: flight.price_value)
        if save_to_disk:
            self.save_flights(f"{origin_airport} FlyFrom", flights)
        return flights

    def flyToCity(
        self,
        destination_airport: str,
        origin_airports: List,
        start_date: date,
        end_date: date,
        save_to_disk: bool = False,
    ) -> List[FlightInfo]:
        tasks: List[Tuple[str, str, str, Optional[str]]] = []
        for travel_date in get_date_range(start_date, end_date):
            for origin_airport in origin_airports:
                if (
                    isinstance(origin_airport, (list, tuple))
                    and len(origin_airport) >= 2
                ):
                    origin_code = str(origin_airport[0])
                else:
                    origin_code = str(origin_airport)
                tasks.append((origin_code, destination_airport, travel_date, None))

        flights = sorted(self._run_tasks(tasks), key=lambda flight: flight.price_value)
        if save_to_disk:
            self.save_flights(f"{destination_airport} FlyTo", flights)
        return flights
