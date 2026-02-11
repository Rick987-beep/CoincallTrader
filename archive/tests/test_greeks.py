#!/usr/bin/env python3

from market_data import get_btc_futures_price, get_option_details
from option_selection import select_option
from config import POSITION_CONFIG
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)

def test_option_selection():
    """Test selecting a specific call option and reading its Greeks"""
    print("Testing option selection and Greeks reading...")

    # Get current BTC/USDT perpetual futures price (not needed for strike selection but good to have)
    spot_price = get_btc_futures_price()
    print(f"BTC/USDT futures price: ${spot_price:.2f}")

    # Define test position config for specific option: 4FEB26 expiry, $80,000 strike call
    test_config = {
        'expiry_criteria': {'symbol': '4FEB26'},
        'legs': [
            {
                'option_type': 'C',  # Call
                'strike_criteria': {'type': 'strike', 'value': 80000},  # Strike $80,000
                'side': 1,  # buy
                'qty': 1
            }
        ]
    }

    # Test the leg
    leg = test_config['legs'][0]
    print(f"\nTesting leg: {leg['option_type']} with {leg['strike_criteria']}")

    symbol = select_option(
        test_config['expiry_criteria'],
        leg['strike_criteria'],
        leg['option_type'],
        'BTC'
    )

    if symbol:
        print(f"✅ Selected: {symbol}")
        
        # Now fetch and print Greeks
        print(f"\nFetching Greeks for {symbol}...")
        try:
            details = options_api.get_option_by_name(symbol)
            if details and 'data' in details and details['code'] == 0:
                data = details['data']
                greeks = {
                    'delta': data.get('delta'),
                    'vega': data.get('vega'),
                    'theta': data.get('theta'),
                    'gamma': data.get('gamma')
                }
                print("Greeks:")
                for greek, value in greeks.items():
                    if value is not None:
                        print(f"  {greek.capitalize()}: {float(value):.6f}")
                    else:
                        print(f"  {greek.capitalize()}: N/A")
                
                # Also print other useful info
                print(f"\nAdditional info:")
                print(f"  Implied Volatility: {data.get('impliedVolatility', 'N/A')}")
                print(f"  Bid: {data.get('bid', 'N/A')}")
                print(f"  Ask: {data.get('ask', 'N/A')}")
                print(f"  Mark Price: {data.get('markPrice', 'N/A')}")
            else:
                print(f"❌ Failed to get option details: {details}")
        except Exception as e:
            print(f"❌ Error fetching Greeks: {e}")
    else:
        print(f"❌ No option found")

    print("\nGreeks test completed!")

if __name__ == "__main__":
    test_option_selection()