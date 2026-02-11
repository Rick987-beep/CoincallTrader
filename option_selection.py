#!/usr/bin/env python3
"""
Option Selection Module

Handles option selection logic based on various criteria:
- Expiry matching (symbol-based or time-based)
- Strike selection (delta, distance from spot, exact strike)
"""

import time
import logging
from market_data import get_option_instruments, get_option_details, get_btc_futures_price


def select_option(expiry_criteria, strike_criteria, option_type='C', underlying='BTC'):
    """
    Select an option based on expiry and strike criteria.

    Args:
        expiry_criteria (dict): Expiry criteria - either {'symbol': '5FEB26'} or {'minExp': days, 'maxExp': days}
        strike_criteria (dict): Strike criteria - {'type': 'delta', 'value': 0.25} or other types
        option_type (str): 'C' for call, 'P' for put
        underlying (str): Underlying symbol, default 'BTC'

    Returns:
        str: Option symbol or None if not found
    """
    try:
        # Get available options
        options_list = get_option_instruments(underlying)
        if not options_list:
            return None

        # Filter by expiry
        expiry_options = _filter_by_expiry(options_list, expiry_criteria, option_type)
        if not expiry_options:
            return None

        # For delta selection, fetch delta for each option
        if strike_criteria.get('type') == 'delta':
            expiry_options = _add_delta_to_options(expiry_options)

        # Select strike based on criteria
        selected_option = _select_by_strike_criteria(expiry_options, strike_criteria)

        if selected_option:
            delta_info = f", delta: {selected_option.get('delta', 'N/A')}"
            logging.info(f"Selected option: {selected_option['symbolName']} (strike: {selected_option['strike']}{delta_info})")
            return selected_option['symbolName']

        return None

    except Exception as e:
        logging.error(f"Error selecting option: {e}")
        return None


def _filter_by_expiry(options_list, expiry_criteria, option_type):
    """
    Filter options by expiry criteria.

    Args:
        options_list (list): List of option instruments
        expiry_criteria (dict): Expiry criteria
        option_type (str): 'C' or 'P'

    Returns:
        list: Filtered options
    """
    # Two expiry matching modes supported:
    # - symbol: match by symbolName substring like '-4FEB26-' (preferred, no ms math)
    # - minExp/maxExp: legacy days-based matching using expirationTimestamp

    if isinstance(expiry_criteria, dict) and 'symbol' in expiry_criteria:
        sym = expiry_criteria['symbol']
        # Match symbolName containing the expiry token and option type
        expiry_options = [opt for opt in options_list if (f"-{sym}-" in opt.get('symbolName', '')) and opt['symbolName'].endswith('-' + option_type)]
        if not expiry_options:
            logging.error(f"No options matching symbol expiry {sym} and type {option_type}")
            return []
    else:
        # Legacy time-based matching
        current_time = time.time() * 1000  # Convert to milliseconds for API
        min_expiry = current_time + expiry_criteria['minExp'] * 86400 * 1000
        max_expiry = current_time + expiry_criteria['maxExp'] * 86400 * 1000

        # Filter options by expiry range and type
        valid_options = [opt for opt in options_list if min_expiry <= opt['expirationTimestamp'] <= max_expiry and opt['symbolName'].endswith('-' + option_type)]
        if not valid_options:
            logging.error(f"No options within expiry range {expiry_criteria} and type {option_type}")
            return []

        # Find closest expiry and filter to that expiry
        target_expiry = (min_expiry + max_expiry) / 2
        closest_expiry_opt = min(valid_options, key=lambda x: abs(x['expirationTimestamp'] - target_expiry))
        expiry_date = closest_expiry_opt['expirationTimestamp']
        expiry_options = [opt for opt in valid_options if opt['expirationTimestamp'] == expiry_date]

    return expiry_options


def _add_delta_to_options(options_list):
    """
    Add delta values to option instruments by fetching details.

    Args:
        options_list (list): List of option instruments

    Returns:
        list: Options with delta added
    """
    options_with_delta = []
    for opt in options_list[:10]:  # Limit to 10 to avoid too many API calls
        try:
            details = get_option_details(opt['symbolName'])
            if details and 'delta' in details:
                delta = float(details['delta'])
                opt['delta'] = delta
                options_with_delta.append(opt)
            else:
                logging.warning(f"Could not get delta details for {opt['symbolName']}: {details}")
        except Exception as e:
            logging.warning(f"Could not get delta for {opt['symbolName']}: {e}")

    return options_with_delta


def _select_by_strike_criteria(options_list, strike_criteria):
    """
    Select option based on strike criteria.

    Args:
        options_list (list): List of option instruments
        strike_criteria (dict): Strike selection criteria

    Returns:
        dict: Selected option instrument or None
    """
    criteria_type = strike_criteria['type']

    if criteria_type == 'closestStrike':
        target_strike = strike_criteria['value']
        return min(options_list, key=lambda x: abs(x['strike'] - target_strike))

    elif criteria_type == 'delta':
        target_delta = strike_criteria['value']
        return min(options_list, key=lambda x: abs(x.get('delta', 0) - target_delta))

    elif criteria_type == 'spotdistance %':
        spot_price = get_btc_futures_price()
        pct = strike_criteria['value'] / 100
        target_price = spot_price * (1 + pct)
        return min(options_list, key=lambda x: abs(x['strike'] - target_price))

    elif criteria_type == 'strike':
        # Exact strike match
        target_strike = strike_criteria['value']
        exact_matches = [opt for opt in options_list if float(opt.get('strike', 0)) == float(target_strike)]
        if not exact_matches:
            logging.error(f"No exact strike {target_strike} found in expiry options")
            return None
        return exact_matches[0]

    else:
        logging.error(f"Invalid strike criteria type: {criteria_type}")
        return None