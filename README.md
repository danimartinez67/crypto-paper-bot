# Crypto Paper Trading Bot — Momentum + Breakouts

## Configuration
- Starting virtual balance: €15,000
- Assets: BTC, ETH, SOL
- Strategy: momentum + breakouts from observed real-price snapshots
- Snapshot refresh: every 5 minutes while the Streamlit browser session is open
- Stop-loss: 2%; take-profit: 4%
- Maximum risk budget per trade: 4% of equity (ceiling, not target)
- Position notional cap: 30% of equity, no leverage
- Simulated costs: 0.10% fee + 0.10% slippage on each side
- Daily loss circuit breaker: 8% of the day's starting equity
- DexScreener liquidity/volume filters: at least $500,000 liquidity and $1,000,000 24h volume for an eligible Solana stablecoin pair where available
- No wallet connection, private keys, exchange API keys, or real orders.

## Data and signals
- CoinGecko public endpoint supplies the reference EUR prices and 24h change.
- DexScreener public search endpoint supplies pair/liquidity/volume/5-minute change where a matching Solana stablecoin pair is available.
- Signal history is collected by this app session. It takes around 60 minutes of observations to begin evaluating breakouts.
- If an API fails or a qualifying pair is unavailable, the app displays the issue and does not fabricate values. The SOL automatic-entry filter requires an eligible DexScreener pair.

## Deploy on Streamlit Community Cloud
1. Download and unzip this archive.
2. Open your GitHub repository and select **Add file → Upload files**.
3. Upload `app.py`, `requirements.txt`, and `README.md` from the extracted folder, replacing the old files.
4. Click **Commit changes**.
5. Return to Streamlit Community Cloud. Wait for automatic redeploy; if needed choose **Manage app → Reboot app**.
6. Open the app, press **Consultar datos y ejecutar ciclo** once, and inspect the market/error tables.
7. Enable **Automatización demo activada** to refresh every five minutes while the session stays open.

## Important limitations
- This is a demonstration, not investment advice or a validated profitable strategy.
- Five-minute refresh depends on the app/browser session and is not guaranteed 24/7.
- Streamlit session state is in memory and may reset after restart/sleep.
- The short side is a theoretical paper position; no real borrowing or short sale occurs.
- A stop-loss is checked only when the next cycle runs and cannot guarantee a precise exit price.
