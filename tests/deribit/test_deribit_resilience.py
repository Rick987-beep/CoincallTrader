"""
Test 6: Deribit Rate Limits & Error Handling — Resilience
==========================================================
Tests:
  6.1  Rapid-fire public calls — find where throttling kicks in
  6.2  Invalid/expired token — error shape
  6.3  Insufficient scope (simulated) — error shape
  6.4  Non-existent instrument — error shape

Run:  python tests/deribit/test_deribit_resilience.py [--prod]
"""
import requests
import json
import sys
import time

# ── Config ──────────────────────────────────────────────────────────────
USE_PROD = "--prod" in sys.argv

if USE_PROD:
    BASE_URL = "https://www.deribit.com"
    CLIENT_ID = "TV6tvw6J"
    CLIENT_SECRET = "NUDhggDLNwL9xj6N2_e-2dqP4jOrKnrBFRMVopK_IAM"
    ENV_LABEL = "PRODUCTION"
else:
    BASE_URL = "https://test.deribit.com"
    CLIENT_ID = "CWlZBUXA"
    CLIENT_SECRET = "sVrL_Bdz-j8_mtLB-y4EdxPS-YGkqeMtLzh4Wi1sz2E"
    ENV_LABEL = "TESTNET"

TOKEN = None

def authenticate():
    global TOKEN
    resp = requests.post(
        f"{BASE_URL}/api/v2/public/auth",
        json={
            "jsonrpc": "2.0", "id": 1,
            "method": "public/auth",
            "params": {
                "grant_type": "client_credentials",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
        },
        timeout=10,
    )
    data = resp.json()
    if "result" in data:
        TOKEN = data["result"]["access_token"]
        print(f"Auth OK — token acquired")
        return True
    print(f"Auth FAILED: {data.get('error')}")
    return False

def separator(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


# ────────────────────────────────────────────────────────────────────────
separator(f"AUTHENTICATION ({ENV_LABEL})")
if not authenticate():
    sys.exit(1)


# ────────────────────────────────────────────────────────────────────────
#  TEST 6.1: Rapid-fire public calls
# ────────────────────────────────────────────────────────────────────────
separator(f"TEST 6.1: Rapid-Fire Rate Limit Test ({ENV_LABEL})")

NUM_CALLS = 25  # keep it moderate to avoid being rude
results = []
start_all = time.time()

for i in range(NUM_CALLS):
    t0 = time.time()
    try:
        resp = requests.get(
            f"{BASE_URL}/api/v2/public/get_index_price",
            params={"index_name": "btc_usd"},
            timeout=10,
        )
        elapsed = time.time() - t0
        status = resp.status_code
        body = resp.json()
        has_error = "error" in body
        error_code = body.get("error", {}).get("code") if has_error else None
        results.append({
            "call": i + 1,
            "http": status,
            "error": has_error,
            "error_code": error_code,
            "ms": round(elapsed * 1000),
        })
        if has_error:
            print(f"  Call {i+1}: HTTP {status} — ERROR code={error_code}  "
                  f"msg={body['error'].get('message')}  ({round(elapsed*1000)}ms)")
    except Exception as e:
        elapsed = time.time() - t0
        results.append({"call": i + 1, "http": 0, "error": True,
                         "error_code": str(e), "ms": round(elapsed * 1000)})
        print(f"  Call {i+1}: EXCEPTION {e}  ({round(elapsed*1000)}ms)")

total_elapsed = time.time() - start_all
errors = [r for r in results if r["error"]]
latencies = [r["ms"] for r in results]

print(f"\n  ── Summary ──")
print(f"  Total calls: {NUM_CALLS} in {total_elapsed:.2f}s")
print(f"  Errors: {len(errors)}")
print(f"  Latency: min={min(latencies)}ms  max={max(latencies)}ms  "
      f"avg={sum(latencies)//len(latencies)}ms")

if errors:
    print(f"\n  ── Error details ──")
    for e in errors:
        print(f"    Call {e['call']}: HTTP {e['http']}  code={e['error_code']}")
else:
    print(f"  ✓ No throttling detected at {NUM_CALLS} rapid calls")


# ────────────────────────────────────────────────────────────────────────
#  TEST 6.2: Invalid / expired token
# ────────────────────────────────────────────────────────────────────────
separator(f"TEST 6.2: Invalid Token ({ENV_LABEL})")

# 6.2a: completely bogus token
print("  6.2a: Bogus token...")
resp = requests.post(
    f"{BASE_URL}/api/v2/private/get_account_summary",
    json={
        "jsonrpc": "2.0", "id": 1,
        "method": "private/get_account_summary",
        "params": {"currency": "BTC"},
    },
    headers={"Authorization": "Bearer INVALID_TOKEN_12345"},
    timeout=10,
)
data = resp.json()
print(f"  HTTP status: {resp.status_code}")
print(f"  Response: {json.dumps(data, indent=2)}")
if "error" in data:
    err = data["error"]
    print(f"  → error code: {err.get('code')}  message: {err.get('message')}")

# 6.2b: no token at all
print("\n  6.2b: No Authorization header...")
resp = requests.post(
    f"{BASE_URL}/api/v2/private/get_account_summary",
    json={
        "jsonrpc": "2.0", "id": 1,
        "method": "private/get_account_summary",
        "params": {"currency": "BTC"},
    },
    timeout=10,
)
data = resp.json()
print(f"  HTTP status: {resp.status_code}")
if "error" in data:
    err = data["error"]
    print(f"  → error code: {err.get('code')}  message: {err.get('message')}")


# ────────────────────────────────────────────────────────────────────────
#  TEST 6.3: Wrong scope simulation
# ────────────────────────────────────────────────────────────────────────
separator(f"TEST 6.3: Scope / Permission Errors ({ENV_LABEL})")

# We can't easily get a token with reduced scope via the API,
# so instead we test what happens when calling a non-existent private method.
print("  6.3: Call non-existent private method...")
resp = requests.post(
    f"{BASE_URL}/api/v2/private/nonexistent_method",
    json={
        "jsonrpc": "2.0", "id": 1,
        "method": "private/nonexistent_method",
        "params": {},
    },
    headers={"Authorization": f"Bearer {TOKEN}"},
    timeout=10,
)
data = resp.json()
print(f"  HTTP status: {resp.status_code}")
if "error" in data:
    err = data["error"]
    print(f"  → error code: {err.get('code')}  message: {err.get('message')}")
else:
    print(f"  → Response: {json.dumps(data, indent=2)[:200]}")


# ────────────────────────────────────────────────────────────────────────
#  TEST 6.4: Non-existent instrument
# ────────────────────────────────────────────────────────────────────────
separator(f"TEST 6.4: Non-Existent Instrument ({ENV_LABEL})")

print("  6.4a: Ticker for fake instrument...")
resp = requests.get(
    f"{BASE_URL}/api/v2/public/ticker",
    params={"instrument_name": "BTC-1JAN20-999999-C"},
    timeout=10,
)
data = resp.json()
print(f"  HTTP status: {resp.status_code}")
if "error" in data:
    err = data["error"]
    print(f"  → error code: {err.get('code')}  message: {err.get('message')}")
    if "data" in err:
        print(f"  → error data: {err['data']}")

print("\n  6.4b: Orderbook for fake instrument...")
resp = requests.get(
    f"{BASE_URL}/api/v2/public/get_order_book",
    params={"instrument_name": "FAKE-INSTRUMENT"},
    timeout=10,
)
data = resp.json()
print(f"  HTTP status: {resp.status_code}")
if "error" in data:
    err = data["error"]
    print(f"  → error code: {err.get('code')}  message: {err.get('message')}")


# ────────────────────────────────────────────────────────────────────────
#  TEST 6.5: Token Refresh Behavior
# ────────────────────────────────────────────────────────────────────────
separator(f"TEST 6.5: Token Refresh ({ENV_LABEL})")

print("  Authenticating to get a fresh token + refresh_token...")
resp = requests.post(
    f"{BASE_URL}/api/v2/public/auth",
    json={
        "jsonrpc": "2.0", "id": 1,
        "method": "public/auth",
        "params": {
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
    },
    timeout=10,
)
auth_data = resp.json().get("result", {})
refresh_token = auth_data.get("refresh_token")
old_token = auth_data.get("access_token")
print(f"  access_token: {old_token[:16]}...")
print(f"  refresh_token: {refresh_token[:16]}..." if refresh_token else "  refresh_token: NOT PRESENT")
print(f"  expires_in: {auth_data.get('expires_in')}s")
print(f"  token_type: {auth_data.get('token_type')}")

if refresh_token:
    print("\n  Refreshing token via refresh_token grant...")
    resp = requests.post(
        f"{BASE_URL}/api/v2/public/auth",
        json={
            "jsonrpc": "2.0", "id": 1,
            "method": "public/auth",
            "params": {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        },
        timeout=10,
    )
    data = resp.json()
    if "result" in data:
        new_token = data["result"]["access_token"]
        new_refresh = data["result"].get("refresh_token")
        print(f"  New access_token: {new_token[:16]}...")
        print(f"  New refresh_token: {new_refresh[:16]}..." if new_refresh else "  New refresh_token: NOT PRESENT")
        print(f"  Token changed? {'YES' if new_token != old_token else 'NO'}")
        print(f"  Refresh token changed? {'YES' if new_refresh != refresh_token else 'NO'}")
        print(f"  ✓ Token refresh works")

        # Verify old token still works (or doesn't)
        print("\n  Testing if OLD token still works after refresh...")
        resp = requests.post(
            f"{BASE_URL}/api/v2/private/get_account_summary",
            json={
                "jsonrpc": "2.0", "id": 1,
                "method": "private/get_account_summary",
                "params": {"currency": "BTC"},
            },
            headers={"Authorization": f"Bearer {old_token}"},
            timeout=10,
        )
        data = resp.json()
        if "result" in data:
            print(f"  → OLD token STILL VALID (equity={data['result'].get('equity')})")
        elif "error" in data:
            print(f"  → OLD token INVALIDATED: {data['error']}")
    else:
        print(f"  Refresh failed: {data.get('error')}")
else:
    print("  Skipping refresh test — no refresh_token")


# ────────────────────────────────────────────────────────────────────────
separator("TEST 6 SUMMARY")
print(f"Environment: {ENV_LABEL} ({BASE_URL})")
print(f"Rate limit test: {NUM_CALLS} rapid calls, {len(errors)} throttled")
print(f"\nTest 6: COMPLETE (manual review of error shapes above)")
