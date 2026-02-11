#!/usr/bin/env python3

import os
from dotenv import load_dotenv
from coincall import Public, Options

load_dotenv()

API_KEY = os.getenv('COINCALL_API_KEY')
API_SECRET = os.getenv('COINCALL_API_SECRET')

print(f"API_KEY: {API_KEY}")
print(f"API_SECRET: {API_SECRET}")

public_api = Public.PublicAPI()
options_api = Options.OptionsAPI(API_KEY, API_SECRET)

# Set testnet URL
public_api.domain = 'https://beta.seizeyouralpha.com'
options_api.domain = 'https://beta.seizeyouralpha.com'

print("Public API methods:", [m for m in dir(public_api) if not m.startswith('_')])
print("Options API methods:", [m for m in dir(options_api) if not m.startswith('_')])

try:
    # Try to get server time
    time = public_api.get_server_time()
    print(f"Server time: {time}")
except Exception as e:
    print(f"Error getting server time: {e}")

try:
    # Try to get instruments
    instruments = options_api.get_instruments(base='BTC')
    print(f"Instruments: {instruments}")
except Exception as e:
    print(f"Error getting instruments: {e}")