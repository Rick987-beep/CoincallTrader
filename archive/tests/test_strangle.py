#!/usr/bin/env python3

from market_data import get_btc_futures_price, get_option_details
from option_selection import select_option
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)

def test_strangle_selection():
    """Test selecting long strangle options: 5 Feb expiry, delta ±0.25"""
    print("Testing long strangle selection: 5 Feb 2026 expiry, delta ±0.25...")

    # Get current BTC/USDT perpetual futures price
    spot_price = get_btc_futures_price()
    print(f"Live BTC/USDT futures price: ${spot_price:.2f}")

    # Define strangle position config: 5 Feb expiry, delta ±0.25
    strangle_config = {
        'expiry_criteria': {'symbol': '5FEB26'},  # 5 February 2026
        'legs': [
            {
                'option_type': 'C',  # Call
                'strike_criteria': {'type': 'delta', 'value': 0.25},  # Delta +0.25
                'side': 1,  # buy
                'qty': 1
            },
            {
                'option_type': 'P',  # Put
                'strike_criteria': {'type': 'delta', 'value': -0.25},  # Delta -0.25
                'side': 1,  # buy
                'qty': 1
            }
        ]
    }

    selected_options = []

    # Test each leg
    for i, leg in enumerate(strangle_config['legs']):
        print(f"\n--- Leg {i+1}: {leg['option_type']} with target {leg['strike_criteria']} ---")

        symbol = select_option(
            strangle_config['expiry_criteria'],
            leg['strike_criteria'],
            leg['option_type'],
            'BTC'
        )

        if symbol:
            print(f"✅ Selected: {symbol}")
            selected_options.append((leg['option_type'], symbol))
        else:
            print(f"❌ No option found for {leg['option_type']} leg")
            continue

    # Now fetch Greeks and market data for selected options
    print(f"\n{'='*60}")
    print("GREEKS AND MARKET DATA FOR SELECTED OPTIONS")
    print(f"{'='*60}")

    for option_type, symbol in selected_options:
        print(f"\n📊 {option_type} Option: {symbol}")
        print("-" * 40)

        try:
            details = get_option_details(symbol)
            if details:

                # Greeks
                greeks = {
                    'delta': details.get('delta'),
                    'theta': details.get('theta'),
                    'vega': details.get('vega'),
                    'gamma': details.get('gamma')
                }

                print("Greeks:")
                for greek, value in greeks.items():
                    if value is not None:
                        print(f"  {greek.capitalize()}: {float(value):.6f}")
                    else:
                        print(f"  {greek.capitalize()}: N/A")

                # Market data
                print("\nMarket Data:")
                market_data = {
                    'Bid': details.get('bid'),
                    'Ask': details.get('ask'),
                    'Mark Price': details.get('markPrice'),
                    'Implied Volatility': details.get('impliedVolatility')
                }

                for label, value in market_data.items():
                    if value is not None:
                        if 'Price' in label:
                            print(f"  {label}: ${float(value):.2f}")
                        else:
                            print(f"  {label}: {float(value):.4f}")
                    else:
                        print(f"  {label}: N/A")

            else:
                print(f"❌ Failed to get option details: {details}")

        except Exception as e:
            print(f"❌ Error fetching data for {symbol}: {e}")

    print(f"\n{'='*60}")
    print("STRANGLE TEST COMPLETED")
    print(f"{'='*60}")

if __name__ == "__main__":
    test_strangle_selection()