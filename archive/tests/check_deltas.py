#!/usr/bin/env python3

from coincall import Options
from config import API_KEY, API_SECRET
import time

options_api = Options.OptionsAPI(API_KEY, API_SECRET)
options_api.domain = 'https://betaapi.coincall.com'

# Get instruments
instruments = options_api.get_instruments(base='BTC')
if not instruments or 'data' not in instruments:
    print('Failed to get instruments')
    exit()

current_ms = int(time.time() * 1000)
print(f'Current time: {time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(current_ms / 1000))}')

# Look for Feb 13 expiry
feb13_options = [opt for opt in instruments['data'] if opt['expirationTimestamp'] > current_ms + 10*86400*1000 and opt['expirationTimestamp'] < current_ms + 12*86400*1000]
if feb13_options:
    expiry_ts = feb13_options[0]['expirationTimestamp']
    expiry_date = time.strftime('%Y-%m-%d', time.localtime(expiry_ts / 1000))
    days_to_expiry = (expiry_ts - current_ms) / (86400 * 1000)
    print(f'Feb 13 expiry: {expiry_date} ({days_to_expiry:.1f} days)')

    # Get all strikes for calls and puts
    calls = [opt for opt in feb13_options if opt['symbolName'].endswith('-C')]
    puts = [opt for opt in feb13_options if opt['symbolName'].endswith('-P')]

    print(f'Calls: {len(calls)}, Puts: {len(puts)}')

    # Sample some deltas around potential target strikes
    target_strikes = [75000, 80000, 85000, 90000]
    for strike in target_strikes:
        # Find call with this strike
        call_opt = next((opt for opt in calls if opt['strike'] == strike), None)
        if call_opt:
            try:
                details = options_api.get_option_by_name(call_opt['symbolName'])
                if details and 'data' in details and 'delta' in details['data']:
                    delta = float(details['data']['delta'])
                    print(f'Call {strike}: delta = {delta:.4f}')
            except Exception as e:
                print(f'Error getting call {strike}: {e}')

        # Find put with this strike
        put_opt = next((opt for opt in puts if opt['strike'] == strike), None)
        if put_opt:
            try:
                details = options_api.get_option_by_name(put_opt['symbolName'])
                if details and 'data' in details and 'delta' in details['data']:
                    delta = float(details['data']['delta'])
                    print(f'Put {strike}: delta = {delta:.4f}')
            except Exception as e:
                print(f'Error getting put {strike}: {e}')