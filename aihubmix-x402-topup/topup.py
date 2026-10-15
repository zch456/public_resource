# /// script
# dependencies = ["eth-account", "requests"]
# ///
"""AIHubMix USDC 自动充值：查余额 → 建单 → x402 付款 → 轮询到账。

用法：
  uv run topup.py check   只检查配置（令牌、私钥、余额），不花钱
  uv run topup.py 1       充值 $1（整数美元）

令牌和私钥的读取顺序：环境变量 → ~/.aihubmix-topup.env → 在终端里输入（可选择保存）
  AIHUBMIX_ACCESS_TOKEN  系统访问令牌（不是 sk- 开头的 API key）
  PAYER_PRIVATE_KEY      付款钱包的私钥（Base 主网，只放少量 USDC）
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
NETWORK = "eip155:8453"  # Base 主网
CHAIN_ID = 8453
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # Base 主网 USDC，6 位小数

CONFIG_FILE = Path.home() / ".aihubmix-topup.env"
CONFIG_TEMPLATE = """\
# AIHubMix USDC 充值配置。只保存在你自己的电脑上，不要发给任何人，包括 AI 助手。
# 系统访问令牌：aihubmix.com → 个人设置 → 系统访问令牌（不是 sk- 开头的 API key）
AIHUBMIX_ACCESS_TOKEN={token}
# 付款钱包私钥：只放少量 USDC 的专用钱包
PAYER_PRIVATE_KEY={key}
"""


def read_config():
    # 环境变量优先，其次是配置文件
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
    os.chmod(CONFIG_FILE, 0o600)  # 只有自己能读


def setup_credentials():
    # 缺令牌或私钥：能交互就在终端里输入；否则（例如由 AI 工具代为运行）生成配置文件让用户自己填
    global ACCESS_TOKEN, PRIVATE_KEY
    if not sys.stdin.isatty():
        if not CONFIG_FILE.exists():
            write_config("", "")
        sys.exit(f"缺少令牌或私钥：请用文本编辑器打开 {CONFIG_FILE}，填好后保存，再重新运行")
    ACCESS_TOKEN = ACCESS_TOKEN or getpass("系统访问令牌（输入时不显示）: ").strip()
    PRIVATE_KEY = PRIVATE_KEY or getpass("钱包私钥（输入时不显示）: ").strip()
    wallet_address()  # 私钥格式不对就不保存
    if input(f"保存到 {CONFIG_FILE}，下次不用再输？[Y/n] ").strip().lower() != "n":
        write_config(ACCESS_TOKEN, PRIVATE_KEY)


def wallet_address():
    try:
        return Account.from_key(PRIVATE_KEY).address
    except ValueError:
        sys.exit(f"私钥格式不对，应为 64 位十六进制字符（可带 0x 前缀）。请检查 {CONFIG_FILE} 或环境变量")


def usdc_balance(address):
    # 调用 USDC 合约的 balanceOf(address)，返回美元金额
    data = "0x70a08231" + address[2:].lower().rjust(64, "0")
    resp = requests.post(BASE_RPC, json={
        "jsonrpc": "2.0", "id": 1, "method": "eth_call",
        "params": [{"to": USDC, "data": data}, "latest"],
    }, timeout=30)
    resp.raise_for_status()
    return int(resp.json()["result"], 16) / 10**6


def aihubmix(method, path, **kwargs):
    # 裸令牌，不加 Bearer；除 401/403 外，业务错误都是 HTTP 200 + success:false
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
    # 用一个不存在的订单号查单：令牌有效时返回 Order not found，不会建单
    try:
        aihubmix("GET", "qry", params={"trade_no": "pl_check"})
    except RuntimeError as e:
        if "Order not found" not in str(e):
            raise


def create_order(amount):
    return aihubmix("POST", "crt", json={"amount": amount})


def check_challenge(req, amount):
    # 自动付款没有人做最后确认，这四项就是那道确认，任一不符就不签名
    expected = str(amount * 10**6)
    problems = []
    if req.get("scheme") != "exact":
        problems.append(f"scheme={req.get('scheme')}")
    if req.get("network") != NETWORK:
        problems.append(f"network={req.get('network')}")
    if str(req.get("asset", "")).lower() != USDC.lower():
        problems.append(f"asset={req.get('asset')}")
    if str(req.get("maxAmountRequired")) != expected:
        problems.append(f"maxAmountRequired={req.get('maxAmountRequired')}，应为 {expected}")
    if problems:
        raise RuntimeError("402 挑战校验失败，已中止：" + "；".join(problems))


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
    # 这两次请求都不带 access_token：x402 里付款方是匿名的
    first = requests.get(url, timeout=30)
    if first.status_code != 402:
        raise RuntimeError(f"预期 402，实际 {first.status_code}: {first.text[:200]}")
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
        raise RuntimeError(f"付款未被受理 {second.status_code}: {second.text[:200]}")
    return (second.json().get("receipt") or {}).get("txHash")


def wait_for_credit(trade_no, max_wait=600):
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            order = aihubmix("GET", "qry", params={"trade_no": trade_no})
        except requests.RequestException as e:
            print("查单失败，稍后重试：", e)
        else:
            if order["status"] == "TRADE_SUCCESS":
                return "credited"
            if order["status"] == "TRADE_CLOSED" or order.get("expired"):
                return "failed"
        time.sleep(8)  # 服务端每张单 5 秒节流，更密没有意义
    return "pending"  # 不代表失败，兜底对账会在分钟级补上


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "1"
    if arg != "check" and not arg.isdigit():
        sys.exit("用法：topup.py 金额（整数美元，例如 1），或 topup.py check 只检查配置")
    if not ACCESS_TOKEN or not PRIVATE_KEY:
        setup_credentials()
    address = wallet_address()
    try:
        balance = usdc_balance(address)
        print(f"付款钱包: {address}（USDC 余额 {balance}）")
        if arg == "check":
            check_token()
            if balance == 0:
                print("钱包里还没有 USDC：请往上面的地址转入 Base 主网 USDC")
            print("令牌和私钥都没问题，可以充值了")
            sys.exit()

        # 建单会占用当天次数，所以先确认余额够用
        amount = int(arg)
        if balance < amount:
            sys.exit(f"USDC 余额不足 {amount}，请先往这个地址转入 Base 主网 USDC")

        order = create_order(amount)
        print("tradeNo:", order["tradeNo"])

        tx_hash = pay(order["url"], order["amount"])
        print("txHash:", tx_hash)  # 唯一的链上凭证，务必持久化

        print("result:", wait_for_credit(order["tradeNo"]))
    except (RuntimeError, ValueError, requests.RequestException) as e:
        sys.exit(f"出错了：{e}")
