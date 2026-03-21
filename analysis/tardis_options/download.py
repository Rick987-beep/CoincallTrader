#!/usr/bin/env python3
"""
Download Deribit options_chain data from tardis.dev.

tardis.dev free tier: 1st of each month, no API key required.
URL pattern: https://datasets.tardis.dev/v1/deribit/options_chain/YYYY/MM/DD/OPTIONS.csv.gz

The file contains ALL Deribit options (BTC + ETH + all expiries) at tick-level
granularity. Expect ~4.5GB compressed per day. Supports HTTP Range resume.

Usage:
    python -m analysis.tardis_options.download                    # default: 2025-03-01
    python -m analysis.tardis_options.download 2025-02-01         # specify date
    python -m analysis.tardis_options.download 2025-01-01 --force # re-download
"""
import argparse
import os
import sys

try:
    import requests
except ImportError:
    print("pip install requests")
    sys.exit(1)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def download(date_str="2025-03-01", force=False):
    """Download options_chain for a given date.

    Args:
        date_str: Date string YYYY-MM-DD (must be 1st of month for free tier).
        force:    If True, overwrite existing file.

    Returns:
        Path to the downloaded .csv.gz file.
    """
    os.makedirs(DATA_DIR, exist_ok=True)

    year, month, day = date_str.split("-")
    url = (
        f"https://datasets.tardis.dev/v1/deribit/options_chain"
        f"/{year}/{month}/{day}/OPTIONS.csv.gz"
    )
    gz_path = os.path.join(DATA_DIR, f"options_chain_{date_str}.csv.gz")

    if force and os.path.exists(gz_path):
        os.remove(gz_path)

    existing_size = os.path.getsize(gz_path) if os.path.exists(gz_path) else 0

    # Try a streaming GET (with Range header if we have a partial file)
    req_headers = {}
    mode = "wb"
    downloaded = 0

    if existing_size > 0:
        req_headers["Range"] = "bytes=%d-" % existing_size

    print(f"Connecting to {url} ...")
    resp = requests.get(url, stream=True, timeout=120, headers=req_headers)
    resp.raise_for_status()

    if resp.status_code == 206:
        # Resume succeeded
        mode = "ab"
        downloaded = existing_size
        total_size = existing_size + int(resp.headers.get("content-length", 0))
        print(f"Resuming from byte {existing_size:,} / {total_size:,}")
    else:
        total_size = int(resp.headers.get("content-length", 0))
        if existing_size > 0 and existing_size >= total_size > 0:
            print(f"Already complete: {gz_path} ({existing_size:,} bytes)")
            resp.close()
            return gz_path
        if existing_size > 0:
            print(f"Server doesn't support resume, restarting...")
        print(f"Downloading {total_size:,} bytes ...")

    with open(gz_path, mode) as f:
        for chunk in resp.iter_content(chunk_size=256 * 1024):
            f.write(chunk)
            downloaded += len(chunk)
            if total_size:
                pct = downloaded / total_size * 100
                print(
                    f"\r  {downloaded:,} / {total_size:,} bytes ({pct:.0f}%)",
                    end="", flush=True,
                )
            else:
                print(f"\r  {downloaded:,} bytes", end="", flush=True)

    print(f"\nSaved: {gz_path} ({os.path.getsize(gz_path):,} bytes)")
    return gz_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download tardis.dev options data")
    parser.add_argument("date", nargs="?", default="2025-03-01", help="YYYY-MM-DD")
    parser.add_argument("--force", action="store_true", help="Re-download")
    args = parser.parse_args()
    download(args.date, force=args.force)
