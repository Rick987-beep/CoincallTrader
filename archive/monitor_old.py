from coincall import Options
from config import API_KEY, API_SECRET, CLOSE_POSITION_CONDITIONS
import logging
from trade_execution import execute_trade

options_api = Options.OptionsAPI(API_KEY, API_SECRET)

def monitor_positions():
    try:
        positions = options_api.get_positions()
        for position in positions['data'] if positions else []:
            pnl = float(position.get('unrealizedPnl', 0))
            position_id = position.get('positionId')
            if pnl >= CLOSE_POSITION_CONDITIONS['profit_target'] or pnl <= CLOSE_POSITION_CONDITIONS['loss_limit']:
                # Close position: place opposite order
                symbol = position.get('symbol')
                qty = abs(float(position.get('qty', 0)))
                side = 2 if position.get('side') == 'long' else 1  # sell if long, buy if short
                execute_trade(symbol, qty, side)
                logging.info(f"Closed position {position_id} with PnL {pnl}")
            else:
                logging.info(f"Position {position_id} monitored, PnL {pnl}")
    except Exception as e:
        logging.error(f"Error monitoring positions: {e}")