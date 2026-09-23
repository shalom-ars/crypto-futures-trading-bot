"""Binance API Connection & Key Verification Script.

Inspects configured API keys, tests Binance network connectivity, and
verifies account authentication / trading permissions.
"""

import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv
import ccxt.async_support as ccxt_async

# Load environment variables
dotenv_path = Path(".env")
env_loaded = load_dotenv(dotenv_path)

from config import BotConfig


def mask_string(s: str, show_start: int = 6, show_end: int = 4) -> str:
    """Mask a sensitive key string for safe terminal display."""
    if not s:
        return "[NOT SET / EMPTY]"
    if len(s) <= (show_start + show_end):
        return f"*** (Length: {len(s)})"
    return f"{s[:show_start]}...{s[-show_end:]} (Length: {len(s)})"


async def verify_binance():
    config = BotConfig()
    print("=" * 68)
    print("        BINANCE FUTURES API KEY & CONNECTIVITY VERIFIER")
    print("=" * 68)

    # 1. Environment & Config Inspection
    print(f"\n[1] CONFIGURATION INSPECTION:")
    print(f"  - .env file detected       : {'YES (' + str(dotenv_path.resolve()) + ')' if dotenv_path.exists() else 'NO (using OS environment / defaults)'}")
    print(f"  - Exchange Target          : {config.EXCHANGE_ID.upper()}")
    print(f"  - Mode                     : {'TESTNET (Sandbox)' if config.TESTNET else 'PRODUCTION (Mainnet)'}")
    print(f"  - Dry-Run / Simulation     : {config.DRY_RUN}")
    print(f"  - Simulated Wallet Equity  : ${config.SIMULATED_WALLET_EQUITY:.2f} USDT")

    # Inspect Keys
    api_key = config.API_KEY.strip()
    api_secret = config.API_SECRET.strip()

    print(f"\n[2] ACTIVE CREDENTIALS:")
    print(f"  - API Key                  : {mask_string(api_key)}")
    print(f"  - API Secret               : {mask_string(api_secret, show_start=3, show_end=3)}")

    key_env_var = None
    for var in ["EXCHANGE_API_KEY", "BINANCE_API_KEY", "BINANCE_KEY"]:
        if os.getenv(var):
            key_env_var = var
            break
    print(f"  - Key Source Variable      : {key_env_var or 'None'}")

    # 2. Public Network Connectivity Test (no auth headers)
    print(f"\n[3] TESTING PUBLIC NETWORK & MARKET DATA:")
    public_client = ccxt_async.binance({
        "enableRateLimit": True,
        "options": {
            "defaultType": "swap",
            "defaultSubType": "linear",
        },
    })
    if config.TESTNET:
        public_client.set_sandbox_mode(True)

    try:
        server_time = await public_client.fetch_time()
        print(f"  [OK] Connected to Binance Futures REST API.")
        print(f"  [OK] Server Time Sync: {server_time}")

        ticker = await public_client.fetch_ticker("BTC/USDT:USDT")
        print(f"  [OK] Live Public Market Data: BTC/USDT Price = ${float(ticker['last']):,.2f}")
    except Exception as e:
        print(f"  [FAIL] Public market connection failed: {e}")
    finally:
        await public_client.close()

    # 3. Authentication & Account Permissions Test
    print(f"\n[4] TESTING AUTHENTICATION & TRADING PERMISSIONS:")
    if not api_key or not api_secret:
        print("  [STATUS] No API credentials provided.")
        print("  -> The bot is running in SIMULATION / DRY-RUN mode.")
        print("  -> It streams live Binance market prices and simulates trades using $300 virtual equity.")
        print("  -> To connect a real Binance account for live order execution, create a `.env` file with:")
        print("       EXCHANGE_API_KEY=your_actual_binance_api_key")
        print("       EXCHANGE_API_SECRET=your_actual_binance_api_secret")
    else:
        auth_client = ccxt_async.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",
                "defaultSubType": "linear",
                "adjustForTimeDifference": True,
            },
        })
        if config.TESTNET:
            auth_client.set_sandbox_mode(True)

        try:
            print("  [>] Contacting Binance Account API with configured keys...")
            balance = await auth_client.fetch_balance({"type": "swap"})
            usdt_free = balance.get("USDT", {}).get("free", 0.0)
            usdt_total = balance.get("USDT", {}).get("total", 0.0)
            print(f"  [SUCCESS] API Key is VALID and successfully authenticated!")
            print(f"  [SUCCESS] USDT Wallet Balance: Free = ${float(usdt_free or 0):,.2f} | Total = ${float(usdt_total or 0):,.2f}")

            # Query exact permissions from Binance SAPI
            try:
                restrictions = await auth_client.sapi_get_account_apirestrictions()
                enable_futures = restrictions.get("enableFutures", False)
                enable_reading = restrictions.get("enableReading", False)
                ip_restrict = restrictions.get("ipRestrict", False)

                print(f"  [PERMISSIONS] Reading: {'ENABLED' if enable_reading else 'DISABLED'}")
                print(f"  [PERMISSIONS] Futures Trading: {'ENABLED' if enable_futures else 'DISABLED (Action Required: Check Enable Futures in Binance)'}")
                print(f"  [PERMISSIONS] IP Restriction: {'RESTRICTED' if ip_restrict else 'UNRESTRICTED'}")

                if not enable_futures:
                    print("\n  [!] ACTION REQUIRED FOR LIVE EXECUTION:")
                    print("      1. Login to Binance -> API Management")
                    print("      2. Click 'Edit' on this API Key")
                    print("      3. Check the box 'Enable Futures'")
                    print("      4. Save changes with 2FA / Authenticator")
                else:
                    # Test order placement validation on Binance Futures
                    try:
                        test_order = await auth_client.fapiPrivatePostOrderTest({
                            "symbol": "BTCUSDT",
                            "side": "BUY",
                            "type": "MARKET",
                            "quantity": 0.001,
                        })
                        print(f"  [SUCCESS] Live Order Matching Engine Verified: Binance accepted test order interaction!")
                    except Exception as err:
                        print(f"  [INFO] Order engine test response: {err}")
            except Exception as e:
                print(f"  [INFO] Could not fetch API restrictions: {e}")

        except ccxt_async.AuthenticationError as e:
            print(f"  [FAIL] Authentication Error: Binance rejected the API Key or Secret.")
            print(f"         Details: {e}")
        except ccxt_async.PermissionDenied as e:
            print(f"  [FAIL] Permission Denied: Key lacks Futures Trading permissions or has IP restriction.")
            print(f"         Details: {e}")
        except Exception as e:
            print(f"  [FAIL] Account check failed: {e}")
        finally:
            await auth_client.close()

    print("=" * 68)


if __name__ == "__main__":
    asyncio.run(verify_binance())
