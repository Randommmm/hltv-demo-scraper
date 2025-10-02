"""Command line interface for the HLTV demo scraper."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import timedelta
from pathlib import Path
from time import perf_counter
from typing import Iterable, List

from .downloader import DemoDownloader, DownloadResult, unique_demo_ids, write_demo_id_file

DEFAULT_OUTPUT_DIR = Path("demos")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download demo archives from HLTV.org with Cloudscraper and a progress bar.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (DEBUG, INFO, WARNING, ERROR). Default: INFO",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    download_parser = subparsers.add_parser(
        "download",
        help="Download one or more demos by ID.",
    )
    download_parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory to save demos (default: {DEFAULT_OUTPUT_DIR}).",
    )
    download_parser.add_argument(
        "--ids-file",
        type=Path,
        action="append",
        help="Path to a text file containing demo IDs (one per line). Can be provided multiple times.",
    )
    download_parser.add_argument(
        "--start-id",
        type=int,
        help="Start ID for downloading a sequential range of demos (inclusive).",
    )
    download_parser.add_argument(
        "--end-id",
        type=int,
        help="End ID for downloading a sequential range of demos (inclusive).",
    )
    download_parser.add_argument(
        "--id",
        dest="ids",
        type=int,
        action="append",
        help="Download a specific demo ID. Can be provided multiple times.",
    )
    download_parser.add_argument(
        "--chunk-size",
        type=int,
        default=64 * 1024,
        help="Chunk size (in bytes) for streaming downloads. Default: 65536.",
    )
    download_parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Number of times to retry a failed download. Default: 3.",
    )
    download_parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Request timeout in seconds for each download attempt. Default: 60.",
    )
    download_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite files if they already exist.",
    )
    download_parser.add_argument(
        "--metadata-file",
        type=Path,
        help="Path to store metadata JSON (default: <output_dir>/metadata.json).",
    )

    id_file_parser = subparsers.add_parser(
        "generate-id-file",
        help="Generate a file containing a sequential list of demo IDs.",
    )
    id_file_parser.add_argument("start_id", type=int, help="First demo ID (inclusive).")
    id_file_parser.add_argument("end_id", type=int, help="Last demo ID (inclusive).")
    id_file_parser.add_argument("output", type=Path, help="Destination file to write IDs to.")

    return parser


def configure_logging(level: str) -> None:
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Invalid log level: {level}")
    logging.basicConfig(level=numeric_level, format="%(asctime)s %(levelname)s %(message)s")


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    configure_logging(args.log_level)

    if args.command == "generate-id-file":
        write_demo_id_file(args.start_id, args.end_id, args.output)
        return 0

    if args.command == "download":
        demo_ids = _collect_demo_ids(args)
        if not demo_ids:
            parser.error("No demo IDs provided. Use --id, --ids-file, or --start-id/--end-id.")

        metadata_path = args.metadata_file or (args.output_dir / "metadata.json")

        downloader = DemoDownloader(
            args.output_dir,
            chunk_size=args.chunk_size,
            retries=args.retries,
            timeout=args.timeout,
            skip_existing=not args.overwrite,
            metadata_path=metadata_path,
        )
        start_time = perf_counter()
        results = downloader.download_many(demo_ids)
        elapsed_seconds = perf_counter() - start_time
        _print_summary(results, elapsed_seconds)
        failed_downloads = [result for result in results if result.status not in {"downloaded", "skipped"}]
        return 1 if failed_downloads else 0

    parser.error("Unknown command")
    return 2


def _collect_demo_ids(args: argparse.Namespace) -> List[int]:
    ids: List[int] = []
    if args.ids:
        ids.extend(args.ids)
    if args.ids_file:
        ids.extend(_load_ids_from_files(args.ids_file))
    if args.start_id is not None or args.end_id is not None:
        if args.start_id is None or args.end_id is None:
            raise SystemExit("--start-id and --end-id must be provided together")
        if args.end_id < args.start_id:
            raise SystemExit("--end-id must be greater than or equal to --start-id")
        ids.extend(range(args.start_id, args.end_id + 1))
    return unique_demo_ids(ids)


def _load_ids_from_files(files: Iterable[Path]) -> List[int]:
    demo_ids: List[int] = []
    for file_path in files:
        with Path(file_path).open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                try:
                    demo_ids.append(int(stripped))
                except ValueError as exc:
                    raise SystemExit(
                        f"Invalid demo ID '{stripped}' in {file_path} on line {line_number}: {exc}"
                    ) from exc
    return demo_ids


def _print_summary(results: Iterable[DownloadResult], elapsed_seconds: float) -> None:
    total = 0
    counts = {"downloaded": 0, "skipped": 0, "not_found": 0, "failed": 0}
    bytes_downloaded = 0
    for result in results:
        total += 1
        counts.setdefault(result.status, 0)
        counts[result.status] += 1
        if result.status == "downloaded":
            bytes_downloaded += result.bytes_downloaded
        if result.status == "failed":
            logging.error("Failed to download %s: %s", result.demo_id, result.message)
        elif result.status == "not_found":
            logging.warning("Demo ID %s was not found", result.demo_id)

    logging.info(
        "Summary: %s demos processed (%s downloaded, %s skipped, %s not found, %s failed)",
        total,
        counts.get("downloaded", 0),
        counts.get("skipped", 0),
        counts.get("not_found", 0),
        counts.get("failed", 0),
    )

    logging.info(
        "Session stats: %s demos downloaded, %s transferred, elapsed %s",
        counts.get("downloaded", 0),
        _format_bytes(bytes_downloaded),
        timedelta(seconds=elapsed_seconds),
    )


def _format_bytes(num_bytes: int) -> str:
    if num_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(num_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
