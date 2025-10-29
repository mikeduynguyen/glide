# Glide

Crawler utilities for ingesting Airstream trailer listings.

## Getting started

Create a virtual environment and install the dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run the crawler from the command line to export listing data as JSON:

```bash
python -m glide.crawlers.airstream --pages 2 --delay 1 --output listings.json
```

## Tests

```bash
PYTHONPATH=. pytest
```