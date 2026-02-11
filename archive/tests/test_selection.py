#!/usr/bin/env python3

from coincall import Options, Public
from config import API_KEY, API_SECRET, POSITION_CONFIG
from trading import select_option
import logging
import time
import requests

# Set up logging
logging.basicConfig(level=logging.INFO)

# Initialize APIs
public_api = Public.PublicAPI()
options_api = Options.OptionsAPI(API_KEY, API_SECRET)

# Set testnet URLs
public_api.domain = 'https://betaapi.coincall.com'
options_api.domain = 'https://betaapi.coincall.com'

def test_option_selection():
    """Test the option selection logic for 4 Feb 2026 expiry with strikes 3% from spot"""
    print("Testing option selection for 4 Feb 2026 expiry with ±3% strikes from spot...")

    # Get current BTC/USDT perpetual futures price
    try:
        print(f"Available public API methods: {[m for m in dir(public_api) if not m.startswith('_')]}")
        # Try Coincall first
        spot_price = 0
        endpoints = ['/open/futures/ticker/BTCUSDT', '/open/futures/index/BTCUSDT']
        for endpoint in endpoints:
            try:
                response = public_api.client.get(endpoint)
                if response.status_code == 200:
                    data = response.json()
                    if data.get('code') == 0 and data.get('data'):
                        price_fields = ['lastPrice', 'price', 'indexPrice', 'markPrice']
                        for field in price_fields:
                            if field in data['data']:
                                spot_price = float(data['data'][field])
                                break
                        if spot_price > 0:
                            break
            except:
                pass
        
        # If Coincall failed, try Binance API for BTCUSDT futures
        if spot_price == 0:
            try:
                import requests
                binance_response = requests.get('https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT', timeout=5)
                if binance_response.status_code == 200:
                    binance_data = binance_response.json()
                    spot_price = float(binance_data['price'])
                    print(f"Using live BTC/USDT futures price from Binance: ${spot_price:.2f}")
                else:
                    spot_price = 72000.0
                    print(f"Binance API failed, using fallback BTC/USDT futures price: ${spot_price:.2f}")
            except Exception as e:
                spot_price = 72000.0
                print(f"Error getting Binance price: {e}, using fallback: ${spot_price:.2f}")
        else:
            print(f"Live BTC/USDT futures price from Coincall: ${spot_price:.2f}")
    except Exception as e:
        print(f"❌ Error getting futures price: {e}")
        spot_price = 72000.0
        print(f"Using fallback BTC/USDT futures price: ${spot_price:.2f}")
        
        if spot_price == 0:
            # Fallback
            spot_price = 72000.0
            print(f"Using fallback BTC/USDT futures price: ${spot_price:.2f}")
        else:
            print(f"Live BTC/USDT futures price: ${spot_price:.2f}")
    except Exception as e:
        print(f"❌ Error getting futures price: {e}")
        spot_price = 72000.0
        print(f"Using fallback BTC/USDT futures price: ${spot_price:.2f}")

    # Define test position config
    test_config = {
        'expiry_criteria': {'symbol': '4FEB26'},
        'legs': [
            {
                'option_type': 'C',  # Call
                'strike_criteria': {'type': 'spotdistance %', 'value': 3.0},  # +3% from spot
                'side': 1,  # buy
                'qty': 1
            },
            {
                'option_type': 'P',  # Put
                'strike_criteria': {'type': 'spotdistance %', 'value': -3.0},  # -3% from spot
                'side': 1,  # buy
                'qty': 1
            }
        ]
    }

    # Test each leg
    for i, leg in enumerate(test_config['legs']):
        print(f"\nTesting leg {i+1}: {leg['option_type']} with {leg['strike_criteria']}")

        symbol = select_option(
            test_config['expiry_criteria'],
            leg['strike_criteria'],
            leg['option_type'],
            'BTC'
        )

        if symbol:
            print(f"✅ Selected: {symbol}")
        else:
            print(f"❌ No option found for leg {i+1}")

    print("\nOption selection test completed!")

if __name__ == "__main__":
    test_option_selection()