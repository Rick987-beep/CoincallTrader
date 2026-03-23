"""Quick auth test for both Deribit accounts."""
import requests
import json

def check_auth(label, base_url, client_id, client_secret):
    print(f"--- {label} ({base_url}) ---")
    try:
        resp = requests.post(
            f"{base_url}/api/v2/public/auth",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "public/auth",
                "params": {
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            },
            timeout=10,
        )
        data = resp.json()
        if "result" in data:
            r = data["result"]
            print(f"  AUTH OK")
            print(f"  token_type: {r.get('token_type')}")
            print(f"  expires_in: {r.get('expires_in')}s")
            print(f"  scope: {r.get('scope', '')}")
            token = r["access_token"]

            # Try an authenticated call
            acct = requests.post(
                f"{base_url}/api/v2/private/get_account_summary",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "private/get_account_summary",
                    "params": {"currency": "BTC"},
                },
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            ).json()

            if "result" in acct:
                a = acct["result"]
                print(f"  ACCOUNT OK")
                print(f"  equity: {a.get('equity')} BTC")
                print(f"  available_funds: {a.get('available_funds')} BTC")
                print(f"  currency: {a.get('currency')}")
            else:
                print(f"  ACCOUNT ERROR: {acct.get('error', acct)}")
        else:
            print(f"  AUTH FAILED: {data.get('error', data)}")
    except Exception as e:
        print(f"  CONNECTION ERROR: {e}")
    print()


if __name__ == "__main__":
    # short string = client_id, long string = client_secret
    check_auth(
        "TESTNET",
        "https://test.deribit.com",
        "CWlZBUXA",
        "sVrL_Bdz-j8_mtLB-y4EdxPS-YGkqeMtLzh4Wi1sz2E",
    )

    check_auth(
        "PRODUCTION",
        "https://www.deribit.com",
        "TV6tvw6J",
        "NUDhggDLNwL9xj6N2_e-2dqP4jOrKnrBFRMVopK_IAM",
    )
