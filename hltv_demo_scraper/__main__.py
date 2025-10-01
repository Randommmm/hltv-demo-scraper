"""Module entry point for ``python -m hltv_demo_scraper``."""
from .cli import main


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
