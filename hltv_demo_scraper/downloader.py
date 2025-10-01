"""Download HLTV demo archives with progress reporting."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.parse import unquote

import cloudscraper
from requests import Response
from requests.exceptions import RequestException
from tqdm import tqdm

from .metadata import MetadataCollector

_LOGGER = logging.getLogger(__name__)

DEMO_URL_TEMPLATE = "https://www.hltv.org/download/demo/{demo_id}"


@dataclass
class DownloadResult:
    """Represents the outcome of a demo download operation."""

    demo_id: int
    status: str
    message: str = ""
    file_path: Optional[Path] = None

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.status == "downloaded"


class DemoDownloader:
    """Download demos from HLTV with Cloudscraper and a progress bar."""

    def __init__(
        self,
        output_dir: Path,
        *,
        chunk_size: int = 64 * 1024,
        retries: int = 3,
        timeout: int = 60,
        skip_existing: bool = True,
        metadata_path: Optional[Path] = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.chunk_size = chunk_size
        self.retries = max(1, retries)
        self.timeout = timeout
        self.skip_existing = skip_existing
        self.scraper = cloudscraper.create_scraper()
        self.metadata_collector = (
            MetadataCollector(self.scraper, metadata_path, timeout=timeout)
            if metadata_path
            else None
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def download_many(self, demo_ids: Iterable[int]) -> List[DownloadResult]:
        results: List[DownloadResult] = []
        for demo_id in demo_ids:
            result = self.download_demo(demo_id)
            results.append(result)
        return results

    def download_demo(self, demo_id: int) -> DownloadResult:
        url = DEMO_URL_TEMPLATE.format(demo_id=demo_id)
        last_error: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            try:
                _LOGGER.debug("Fetching demo %s (attempt %s)", demo_id, attempt)
                with self.scraper.get(url, timeout=self.timeout, stream=True) as response:
                    if response.status_code == 404:
                        return DownloadResult(demo_id, "not_found", "Demo ID does not exist")
                    response.raise_for_status()

                    filename = self._filename_from_response(response, demo_id)
                    destination = self.output_dir / filename
                    if self.skip_existing and destination.exists():
                        _LOGGER.info("Skipping %s; file already exists", filename)
                        return DownloadResult(
                            demo_id,
                            "skipped",
                            "File already exists",
                            destination,
                        )

                    total_bytes = self._content_length(response)
                    progress = tqdm(
                        total=total_bytes,
                        unit="B",
                        unit_scale=True,
                        desc=f"Demo {demo_id}",
                        leave=False,
                    )
                    try:
                        with destination.open("wb") as file_handle:
                            for chunk in response.iter_content(chunk_size=self.chunk_size):
                                if not chunk:
                                    continue
                                file_handle.write(chunk)
                                progress.update(len(chunk))
                    finally:
                        progress.close()

                _LOGGER.info("Downloaded %s -> %s", url, destination)
                if self.metadata_collector:
                    try:
                        self.metadata_collector.record_download(
                            demo_id,
                            file_path=destination,
                            original_url=url,
                            response=response,
                        )
                    except Exception as exc:  # pragma: no cover - defensive logging
                        _LOGGER.warning(
                            "Failed to record metadata for demo %s: %s", demo_id, exc
                        )
                return DownloadResult(demo_id, "downloaded", file_path=destination)
            except RequestException as exc:
                last_error = exc
                _LOGGER.warning(
                    "Error downloading demo %s on attempt %s/%s: %s",
                    demo_id,
                    attempt,
                    self.retries,
                    exc,
                )
        error_message = f"Failed after {self.retries} attempts: {last_error}"
        _LOGGER.error("%s", error_message)
        return DownloadResult(demo_id, "failed", error_message)

    @staticmethod
    def _content_length(response: Response) -> Optional[int]:
        header_value = response.headers.get("Content-Length")
        if header_value is None:
            return None
        try:
            return int(header_value)
        except ValueError:
            return None

    @staticmethod
    def _filename_from_response(response: Response, demo_id: int) -> str:
        disposition = response.headers.get("Content-Disposition")
        if disposition:
            filename = DemoDownloader._parse_content_disposition(disposition)
            if filename:
                return DemoDownloader._ensure_demo_id_prefix(filename, demo_id)

        # Fall back to deriving from the final URL if present.
        filename = Path(response.url).name.split("?")[0]
        if filename:
            return DemoDownloader._ensure_demo_id_prefix(filename, demo_id)

        return f"{demo_id}_demo.rar"

    @staticmethod
    def _ensure_demo_id_prefix(filename: str, demo_id: int) -> str:
        sanitized = Path(filename).name
        prefix = f"{demo_id}_"
        if sanitized.startswith(prefix):
            return sanitized
        return prefix + sanitized

    @staticmethod
    def _parse_content_disposition(header_value: str) -> Optional[str]:
        parts = [part.strip() for part in header_value.split(";") if part.strip()]
        for part in parts[1:]:
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            key = key.lower().strip()
            value = value.strip().strip('"')

            if key == "filename*":
                # RFC 5987: filename*=UTF-8''encoded-name
                _, _, encoded_value = value.partition("''")
                candidate = encoded_value or value
                return Path(unquote(candidate)).name
            if key == "filename":
                return Path(value).name
        return None


def unique_demo_ids(demo_ids: Iterable[int]) -> List[int]:
    seen = set()
    ordered_ids: List[int] = []
    for demo_id in demo_ids:
        if demo_id in seen:
            continue
        seen.add(demo_id)
        ordered_ids.append(demo_id)
    return ordered_ids


def write_demo_id_file(start_id: int, end_id: int, destination: Path) -> Path:
    if end_id < start_id:
        raise ValueError("end_id must be greater than or equal to start_id")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        for demo_id in range(start_id, end_id + 1):
            handle.write(f"{demo_id}\n")
    _LOGGER.info(
        "Wrote %s demo IDs (%s-%s) to %s",
        end_id - start_id + 1,
        start_id,
        end_id,
        destination,
    )
    return destination
