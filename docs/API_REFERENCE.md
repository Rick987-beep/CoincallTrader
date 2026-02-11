# Coincall API Reference

**Official Documentation:** https://docs.coincall.com/  
**Last Updated:** February 10, 2026

This document summarizes the key Coincall API endpoints relevant to our trading system.

---

## Internal Module: Trade Lifecycle

See [trade_lifecycle.py](../trade_lifecycle.py) for the trade state machine implementation.

### Quick Start
```python
from trade_lifecycle import lifecycle_manager, profit_target, max_loss, max_hold_hours
from rfq import OptionLeg

# Define a strangle
legs = [
    OptionLeg('BTCUSD-28FEB26-58000-P', 'BUY', 0.5),
    OptionLeg('BTCUSD-28FEB26-78000-C', 'BUY', 0.5),
]

# Create a trade with exit conditions
trade = lifecycle_manager.create(
    legs=legs,
    exit_conditions=[profit_target(0.50), max_loss(0.80), max_hold_hours(24)],
    execution_mode='rfq',
    label='long strangle'
)

# Open via RFQ
lifecycle_manager.open(trade.trade_id)

# tick() is called automatically by PositionMonitor — evaluates exits
# Or force-close manually:
lifecycle_manager.force_close(trade.trade_id)
```

### Key Classes
| Class | Purpose |
|-------|---------|
| `TradeState` | Enum: PENDING_OPEN → OPENING → OPEN → PENDING_CLOSE → CLOSING → CLOSED \| FAILED |
| `TradeLeg` | Single leg: symbol, qty, side, order_id, fill_price, filled_qty |
| `TradeLifecycle` | Groups legs with exit conditions; computes PnL, Greeks (pro-rated by our qty share) |
| `LifecycleManager` | State machine: `create()`, `open()`, `close()`, `tick()`, `force_close()` |

### Exit Condition Factories
| Factory | Signature | Description |
|---------|-----------|-------------|
| `profit_target(pct)` | `float → Callable` | Close when structure PnL ≥ pct of entry cost |
| `max_loss(pct)` | `float → Callable` | Close when structure loss ≥ pct of entry cost |
| `max_hold_hours(hours)` | `float → Callable` | Close after N hours |
| `account_delta_limit(thr)` | `float → Callable` | Close when account delta exceeds threshold |
| `structure_delta_limit(thr)` | `float → Callable` | Close when structure delta exceeds threshold |
| `leg_greek_limit(idx, greek, op, val)` | `... → Callable` | Close when a specific leg's Greek crosses a limit |

### Position Scaling
The lifecycle tracks our filled quantity vs. the exchange's total position quantity:
- `_our_share(leg, pos)` = `our_filled_qty / exchange_total_qty` (clamped to [0, 1])
- Applied to `structure_pnl()`, `structure_delta()`, `structure_greeks()`
- Prevents contamination when the account has positions from other sources

---

## Internal Module: Position Monitoring

See [account_manager.py](../account_manager.py) for position monitoring implementation.

### Quick Start
```python
from account_manager import PositionMonitor

monitor = PositionMonitor(poll_interval=5)

# Register a callback (called on every poll)
monitor.on_update(lambda snapshot: print(snapshot.summary_str()))

monitor.start()
# ... monitor runs in background thread ...
snap = monitor.snapshot()  # Thread-safe current snapshot
monitor.stop()
```

### Key Classes
| Class | Purpose |
|-------|---------|
| `PositionSnapshot` | Frozen dataclass: symbol, qty, side, avgPrice, markPrice, delta, gamma, vega, theta, unrealized_pnl, roi |
| `AccountSnapshot` | Frozen dataclass: equity, available_margin, im/mm amounts, positions list, aggregated Greeks, `get_position()`, `summary_str()` |
| `PositionMonitor` | Background polling thread with callbacks, `snapshot()`, `start()`, `stop()`, `on_update()` |

### Position Fields (from API)
Uses `upnlByMarkPrice` and `roiByMarkPrice` for accurate options PnL (not `upnl`/`roi` which use last trade price). Also captures `lastPrice`, `indexPrice`, `value` fields.

---

## Internal Module: RFQ Executor

See [rfq.py](../rfq.py) for our RFQ execution implementation.

### Quick Start
```python
from rfq import RFQExecutor, OptionLeg, create_strangle_legs

# Define a strangle structure
legs = create_strangle_legs('28FEB26', 100000, 90000, qty=1.0)

# Open a long position (BUY the strangle)
rfq = RFQExecutor()
result = rfq.execute(legs, action='buy', timeout_seconds=60)

if result.success:
    print(f"Bought for ${result.total_cost:.2f}")

# Later: Close the position (SELL the strangle)
result = rfq.execute(legs, action='sell', timeout_seconds=60)
if result.success:
    print(f"Sold for ${abs(result.total_cost):.2f}")
```

### Key Concepts

**Direction Logic:**
- RFQs are always submitted with legs as "BUY" to the Coincall API
- Market makers respond with two-way quotes (both BUY and SELL sides)
- The quote's `side` field indicates the **market maker's** action, not ours:
  - MM `SELL` = they sell to us = **WE BUY** = positive cost (we pay)
  - MM `BUY` = they buy from us = **WE SELL** = negative cost (we receive)
- Use the `action` parameter to filter: `'buy'` or `'sell'`

**Requirements:**
- Minimum notional: $50,000 (sum of strike values × quantity)
- Accept/Cancel endpoints require `application/x-www-form-urlencoded` content type

**Quote Selection (Best-Quote Logic):**
- All valid quotes are sorted by price (cheapest first for buys, highest first for sells)
- Every quote is logged with rank, cost, and improvement vs. orderbook mid-price
- `min_improvement_pct` parameter gates acceptance: set to 0 to require beating the book, or -999 to accept anything
- On accept failure (quote expired), automatically falls through to next-best quote
- Quotes with <1s remaining until expiry are skipped

**Timing (observed in production):**
- Quotes typically arrive within 3-5 seconds
- Default poll interval: 3 seconds
- Recommended timeout: 60 seconds

### Key Classes
| Class | Purpose |
|-------|---------|
| `OptionLeg` | Dataclass for leg definition (instrument, side, qty) |
| `RFQState` | Enum: PENDING, ACTIVE, FILLED, CANCELLED, EXPIRED |
| `RFQQuote` | Quote received from market maker (with `is_we_buy`, `is_we_sell` properties) |
| `RFQResult` | Execution result with all details |
| `RFQExecutor` | Main executor class |
| `TakerAction` | Enum: BUY, SELL (what we want to do) |

### Helper Functions
| Function | Purpose |
|----------|---------|
| `create_strangle_legs()` | Create call+put legs for strangle |
| `create_spread_legs()` | Create vertical spread legs |
| `execute_rfq()` | Convenience function for quick execution |

---

## Authentication

All private endpoints require:
- `X-CC-APIKEY` header with your API key
- `sign` header with HMAC-SHA256 signature
- `ts` header with current timestamp (milliseconds)
- `X-REQ-TS-DIFF` header (optional, request timestamp tolerance)

### Signature Algorithm
```
sign = HMAC-SHA256(apiSecret, method + uri + "?" + sortedQueryParams)
```

For POST with JSON body, include body params in query string for signing.

---

## Options Trading

### Get Option Instruments
```
GET /open/option/getInstruments/{baseCurrency}
```
Returns all available options for a currency (BTC, ETH, etc.)

**Response fields:**
- `symbolName` - Full option name (e.g., "BTCUSD-14SEP23-22500-C")
- `strike` - Strike price
- `expirationTimestamp` - Expiry time in milliseconds
- `isActive` - Whether tradeable
- `minQty`, `tickSize`

### Get Option Chain
```
GET /open/option/get/v1/{index}?endTime={endTime}
```
Returns full option chain with Greeks, IV, orderbook summary.

### Get Option Details
```
GET /open/option/detail/v1/{symbol}
```
Returns single option details including Greeks.

### Get Option OrderBook
```
GET /open/option/order/orderbook/v1/{symbol}
```
Returns 100-depth orderbook.

### Place Option Order
```
POST /open/option/order/create/v1
```
**Parameters:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| symbol | string | Yes | Option symbol |
| tradeSide | number | Yes | 1=BUY, 2=SELL |
| tradeType | number | Yes | 1=LIMIT, 3=POST_ONLY |
| qty | number | Yes | Quantity |
| price | number | Limit only | Price |
| timeInForce | string | No | IOC, GTC, FOK |
| reduceOnly | number | No | 1=reduce only |
| mmp | boolean | No | Market maker protection |

**Rate Limit:** 60/s

### Batch Create Orders
```
POST /open/option/order/batchCreate/v1
```
Up to 40 orders per request.

### Cancel Order
```
POST /open/option/order/cancel/v1
```
By orderId or clientOrderId.

### Get Positions
```
GET /open/option/position/get/v1
```
Returns all open option positions with Greeks, P&L.

**Response data (array of positions):**
| Field | Type | Description |
|-------|------|-------------|
| positionId | string | Unique position ID |
| symbol | string | Option symbol (e.g. `BTCUSD-13FEB26-80000-C`) |
| displayName | string | Human-readable name |
| qty | number | Position size |
| avgPrice | number | Average entry price |
| markPrice | number | Current mark price |
| upnl | number | Unrealised P&L (USD) |
| roi | number | Return on investment (ratio) |
| tradeSide | number | 1=BUY (long), 2=SELL (short) |
| delta | number | Position delta |
| gamma | number | Position gamma |
| vega | number | Position vega |
| theta | number | Position theta |

### Get Account Summary
```
GET /open/account/summary/v1
```
**Response data:**
| Field | Type | Description |
|-------|------|-------------|
| equity | number | Total account equity (USD) |
| availableMargin | number | Margin available for new trades |
| imAmount | number | Initial margin used |
| mmAmount | number | Maintenance margin required |
| unrealizedPnL | number | Total unrealised P&L |
| imRatio | number | Initial margin ratio |
| mmRatio | number | Maintenance margin ratio |
| totalDollarValue | number | Total account value in USD |

---

## RFQ (Block Trades)

**Important Notes:**
- RFQs must always be submitted with legs as `"side": "BUY"` 
- Market makers respond with two-way quotes (both BUY and SELL)
- Minimum notional: $50,000 (sum of strike values)
- Accept and Cancel endpoints require `application/x-www-form-urlencoded` content type

### Create RFQ Request (Taker)
```
POST /open/option/blocktrade/request/create/v1
Content-Type: application/json
```
**Body:**
```json
{
  "legs": [
    {"instrumentName": "BTCUSD-29OCT25-109000-C", "side": "BUY", "qty": "1"},
    {"instrumentName": "BTCUSD-29OCT25-90000-P", "side": "BUY", "qty": "1"}
  ]
}
```
**Response:**
```json
{
  "data": {
    "requestId": "1983060031318396928",
    "expiryTime": 1761636929597,
    "state": "ACTIVE"
  }
}
```

### Get Quotes Received
```
GET /open/option/blocktrade/request/getQuotesReceived/v1?requestId={id}
```
Returns list of quotes from market makers. Each quote contains:
- `quoteId` - Unique quote identifier
- `legs` - Array with each leg's `side`, `price`, `quantity`, `instrumentName`
- `state` - Quote state (OPEN, CANCELLED, FILLED)

**Quote Direction:**
- Leg `side: "SELL"` = MM sells to us = **we BUY** = we pay
- Leg `side: "BUY"` = MM buys from us = **we SELL** = we receive

### Execute Quote (Accept)
```
POST /open/option/blocktrade/request/accept/v1
Content-Type: application/x-www-form-urlencoded
```
**Parameters (form-urlencoded):**
- `requestId` - RFQ request ID
- `quoteId` - Quote ID to accept

### Cancel RFQ
```
POST /open/option/blocktrade/request/cancel/v1
Content-Type: application/x-www-form-urlencoded
```
**Parameters (form-urlencoded):**
- `requestId` - RFQ request ID to cancel
```
POST /open/option/blocktrade/request/cancel/v1
```

### Get RFQ List
```
GET /open/option/blocktrade/rfqList/v1
```
Query your RFQ history with filters.

### RFQ States
- `ACTIVE` - Waiting for quotes
- `CANCELLED` - Cancelled by user
- `FILLED` - Quote accepted and executed
- `EXPIRED` - Timed out
- `TRADED_AWAY` - Another quote was accepted

---

## Futures Trading

### Get Futures Instruments
```
GET /open/futures/market/instruments/v1
```

### Get Futures Symbol Info
```
GET /open/futures/market/symbol/v1
```

### Get Futures OrderBook
```
GET /open/futures/order/orderbook/v1/{symbol}
```

### Set Leverage
```
POST /open/futures/leverage/set/v1
```
**Parameters:** symbol, leverage

### Place Futures Order
```
POST /open/futures/order/create/v1
```
**Parameters:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| symbol | string | Yes | BTCUSD, ETHUSD, etc. |
| tradeSide | number | Yes | 1=BUY, 2=SELL |
| tradeType | number | Yes | 1=LIMIT, 2=MARKET, 3=POST_ONLY, 4=STOP_LIMIT, 5=STOP_MARKET |
| qty | number | Yes | Quantity |
| price | number | Limit only | Price |
| triggerPrice | number | Stop only | Trigger price |
| reduceOnly | number | No | 1=reduce only |

### Get Futures Positions
```
GET /open/futures/position/get/v1
```

---

## Spot Trading

### Get Spot Instruments
```
GET /open/spot/market/instruments
```

### Get Spot OrderBook
```
GET /open/spot/market/orderbook?symbol={symbol}
```

### Place Spot Order
```
POST /open/spot/trade/order/v1
```
**Parameters:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| symbol | string | Yes | TRXUSDT, etc. |
| tradeSide | string | Yes | 1=BUY, 2=SELL |
| tradeType | string | Yes | 1=LIMIT, 2=MARKET, 3=POST_ONLY |
| qty | string | Yes | Quantity |
| price | string | Limit only | Price |

**Note:** CALL token cannot be traded via API.

---

## Account

### Get Account Summary
```
GET /open/account/summary/v1
```
Returns balance, equity, margin info.

### Get Wallet
```
GET /open/account/wallet/v1
```
Returns holdings per asset.

### Query API Info
```
GET /open/auth/user/query-api
```
Returns API key permissions, readOnly status.

---

## WebSocket Connections

### Options WebSocket
```
wss://ws.coincall.com/options?code=10&uuid={uuid}&ts={ts}&sign={sign}&apiKey={apiKey}
```

### Futures WebSocket
```
wss://ws.coincall.com/futures?code=10&uuid={uuid}&ts={ts}&sign={sign}&apiKey={apiKey}
```

### Spot WebSocket (Public)
```
wss://ws.coincall.com/spot/ws
```

### Spot WebSocket (Private)
```
wss://ws.coincall.com/spot/ws/private?ts={ts}&sign={sign}&apiKey={apiKey}
```

### Subscribe Format
```json
{"action": "subscribe", "dataType": "order"}
{"action": "subscribe", "dataType": "position"}
{"action": "subscribe", "dataType": "orderBook", "payload": {"symbol": "BTCUSD"}}
```

### RFQ WebSocket Channels
| Channel | Data Type | Description |
|---------|-----------|-------------|
| `rfqMaker` | 28 | RFQ requests for market makers |
| `rfqTaker` | 129 | RFQ status updates for takers |
| `rfqQuote` | 130 | Quote updates for makers |
| `quoteReceived` | 131 | Incoming quotes for takers |
| `blockTradeDetail` | 22 | Private trade confirmations |
| `blockTradePublic` | 23 | Public trade feed |

### Heartbeat
Send any message within 30 seconds to keep connection alive.

---

## Error Codes

| Code | Message | Description |
|------|---------|-------------|
| 0 | Success | OK |
| 10534 | order.size.exceeds.the.maximum.limit.per.order | Order too large |
| 10540 | Order has expired | Order expired |
| 10558 | less.than.min.amount | Below minimum quantity |

---

## Sample Python Code

### WebSocket Connection
```python
import hashlib
import hmac
import websocket
import json

api_key = "YOUR_API_KEY"
api_sec = "YOUR_API_SECRET"

def get_signed_header(ts):
    verb = 'GET'
    uri = '/users/self/verify'
    auth = verb + uri + '?apiKey=' + api_key + '&ts=' + str(ts)
    signature = hmac.new(
        api_sec.encode('utf-8'), 
        auth.encode('utf-8'), 
        hashlib.sha256
    ).hexdigest()
    return signature.upper()

def on_open(ws):
    ws.send(json.dumps({
        "action": "subscribe", 
        "dataType": "order"
    }))

def on_message(ws, message):
    data = json.loads(message)
    print(data)

ts = int(time.time() * 1000)
sign = get_signed_header(ts)
url = f"wss://ws.coincall.com/options?code=10&ts={ts}&sign={sign}&apiKey={api_key}"

ws = websocket.WebSocketApp(url, on_open=on_open, on_message=on_message)
ws.run_forever()
```

---

*For complete documentation, see https://docs.coincall.com/*
