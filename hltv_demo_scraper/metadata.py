"""Utilities for collecting and persisting demo metadata."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from cloudscraper import CloudScraper
from requests import Response
from requests.exceptions import RequestException

_LOGGER = logging.getLogger(__name__)

_BASE_URL = "https://www.hltv.org"


@dataclass(slots=True)
class MatchMetadata:
    """Structured details about the match that produced a demo."""

    match_id: Optional[str]
    match_url: Optional[str]
    teams: List[str]
    date: Optional[str]

    def as_dict(self) -> Dict[str, object]:
        return {
            "match_id": self.match_id,
            "match_url": self.match_url,
            "teams": self.teams,
            "date": self.date,
        }


class MetadataCollector:
    """Fetch metadata for HLTV demos and persist it to a JSON file."""

    def __init__(self, scraper: CloudScraper, metadata_path: Path, *, timeout: int = 60) -> None:
        self.scraper = scraper
        self.metadata_path = Path(metadata_path)
        self.timeout = timeout

    def record_download(
        self,
        demo_id: int,
        *,
        file_path: Path,
        original_url: str,
        response: Response,
    ) -> None:
        """Store metadata for a successfully downloaded demo."""

        try:
            match_metadata = self._collect_match_metadata(demo_id)
        except RequestException as exc:
            _LOGGER.warning("Failed to collect match metadata for %s: %s", demo_id, exc)
            match_metadata = None

        entry: Dict[str, object] = {
            "filename": file_path.name,
            "match_info": match_metadata.as_dict() if match_metadata else None,
            "download_date": datetime.now(tz=UTC).isoformat(),
            "file_size": file_path.stat().st_size,
            "original_url": original_url,
        }

        if response.url:
            entry["resolved_download_url"] = response.url

        metadata = self._load_metadata()
        metadata.setdefault("demos", {})[str(demo_id)] = entry
        metadata["last_updated"] = datetime.now(tz=UTC).isoformat()

        self._write_metadata(metadata)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _collect_match_metadata(self, demo_id: int) -> Optional[MatchMetadata]:
        match_url = self._locate_match_url(demo_id)
        if not match_url:
            return None

        try:
            soup = self._fetch_match_page(match_url)
        except RequestException as exc:
            _LOGGER.debug("Unable to fetch match page for demo %s: %s", demo_id, exc)
            soup = None
        teams: List[str] = []
        date: Optional[str] = None
        if soup is not None:
            teams = self._extract_teams(soup)
            date = self._extract_match_date(soup)

        match_id = self._parse_match_id(match_url)
        return MatchMetadata(match_id=match_id, match_url=match_url, teams=teams, date=date)

    def _locate_match_url(self, demo_id: int) -> Optional[str]:
        url = f"{_BASE_URL}/results?demoid={demo_id}"
        response = self.scraper.get(url, timeout=self.timeout)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        result_card = soup.select_one(".result-con a[href]")
        if not result_card:
            _LOGGER.debug("No result card found for demo %s", demo_id)
            return None
        href = result_card.get("href")
        if not href:
            return None
        return urljoin(_BASE_URL, href)

    def _fetch_match_page(self, match_url: str) -> Optional[BeautifulSoup]:
        response = self.scraper.get(match_url, timeout=self.timeout)
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser")

    def _extract_teams(self, soup: BeautifulSoup) -> List[str]:
        teams: List[str] = []
        for team_elem in soup.select(".teamsBox .teamName"):
            name = team_elem.get_text(strip=True)
            if not name or name in teams:
                continue
            teams.append(name)
        if teams:
            return teams

        # Fallback: try compact mobile layout
        for team_elem in soup.select(".teamsBoxDropdown .teamName"):
            name = team_elem.get_text(strip=True)
            if not name or name in teams:
                continue
            teams.append(name)
        return teams

    def _extract_match_date(self, soup: BeautifulSoup) -> Optional[str]:
        date_elem = soup.select_one(".timeAndEvent .date[data-unix]")
        if date_elem is None:
            date_elem = soup.select_one(".date[data-unix]")
        if not date_elem:
            return None
        try:
            unix_ms = int(date_elem["data-unix"])
        except (KeyError, ValueError):
            return None
        dt = datetime.fromtimestamp(unix_ms / 1000, tz=UTC)
        return dt.date().isoformat()

    @staticmethod
    def _parse_match_id(match_url: str) -> Optional[str]:
        path = match_url.split("?", 1)[0]
        parts = path.rstrip("/").split("/")
        try:
            index = parts.index("matches")
        except ValueError:
            return None
        if index + 1 >= len(parts):
            return None
        return parts[index + 1]

    def _load_metadata(self) -> Dict[str, object]:
        if not self.metadata_path.exists():
            return {}
        try:
            with self.metadata_path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except json.JSONDecodeError as exc:
            _LOGGER.warning("Unable to parse metadata JSON at %s: %s", self.metadata_path, exc)
            return {}

    def _write_metadata(self, metadata: Dict[str, object]) -> None:
        self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        with self.metadata_path.open("w", encoding="utf-8") as handle:
            json.dump(metadata, handle, indent=2, ensure_ascii=False)

