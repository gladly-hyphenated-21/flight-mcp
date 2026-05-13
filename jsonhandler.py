import json
from pathlib import Path
from typing import Any, Iterable, List
from flightwrapper import FlightInfo


class JsonHandler:
    def __init__(self, base_path: str | Path = ".") -> None:
        self.base_path = Path(base_path)

    def _resolve_json_path(self, file_name: str | Path) -> Path:
        file_path = Path(file_name)
        if file_path.suffix.lower() != ".json":
            file_path = file_path.with_suffix(".json")

        if not file_path.is_absolute():
            file_path = self.base_path / file_path

        return file_path

    def write_flight_list(
        self, file_name: str | Path, flight_list: Iterable[FlightInfo]
    ) -> Path:
        file_path = self._resolve_json_path(file_name)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        flights = [
            flight.to_dict() if hasattr(flight, "to_dict") else flight
            for flight in flight_list
        ]
        with file_path.open("w", encoding="utf-8") as json_file:
            json.dump(flights, json_file, ensure_ascii=False, indent=2)

        return file_path

    def read_flight_list(self, file_name: str | Path) -> List[Any]:
        file_path = self._resolve_json_path(file_name)
        if not file_path.exists():
            return []

        with file_path.open("r", encoding="utf-8") as json_file:
            data = json.load(json_file)

        if isinstance(data, list):
            return data

        return [data]
