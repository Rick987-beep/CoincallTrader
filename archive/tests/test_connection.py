from coincall import Options, Public
from config import API_KEY, API_SECRET
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)

# Initialize APIs
public_api = Public.PublicAPI()
options_api = Options.OptionsAPI(API_KEY, API_SECRET)

# Set testnet URLs
public_api.domain = 'https://betaapi.coincall.com'
options_api.domain = 'https://betaapi.coincall.com'

def test_connection():
    try:
        # Test public API - get server time
        print("Testing public API connection...")
        server_time = public_api.get_server_time()
        print(f"Server time: {server_time}")

        # Test options API - get instruments
        print("Testing options API connection...")
        instruments = options_api.get_instruments(base='BTC')
        print(f"Instruments response type: {type(instruments)}")
        print(f"Instruments response: {instruments}")
        
        # Check if it's a dict with data
        if isinstance(instruments, dict):
            if 'data' in instruments:
                data = instruments['data']
                print(f"Found {len(data)} BTC options instruments in data")
                if data:
                    print(f"Sample instrument: {data[0]}")
            else:
                print("No 'data' key in response")
        elif isinstance(instruments, list):
            print(f"Found {len(instruments)} BTC options instruments")
            if instruments:
                print(f"Sample instrument: {instruments[0]}")
        else:
            print(f"Unexpected response type: {type(instruments)}")

        print("Connection test completed successfully!")

    except Exception as e:
        print(f"Connection test failed with error: {e}")
        print(f"Error type: {type(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_connection()