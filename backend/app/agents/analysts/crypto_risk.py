"""Agent 07 — Crypto / Risk Appetite.

Bitcoin is used here as a risk-appetite thermometer, not as an asset. It
trades every hour of every day, so when India is shut it is one of the few
things still pricing global risk.

The correlation evidence is what makes this agent narrow. BTC against equity
indices averages roughly 0.30 and sits near zero in a calm market; it runs
toward 1.0 in a dislocation, when everything risky falls at once. So the
information is **one-sided**: BTC down 5% overnight is a reason to take less
risk today, while BTC up 5% says nothing about Indian equities.

The prompt says that, and `CryptoRiskOutput` enforces it -- a bullish view
is downgraded to neutral by the schema. Relying on the prompt alone would
mean relying on a model to stay disciplined about an asymmetry on a day when
the number is dramatic.
"""
from __future__ import annotations

from ...collectors import crypto
from ...store import macro
from ..base import Agent, AgentConfig
from ..llm import ModelTier
from ..schemas import CryptoRiskOutput
from . import _shared as sh

SCHEMA_EXTRA = '  "risk_appetite": "improving" | "stable" | "deteriorating"'

TASK = f"""Read the crypto data below as a measure of global risk appetite.

This agent is deliberately one-sided:
- A sharp fall in BTC is a warning. Global risk appetite is deteriorating and
  Indian equities should be traded smaller today.
- A rally in BTC is NOT a buy signal for Indian equities. BTC-equity
  correlation is near zero in calm markets and only rises in a sell-off, so
  the upside carries no information for us.
- If the move is small either way -- under about 2% in 24 hours -- the honest
  answer is neutral with low confidence.
- Set risk_flag when the fall is large or fast enough that it looks like a
  broad risk-off move rather than a crypto-specific one.

Never return a bullish view. If the data looks positive, return neutral and
explain why in the reasoning.

{sh.HONESTY_RULE}"""


class CryptoRiskAgent(Agent):
    output_model = CryptoRiskOutput

    def __init__(self) -> None:
        super().__init__(AgentConfig(
            name="crypto_risk",
            model=ModelTier.FAST,
            prompt_version="1.0",
            max_tokens=500,
            freshness_seconds=1800,
            interval_seconds=1800,
            window="always",
        ))

    async def gather(self) -> str:
        state = crypto.risk_state()
        if state.get("available"):
            btc = {
                "BTC price (USDT)": sh.num(state["btc_price"]),
                "BTC 24h change": sh.pct(state["change_24h_pct"]),
                "classification": state["signal"],
                "as of": state["as_of"],
            }
        else:
            btc = {"status": "no crypto data collected yet"}

        eth_price = macro.latest("CRYPTO_ETHUSDT")
        eth_change = macro.latest("CRYPTO_ETHUSDT_CHG24")
        eth = {
            "ETH price (USDT)": sh.num(eth_price["value"] if eth_price else None),
            "ETH 24h change": sh.pct(eth_change["value"] if eth_change else None),
        }

        return sh.join(
            sh.block("BITCOIN", btc),
            sh.block("ETHEREUM (confirms or contradicts BTC)", eth),
            sh.block("HOW TO READ THIS", [
                "BTC and ETH falling together is a broader risk-off signal "
                "than either one alone.",
                "One falling while the other holds is usually a "
                "crypto-specific story and not an equity signal.",
            ]),
        )

    def task_prompt(self) -> str:
        return TASK

    def schema_hint(self) -> str:
        return sh.schema_block(SCHEMA_EXTRA)


agent = CryptoRiskAgent()
