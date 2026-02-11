#!/usr/bin/env python3

import logging
from apscheduler.schedulers.blocking import BlockingScheduler
from config import TRADING_CONFIG

# NOTE: position_manager.py has been archived.
# The trade lifecycle is now managed by trade_lifecycle.LifecycleManager
# driven by account_manager.PositionMonitor callbacks.
# This main.py scheduler is a placeholder for future integration.

# Set up logging
logging.basicConfig(filename='logs/trading.log', level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

def main():
    scheduler = BlockingScheduler()
    monitor_interval = TRADING_CONFIG.get('monitor_interval', 60)
    # TODO: integrate trade_lifecycle.LifecycleManager here
    logging.info("Starting trading bot...")
    scheduler.start()

if __name__ == "__main__":
    main()