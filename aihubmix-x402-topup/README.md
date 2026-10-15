# AIHubMix USDC top-up script

Lets an AI agent top up an AIHubMix account with USDC on Base over x402, from $1 per top-up.

No download needed. Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run:

```bash
# Check the setup (spends nothing)
uv run https://raw.githubusercontent.com/zch456/public_resource/main/aihubmix-x402-topup/topup.py check

# Top up $1
uv run https://raw.githubusercontent.com/zch456/public_resource/main/aihubmix-x402-topup/topup.py 1
```

You need:

- An AIHubMix system access token (aihubmix.com → personal settings; not the `sk-` API key)
- The private key of a wallet holding USDC on Base mainnet (no ETH needed)

On the first run the script asks for the token and private key, and can save them to `~/.aihubmix-topup.env` (readable only by your account on macOS and Linux).

The script reads your wallet's private key. Use a dedicated wallet that holds only a little USDC.
