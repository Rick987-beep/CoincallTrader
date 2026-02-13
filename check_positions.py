#!/usr/bin/env python3
"""Check current positions."""
import logging
import json
from account_manager import account_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

positions = account_manager.get_positions(force_refresh=True)

logger.info("Current positions:")
for pos in positions:
    symbol = pos.get('symbol')
    logger.info(f"\n{symbol}:")
    logger.info(f"  Full data: {json.dumps(pos, indent=2)}")
