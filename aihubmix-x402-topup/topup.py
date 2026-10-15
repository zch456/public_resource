# /// script
# dependencies = ["eth-account", "requests"]
# ///
"""AIHubMix USDC top-up over x402: check balance -> create order -> pay -> wait for credit.

Usage:
  uv run topup.py check   check the setup (token, private key, balance) without spending anything
  uv run topup.py 1       top up $1 (whole dollars only)

Credentials are read from environment variables, then ~/.aihubmix-topup.env, then a prompt
in the terminal (which offers to save them):
  AIHUBMIX_ACCESS_TOKEN  system access token (not the sk- API key)
  PAYER_PRIVATE_KEY      private key of the paying wallet (Base mainnet, holding only a little USDC)
"""
import base64
import json
import os
import secrets
import sys
import time
from getpass import getpass
from pathlib import Path

import requests
from eth_account import Account
from eth_account.messages import encode_typed_data

API = "https://aihubmix.com/api/pay/fluxa"
BASE_RPC = "https://mainnet.base.org"
NETWORK = "eip155:8453"  # Base mainnet
CHAIN_ID = 8453
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # USDC on Base mainnet, 6 decimals

CONFIG_FILE = Path.home() / ".aihubmix-topup.env"
CONFIG_TEMPLATE = """\
# AIHubMix USDC top-up settings. Keep this file on your own computer and never share it, not even with an AI assistant.
# System access token: aihubmix.com -> personal settings -> system access token (not the sk- API key)
AIHUBMIX_ACCESS_TOKEN={token}
# Private key of a dedicated wallet that holds only a little USDC
PAYER_PRIVATE_KEY={key}
"""


def read_config():
    # Environment variables win over the config file
    saved = {}
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and not name.lstrip().startswith("#"):
                saved[name.strip()] = value.strip().strip("\"'")
    return tuple(
        os.environ.get(name) or saved.get(name, "")
        for name in ("AIHUBMIX_ACCESS_TOKEN", "PAYER_PRIVATE_KEY")
    )


ACCESS_TOKEN, PRIVATE_KEY = read_config()


def write_config(token, key):
    fd = os.open(CONFIG_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(CONFIG_TEMPLATE.format(token=token, key=key))
    os.chmod(CONFIG_FILE, 0o600)  # readable by you only


def setup_credentials():
    # Missing credentials: prompt if there's a terminal; otherwise (e.g. run by an AI tool)
    # write a blank config file for the user to fill in
    global ACCESS_TOKEN, PRIVATE_KEY
    if not sys.stdin.isatty():
        if not CONFIG_FILE.exists():
            write_config("", "")
        sys.exit(f"Missing access token or private key: open {CONFIG_FILE} in a text editor, "
                 "fill in both, save it, then run this again")
    ACCESS_TOKEN = ACCESS_TOKEN or getpass("System access token (input hidden): ").strip()
    PRIVATE_KEY = PRIVATE_KEY or getpass("Wallet private key (input hidden): ").strip()
    wallet_address()  # don't save a malformed key
    if input(f"Save to {CONFIG_FILE} so you don't have to type them again? [Y/n] ").strip().lower() != "n":
        write_config(ACCESS_TOKEN, PRIVATE_KEY)


def wallet_address():
    try:
        return Account.from_key(PRIVATE_KEY).address
    except ValueError:
        sys.exit("Invalid private key: expected 64 hex characters (0x prefix optional). "
                 f"Check {CONFIG_FILE} or your environment variables")


def usdc_balance(address):
    # Call balanceOf(address) on the USDC contract; returns dollars
    data = "0x70a08231" + address[2:].lower().rjust(64, "0")
    resp = requests.post(BASE_RPC, json={
        "jsonrpc": "2.0", "id": 1, "method": "eth_call",
        "params": [{"to": USDC, "data": data}, "latest"],
    }, timeout=30)
    resp.raise_for_status()
    return int(resp.json()["result"], 16) / 10**6


def aihubmix(method, path, **kwargs):
    # Bare token, no "Bearer"; apart from 401/403, business errors come back as HTTP 200 + success:false
    resp = requests.request(
        method, f"{API}/{path}",
        headers={"Authorization": ACCESS_TOKEN}, timeout=30, **kwargs,
    )
    try:
        body = resp.json()
    except ValueError:
        resp.raise_for_status()
        raise
    if not body.get("success"):
        raise RuntimeError(body.get("message"))
    return body["data"]


def check_token():
    # Query an order number that doesn't exist: a valid token gets "Order not found", and no order is created
    try:
        aihubmix("GET", "qry", params={"trade_no": "pl_check"})
    except RuntimeError as e:
        if "Order not found" not in str(e):
            raise


def create_order(amount):
    return aihubmix("POST", "crt", json={"amount": amount})


def check_challenge(req, amount):
    # Nobody approves an automatic payment, so these four checks are that approval; any mismatch means no signature
    expected = str(amount * 10**6)
    problems = []
    if req.get("scheme") != "exact":
        problems.append(f"scheme={req.get('scheme')}")
    if req.get("network") != NETWORK:
        problems.append(f"network={req.get('network')}")
    if str(req.get("asset", "")).lower() != USDC.lower():
        problems.append(f"asset={req.get('asset')}")
    if str(req.get("maxAmountRequired")) != expected:
        problems.append(f"maxAmountRequired={req.get('maxAmountRequired')}, expected {expected}")
    if problems:
        raise RuntimeError("402 challenge check failed, aborting: " + "; ".join(problems))


def sign_authorization(req):
    wallet = Account.from_key(PRIVATE_KEY)
    now = int(time.time())
    extra = req.get("extra") or {}
    message = {
        "from": wallet.address,
        "to": req["payTo"],
        "value": int(req["maxAmountRequired"]),
        "validAfter": now - 60,
        "validBefore": now + max(int(req.get("maxTimeoutSeconds") or 0), 600),
        "nonce": "0x" + secrets.token_hex(32),
    }
    typed_data = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ],
        },
        "primaryType": "TransferWithAuthorization",
        "domain": {
            "name": extra.get("name", "USD Coin"),
            "version": extra.get("version", "2"),
            "chainId": CHAIN_ID,
            "verifyingContract": USDC,
        },
        "message": message,
    }
    signed = wallet.sign_message(encode_typed_data(full_message=typed_data))
    return {
        "signature": "0x" + bytes(signed.signature).hex(),
        "authorization": {k: str(v) for k, v in message.items()},
    }


def pay(url, amount):
    # Neither request carries the access token: the payer is anonymous in x402
    first = requests.get(url, timeout=30)
    if first.status_code != 402:
        raise RuntimeError(f"Expected 402, got {first.status_code}: {first.text[:200]}")
    req = first.json()["accepts"][0]
    check_challenge(req, amount)

    payment = {
        "x402Version": 1,
        "scheme": "exact",
        "network": NETWORK,
        "payload": sign_authorization(req),
    }
    header = base64.b64encode(json.dumps(payment).encode()).decode()
    second = requests.get(url, headers={"X-PAYMENT": header}, timeout=60)
    if second.status_code not in (200, 202):
        raise RuntimeError(f"Payment not accepted, {second.status_code}: {second.text[:200]}")
    return (second.json().get("receipt") or {}).get("txHash")


def wait_for_credit(trade_no, max_wait=600):
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            order = aihubmix("GET", "qry", params={"trade_no": trade_no})
        except requests.RequestException as e:
            print("Order query failed, retrying:", e)
        else:
            if order["status"] == "TRADE_SUCCESS":
                return "credited"
            if order["status"] == "TRADE_CLOSED" or order.get("expired"):
                return "failed"
        time.sleep(8)  # the server refreshes each order at most every 5 seconds
    return "pending"  # not a failure: reconciliation credits it within minutes


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "1"
    if arg != "check" and not arg.isdigit():
        sys.exit("Usage: topup.py AMOUNT (whole dollars, e.g. 1), or topup.py check to check the setup only")
    if not ACCESS_TOKEN or not PRIVATE_KEY:
        setup_credentials()
    address = wallet_address()
    try:
        balance = usdc_balance(address)
        print(f"Wallet: {address} (USDC balance {balance})")
        if arg == "check":
            check_token()
            if balance == 0:
                print("This wallet has no USDC yet: send Base mainnet USDC to the address above")
            print("Access token and private key are fine. Ready to top up.")
            sys.exit()

        # Creating an order uses up one of today's attempts, so check the balance first
        amount = int(arg)
        if balance < amount:
            sys.exit(f"USDC balance is below {amount}: send Base mainnet USDC to this address first")

        order = create_order(amount)
        print("tradeNo:", order["tradeNo"])

        tx_hash = pay(order["url"], order["amount"])
        print("txHash:", tx_hash)  # the only on-chain receipt; keep it

        print("result:", wait_for_credit(order["tradeNo"]))
    except (RuntimeError, ValueError, requests.RequestException) as e:
        sys.exit(f"Error: {e}")
