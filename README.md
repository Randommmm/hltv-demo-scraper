# HLTV Demo Scraper

A small command line utility for downloading demo archives from [HLTV.org](https://www.hltv.org/) using
[Cloudscraper](https://github.com/VeNoMouS/cloudscraper). The tool supports:

- Downloading individual demo IDs, sequential ranges, or IDs read from a file
- Generating ID files for distributing work across multiple machines
- Progress bars for each download via `tqdm`
- Automatic retry logic, error handling, and optional file overwriting
- Structured metadata capture for each successful download, written to JSON alongside the demos

## Installation

Create a virtual environment and install the dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

The CLI exposes two subcommands: `download` and `generate-id-file`.

### Download demos

```bash
python -m hltv_demo_scraper download --start-id 118700 --end-id 118710 --output-dir demos
```

You can also mix and match ID sources:

```bash
python -m hltv_demo_scraper download \
  --ids-file ids_to_download.txt \
  --id 118738 --id 118739 \
  --start-id 118700 --end-id 118705
```

By default, existing files are skipped. To overwrite, pass `--overwrite`.

Each downloaded archive is saved with its demo ID prefixed to the filename (for example,
`100639_esl-pro-league-season-22-stage-1-m80-vs-heroic-bo3.rar`). Metadata describing the download
is written to `metadata.json` in the output directory unless another path is provided via
`--metadata-file`.

The metadata file contains a `demos` map keyed by demo ID strings. Each entry includes the saved
filename, the match information (teams, match URL, HLTV match ID, and match date when available), the
UTC timestamp of the download, the file size in bytes, and both the original HLTV download URL and the
resolved CDN URL. The top-level `last_updated` field reflects when the file was last written. A minimal
example looks like this:

```json
{
  "demos": {
    "100639": {
      "filename": "100639_esl-pro-league-season-22-stage-1-m80-vs-heroic-bo3.rar",
      "match_info": {
        "match_id": "2385919",
        "match_url": "https://www.hltv.org/matches/2385919/heroic-vs-3dmax-esl-pro-league-season-22-stage-1",
        "teams": [
          "HEROIC",
          "3DMAX"
        ],
        "date": "2025-09-30"
      },
      "download_date": "2025-10-01T10:25:45.711662+00:00",
      "file_size": 397045659,
      "original_url": "https://www.hltv.org/download/demo/100639",
      "resolved_download_url": "https://r2-demos.hltv.org/demos/118738/..."
    }
  },
  "last_updated": "2025-10-01T10:25:45.711683+00:00"
}
```

### Generate an ID file

```bash
python -m hltv_demo_scraper generate-id-file 118700 118900 ids_118700_118900.txt
```

This creates a text file with one demo ID per line, making it simple to split work across machines.

## Notes

- The script uses Cloudscraper to negotiate Cloudflare challenges before streaming the demo file.
- Each download displays a `tqdm` progress bar indicating the number of bytes written. When the server
  provides a `Content-Length`, the bar will show the estimated completion percentage.
- Failed downloads are retried a configurable number of times (`--retries`). At the end of the run,
  a summary is printed indicating how many demos were downloaded, skipped, missing, or failed.

## Disclaimer

This project was built for digital preservation and assumes you have permission to download the demo
files. Respect HLTV's terms of service and the rate limits agreed upon with their CDN partner.
