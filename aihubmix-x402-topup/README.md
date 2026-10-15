# AIHubMix USDC 自动充值脚本

让 AI agent 用 Base 链上的 USDC，通过 x402 给 AIHubMix 账户充值，单笔最低 $1。

不用下载，装好 [uv](https://docs.astral.sh/uv/getting-started/installation/) 后直接运行：

```bash
# 检查配置（不花钱）
uv run https://raw.githubusercontent.com/zch456/public_resource/main/aihubmix-x402-topup/topup.py check

# 充值 $1
uv run https://raw.githubusercontent.com/zch456/public_resource/main/aihubmix-x402-topup/topup.py 1
```

需要准备：

- AIHubMix 系统访问令牌（aihubmix.com → 个人设置，不是 `sk-` 开头的 API key）
- 一个钱包的私钥，钱包里有 Base 主网 USDC，不需要 ETH

第一次运行时，脚本会让你输入令牌和私钥，并可以保存到 `~/.aihubmix-topup.env`（macOS / Linux 上只有你自己的账户能读）。

脚本会读取你的钱包私钥，请只用放了少量 USDC 的专用钱包。
