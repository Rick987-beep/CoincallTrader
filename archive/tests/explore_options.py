#!/usr/bin/env python3

from coincall import Options, Public
from config import API_KEY, API_SECRET
import logging
import time

# Set up logging
logging.basicConfig(level=logging.INFO)

# Initialize APIs
public_api = Public.PublicAPI()
options_api = Options.OptionsAPI(API_KEY, API_SECRET)

# Set testnet URLs
public_api.domain = 'https://betaapi.coincall.com'
options_api.domain = 'https://betaapi.coincall.com'

def explore_options():
    """Explore available options to understand expiry dates and delta ranges"""
    print("Exploring available BTC options...")

    # Get all instruments
    instruments_response = options_api.get_instruments(base='BTC')
    if not instruments_response or 'data' not in instruments_response:
        print("❌ Failed to get instruments")
        return

    options = instruments_response['data']
    print(f"Total BTC options: {len(options)}")

    # Group by expiry date
    expiries = {}
    for opt in options:
        expiry_ts = opt['expirationTimestamp']
        expiry_date = time.strftime('%Y-%m-%d', time.localtime(expiry_ts / 1000))
        if expiry_date not in expiries:
            expiries[expiry_date] = []
        expiries[expiry_date].append(opt)

    print(f"\nAvailable expiry dates: {sorted(expiries.keys())}")

    # Check current time and calculate days to expiry
    current_ms = int(time.time() * 1000)
    print(f"\nCurrent time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_ms / 1000))}")

    # For each expiry, show some delta ranges
    for expiry_date in sorted(expiries.keys())[:5]:  # First 5 expiries
        print(f"\n--- {expiry_date} ---")
        expiry_options = expiries[expiry_date]
        days_to_expiry = (expiry_options[0]['expirationTimestamp'] - current_ms) / (86400 * 1000)
        print(".1f")

        calls = [opt for opt in expiry_options if opt['symbolName'].endswith('-C')]
        puts = [opt for opt in expiry_options if opt['symbolName'].endswith('-P')]

        print(f"Calls: {len(calls)}, Puts: {len(puts)}")

        # Sample deltas for calls
        call_deltas = []
        for opt in calls[:3]:  # First 3 calls
            try:
                details = options_api.get_option_by_name(opt['symbolName'])
                if details and 'data' in details and 'delta' in details['data']:
                    delta = float(details['data']['delta'])
                    call_deltas.append((opt['strike'], delta))
            except Exception as e:
                pass

        # Sample deltas for puts
        put_deltas = []
        for opt in puts[:3]:  # First 3 puts
            try:
                details = options_api.get_option_by_name(opt['symbolName'])
                if details and 'data' in details and 'delta' in details['data']:
                    delta = float(details['data']['delta'])
                    put_deltas.append((opt['strike'], delta))
            except Exception as e:
                pass

        print(f"Sample call deltas: {call_deltas}")
        print(f"Sample put deltas: {put_deltas}")

if __name__ == "__main__":
    explore_options()