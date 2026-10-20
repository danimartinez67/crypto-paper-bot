import time
from datetime import datetime, date

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Crypto Paper Trading — Momentum", page_icon="📈", layout="wide")

STARTING_CASH = 15000.0
CHECK_EVERY_SECONDS = 300
FEE_RATE = 0.001       # 0.10% per side
SLIPPAGE_RATE = 0.001  # conservative simulated slippage per side
STOP_LOSS_PCT = 0.02
TAKE_PROFIT_PCT = 0.04
MAX_RISK_BUDGET_PCT = 0.04  # ceiling, not a target
MAX_NOTIONAL_PCT = 0.30     # no leverage: caps exposure to 30% of equity per position
MAX_OPEN_POSITIONS = 3
DAILY_LOSS_LIMIT_PCT = 0.08
MIN_LIQUIDITY_USD = 500_000
MIN_VOLUME_24H_USD = 1_000_000
MOMENTUM_LOOKBACK = 3       # three 5-minute intervals
BREAKOUT_LOOKBACK = 12      # previous 60 minutes of observed snapshots
MOMENTUM_THRESHOLD_PCT = 0.60
BREAKOUT_BUFFER_PCT = 0.15

COINS = {
    "BTC": {"id": "bitcoin", "name": "Bitcoin", "search": "WBTC USDC"},
    "ETH": {"id": "ethereum", "name": "Ethereum", "search": "WETH USDC"},
    "SOL": {"id": "solana", "name": "Solana", "search": "SOL USDC"},
}
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
DEX_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"

st.title("📈 Crypto Paper Trading Bot — Momentum + Breakouts")
st.caption("BTC · ETH · SOL | 15.000 € virtuales | Solo simulación: no hay wallet, claves ni órdenes reales")

defaults = {
    "cash": STARTING_CASH,
    "positions": {},
    "trades": [],
    "prices": {},
    "market_df": None,
    "last_update": None,
    "last_cycle": None,
    "last_error": None,
    "auto_enabled": False,
    "price_history": {ticker: [] for ticker in COINS},
    "signals": {},
    "daily_start_equity": STARTING_CASH,
    "daily_date": date.today().isoformat(),
    "halted_today": False,
}
for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value

def now_label():
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

def get_live_prices():
    params = {
        "ids": ",".join(c["id"] for c in COINS.values()),
        "vs_currencies": "eur",
        "include_24hr_change": "true",
    }
    response = requests.get(COINGECKO_URL, params=params, timeout=15)
    response.raise_for_status()
    payload = response.json()
    prices, changes = {}, {}
    for ticker, coin in COINS.items():
        item = payload.get(coin["id"], {})
        price = item.get("eur")
        if not isinstance(price, (int, float)) or price <= 0:
            raise ValueError(f"Precio EUR no válido o ausente para {ticker}.")
        prices[ticker] = float(price)
        change = item.get("eur_24h_change")
        changes[ticker] = float(change) if isinstance(change, (int, float)) else None
    return prices, changes

def get_dex_pair(ticker):
    """Read-only DexScreener search; choose a liquid Solana stablecoin pair if one exists."""
    query = COINS[ticker]["search"]
    response = requests.get(DEX_SEARCH_URL, params={"q": query}, timeout=15)
    response.raise_for_status()
    pairs = response.json().get("pairs") or []
    candidates = []
    target_symbols = {"BTC": {"WBTC", "BTC"}, "ETH": {"WETH", "ETH"}, "SOL": {"SOL"}}[ticker]
    stable_symbols = {"USDC", "USDT"}
    for pair in pairs:
        if pair.get("chainId") != "solana":
            continue
        base = (pair.get("baseToken") or {}).get("symbol", "").upper()
        quote = (pair.get("quoteToken") or {}).get("symbol", "").upper()
        # Require the target asset to be the base token and a stablecoin quote.
        if base not in target_symbols or quote not in stable_symbols:
            continue
        liquidity = (pair.get("liquidity") or {}).get("usd")
        volume = (pair.get("volume") or {}).get("h24")
        price_usd = pair.get("priceUsd")
        if not isinstance(liquidity, (int, float)) or not isinstance(volume, (int, float)):
            continue
        if not price_usd:
            continue
        candidates.append((float(liquidity), float(volume), pair))
    if not candidates:
        return {
            "dex_liquidity_usd": None, "dex_volume_24h_usd": None,
            "dex_change_5m_pct": None, "dex_pair_url": None,
            "dex_pair_ok": False, "dex_note": "No se encontró par Solana/stablecoin elegible",
        }
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    liquidity, volume, pair = candidates[0]
    change = (pair.get("priceChange") or {}).get("m5")
    change = float(change) if isinstance(change, (int, float)) else None
    eligible = liquidity >= MIN_LIQUIDITY_USD and volume >= MIN_VOLUME_24H_USD
    note = "OK" if eligible else f"Liquidez/volumen bajo (mínimos ${MIN_LIQUIDITY_USD:,.0f} / ${MIN_VOLUME_24H_USD:,.0f})"
    return {
        "dex_liquidity_usd": liquidity,
        "dex_volume_24h_usd": volume,
        "dex_change_5m_pct": change,
        "dex_pair_url": pair.get("url"),
        "dex_pair_ok": eligible,
        "dex_note": note,
    }

def current_equity():
    reserved = sum(p["notional"] for p in st.session_state.positions.values())
    unrealized = sum(unrealized_pnl(p, st.session_state.prices.get(t, p["entry"])) for t, p in st.session_state.positions.items())
    return st.session_state.cash + reserved + unrealized

def unrealized_pnl(position, price):
    direction = 1 if position["side"] == "LONG" else -1
    return direction * position["qty"] * (price - position["entry"])

def log_trade(ticker, action, price, qty, pnl=0.0, fees=0.0, note=""):
    st.session_state.trades.append({
        "Fecha": now_label(), "Activo": ticker, "Acción": action,
        "Precio (€)": price, "Cantidad": qty, "P&L neto (€)": pnl,
        "Comisiones + deslizamiento (€)": fees, "Motivo": note,
    })

def close_position(ticker, price, note="Cierre manual"):
    position = st.session_state.positions.get(ticker)
    if not position or not price or price <= 0:
        return
    gross = unrealized_pnl(position, price)
    exit_cost = position["qty"] * price * (FEE_RATE + SLIPPAGE_RATE)
    net = gross - position["entry_cost"] - exit_cost
    st.session_state.cash += position["notional"] + gross - exit_cost
    log_trade(ticker, f"CERRAR {position['side']}", price, position["qty"], pnl=net,
              fees=exit_cost, note=note)
    del st.session_state.positions[ticker]

def open_position(ticker, side, note="Señal manual"):
    if ticker in st.session_state.positions or len(st.session_state.positions) >= MAX_OPEN_POSITIONS:
        return
    if st.session_state.halted_today:
        return
    price = st.session_state.prices.get(ticker)
    if not price or price <= 0:
        return
    equity = current_equity()
    risk_budget = equity * MAX_RISK_BUDGET_PCT
    notional_cap = equity * MAX_NOTIONAL_PCT
    # Size is constrained by the 2% stop and a no-leverage exposure cap.
    notional = min(risk_budget / STOP_LOSS_PCT, notional_cap, st.session_state.cash / (1 + FEE_RATE + SLIPPAGE_RATE))
    if notional <= 0:
        return
    entry_cost = notional * (FEE_RATE + SLIPPAGE_RATE)
    qty = notional / price
    # Reserve notional and charge entry costs immediately.
    st.session_state.cash -= notional + entry_cost
    st.session_state.positions[ticker] = {
        "side": side, "entry": price, "qty": qty, "notional": notional,
        "entry_cost": entry_cost, "opened": now_label(),
        "stop": price * (1 - STOP_LOSS_PCT if side == "LONG" else 1 + STOP_LOSS_PCT),
        "target": price * (1 + TAKE_PROFIT_PCT if side == "LONG" else 1 - TAKE_PROFIT_PCT),
    }
    log_trade(ticker, f"ABRIR {side}", price, qty, fees=entry_cost, note=note)

def build_signal(ticker, live_change_24h, dex):
    history = st.session_state.price_history[ticker]
    price = st.session_state.prices[ticker]
    if len(history) < max(MOMENTUM_LOOKBACK + 1, BREAKOUT_LOOKBACK + 1):
        return "WAIT", "Recopilando observaciones reales (se necesitan ~60 min)", None
    previous = history[-1 - MOMENTUM_LOOKBACK]["price"]
    momentum = (price / previous - 1) * 100 if previous else None
    prior_prices = [item["price"] for item in history[-BREAKOUT_LOOKBACK-1:-1]]
    if not prior_prices:
        return "WAIT", "Histórico insuficiente", momentum
    breakout_high = max(prior_prices)
    breakout_low = min(prior_prices)
    breakout_up = price > breakout_high * (1 + BREAKOUT_BUFFER_PCT / 100)
    breakout_down = price < breakout_low * (1 - BREAKOUT_BUFFER_PCT / 100)
    dex_ok = ticker == "SOL" or dex.get("dex_pair_ok", False)
    # For BTC/ETH the Dex pair is a liquidity cross-check when available; the CoinGecko
    # price history remains the broad-market reference. SOL requires a qualifying Dex pair.
    if ticker == "SOL" and not dex_ok:
        return "NO TRADE", f"Filtro DexScreener SOL: {dex.get('dex_note', 'sin datos')}", momentum
    if momentum is not None and momentum >= MOMENTUM_THRESHOLD_PCT and breakout_up:
        return "LONG", f"Momentum +{momentum:.2f}% y ruptura del máximo observado", momentum
    if momentum is not None and momentum <= -MOMENTUM_THRESHOLD_PCT and breakout_down:
        return "SHORT", f"Momentum {momentum:.2f}% y ruptura del mínimo observado", momentum
    return "WAIT", "Sin confirmación simultánea de momentum y ruptura", momentum

def run_cycle():
    try:
        prices, changes = get_live_prices()
        dex_data = {}
        dex_errors = {}
        for ticker in COINS:
            try:
                dex_data[ticker] = get_dex_pair(ticker)
            except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
                dex_data[ticker] = {
                    "dex_liquidity_usd": None, "dex_volume_24h_usd": None,
                    "dex_change_5m_pct": None, "dex_pair_url": None,
                    "dex_pair_ok": False, "dex_note": f"Error DexScreener: {exc}",
                }
                dex_errors[ticker] = str(exc)

        st.session_state.prices = prices
        st.session_state.last_update = now_label()
        today = date.today().isoformat()
        if st.session_state.daily_date != today:
            st.session_state.daily_date = today
            st.session_state.daily_start_equity = current_equity()
            st.session_state.halted_today = False

        for ticker, price in prices.items():
            history = st.session_state.price_history[ticker]
            # Avoid duplicate snapshots from accidental reruns in less than 60 seconds.
            now_ts = time.time()
            if not history or now_ts - history[-1]["ts"] >= 60:
                history.append({"ts": now_ts, "price": price})
            st.session_state.price_history[ticker] = history[-(BREAKOUT_LOOKBACK + 5):]

        market_rows, signals = [], {}
        for ticker in COINS:
            dex = dex_data[ticker]
            signal, reason, momentum = build_signal(ticker, changes[ticker], dex)
            signals[ticker] = (signal, reason)
            market_rows.append({
                "Activo": ticker, "Precio (€)": prices[ticker],
                "Cambio 24 h CoinGecko (%)": changes[ticker],
                "Momentum observado (%)": momentum,
                "Cambio Dex 5 min (%)": dex.get("dex_change_5m_pct"),
                "Liquidez Dex (USD)": dex.get("dex_liquidity_usd"),
                "Volumen Dex 24 h (USD)": dex.get("dex_volume_24h_usd"),
                "Filtro liquidez": dex.get("dex_note"),
                "Par DexScreener": dex.get("dex_pair_url") or "No disponible",
            })

        # First handle exits using live prices and paper-only rules.
        for ticker in list(st.session_state.positions.keys()):
            position = st.session_state.positions[ticker]
            price = prices[ticker]
            side = position["side"]
            hit_stop = price <= position["stop"] if side == "LONG" else price >= position["stop"]
            hit_target = price >= position["target"] if side == "LONG" else price <= position["target"]
            opposite = signals.get(ticker, ("WAIT", ""))[0]
            if hit_stop:
                close_position(ticker, price, "Stop-loss simulado")
            elif hit_target:
                close_position(ticker, price, "Take-profit simulado")
            elif (side == "LONG" and opposite == "SHORT") or (side == "SHORT" and opposite == "LONG"):
                close_position(ticker, price, "Señal contraria")

        # Daily loss circuit breaker, measured against today's starting equity.
        if current_equity() <= st.session_state.daily_start_equity * (1 - DAILY_LOSS_LIMIT_PCT):
            st.session_state.halted_today = True
            for ticker in list(st.session_state.positions.keys()):
                close_position(ticker, prices[ticker], "Límite de pérdida diaria: cierre simulado")
        if st.session_state.auto_enabled and not st.session_state.halted_today:
            for ticker, (signal, reason) in signals.items():
                if signal in ("LONG", "SHORT") and ticker not in st.session_state.positions:
                    open_position(ticker, signal, reason)

        st.session_state.market_df = pd.DataFrame(market_rows)
        st.session_state.signals = signals
        st.session_state.last_cycle = now_label()
        st.session_state["last_cycle_ts"] = time.time()
        st.session_state.last_error = None
        if dex_errors:
            st.session_state.last_error = "DexScreener no respondió para: " + ", ".join(dex_errors.keys()) + ". Se muestran datos disponibles y se bloquean entradas cuando falta la liquidez requerida."
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        st.session_state.last_error = f"No se pudo completar el ciclo: {exc}"

with st.sidebar:
    st.header("Riesgo (simulación)")
    st.write(f"Capital virtual inicial: **€{STARTING_CASH:,.2f}**")
    st.write(f"Riesgo máximo teórico por operación: **4% del equity**")
    st.write(f"Exposición máxima por posición: **30% del equity, sin apalancamiento**")
    st.write(f"Stop-loss: **{STOP_LOSS_PCT*100:.0f}%** · Take-profit: **{TAKE_PROFIT_PCT*100:.0f}%**")
    st.write(f"Comisión simulada: **{FEE_RATE*100:.2f}% por lado**")
    st.write(f"Deslizamiento simulado: **{SLIPPAGE_RATE*100:.2f}% por lado**")
    st.write(f"Límite de pérdida diaria: **{DAILY_LOSS_LIMIT_PCT*100:.0f}%**")
    st.write(f"Filtros Dex: liquidez ≥ **${MIN_LIQUIDITY_USD:,.0f}** y volumen 24 h ≥ **${MIN_VOLUME_24H_USD:,.0f}**")
    st.caption("El 4% es un techo de presupuesto de riesgo, no una pérdida objetivo. El tamaño se limita además al 30% del equity por posición y al efectivo disponible.")
    st.session_state.auto_enabled = st.toggle("Automatización demo activada", value=st.session_state.auto_enabled)
    if st.button("Reiniciar simulación", use_container_width=True):
        st.session_state.cash = STARTING_CASH
        st.session_state.positions = {}
        st.session_state.trades = []
        st.session_state.prices = {}
        st.session_state.market_df = None
        st.session_state.price_history = {ticker: [] for ticker in COINS}
        st.session_state.signals = {}
        st.session_state.daily_start_equity = STARTING_CASH
        st.session_state.daily_date = date.today().isoformat()
        st.session_state.halted_today = False
        st.session_state.last_cycle = None
        st.session_state.last_error = None
        st.rerun()

st.info("La actualización cada 5 minutos funciona solo mientras la sesión de Streamlit permanece activa. El estado está en memoria y puede perderse al reiniciar. No es un servicio 24/7.")
left, right = st.columns([1, 1])
with left:
    if st.button("▶ Consultar datos y ejecutar ciclo", type="primary", use_container_width=True):
        run_cycle()
with right:
    st.write(f"Último ciclo: {st.session_state.last_cycle or 'Todavía no ejecutado'}")

if st.session_state.auto_enabled:
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=CHECK_EVERY_SECONDS * 1000, key="auto_refresh_5m")
    except ImportError:
        st.warning("Falta streamlit-autorefresh. Comprueba requirements.txt.")
    if st.session_state.last_cycle is None:
        run_cycle()
    elif st.session_state.last_cycle:
        # Each auto-refresh rerun performs the next paper cycle.
        # The timer only works while the browser session is open.
        if st.session_state.get("last_cycle_ts") is None or time.time() - st.session_state["last_cycle_ts"] >= CHECK_EVERY_SECONDS - 5:
            run_cycle()
            st.session_state["last_cycle_ts"] = time.time()

if st.session_state.last_error:
    st.warning(st.session_state.last_error)
if st.session_state.last_update:
    st.caption(f"Última consulta de precios: {st.session_state.last_update}")

if st.session_state.market_df is not None:
    st.subheader("Mercado y liquidez real")
    st.dataframe(
        st.session_state.market_df.style.format({
            "Precio (€)": "€{:,.4f}", "Cambio 24 h CoinGecko (%)": "{:+.2f}%",
            "Momentum observado (%)": "{:+.2f}%", "Cambio Dex 5 min (%)": "{:+.2f}%",
            "Liquidez Dex (USD)": "${:,.0f}", "Volumen Dex 24 h (USD)": "${:,.0f}",
        }, na_rep="N/D"),
        use_container_width=True, hide_index=True
    )
    st.subheader("Señales")
    signal_rows = [{"Activo": ticker, "Señal": value[0], "Motivo": value[1]}
                   for ticker, value in st.session_state.signals.items()]
    st.dataframe(pd.DataFrame(signal_rows), use_container_width=True, hide_index=True)

    st.subheader("Posiciones virtuales")
    cards = st.columns(3)
    for idx, ticker in enumerate(COINS):
        with cards[idx]:
            st.markdown(f"**{ticker} · {COINS[ticker]['name']}**")
            price = st.session_state.prices.get(ticker)
            if price:
                st.write(f"Precio: €{price:,.4f}")
            position = st.session_state.positions.get(ticker)
            if position and price:
                pnl = unrealized_pnl(position, price)
                st.write(f"{position['side']} · P&L abierto: {pnl:+.2f} €")
                st.write(f"Stop: €{position['stop']:,.4f} · Objetivo: €{position['target']:,.4f}")
                st.button("Cerrar posición", key=f"close_{ticker}", on_click=close_position,
                          args=(ticker, price, "Cierre manual"))
            else:
                c1, c2 = st.columns(2)
                c1.button("LONG", key=f"long_{ticker}", on_click=open_position, args=(ticker, "LONG"))
                c2.button("SHORT", key=f"short_{ticker}", on_click=open_position, args=(ticker, "SHORT"))

    reserved = sum(p["notional"] for p in st.session_state.positions.values())
    open_pnl = sum(unrealized_pnl(p, st.session_state.prices.get(t, p["entry"]))
                   for t, p in st.session_state.positions.items())
    equity = st.session_state.cash + reserved + open_pnl
    m1, m2, m3 = st.columns(3)
    m1.metric("Saldo disponible", f"€{st.session_state.cash:,.2f}")
    m2.metric("Equity virtual", f"€{equity:,.2f}", f"{equity-STARTING_CASH:+,.2f} €")
    m3.metric("P&L abierto", f"€{open_pnl:+,.2f}")
    st.write(f"Estado del límite diario: **{'BLOQUEADO' if st.session_state.halted_today else 'Activo'}**")
    if st.session_state.positions:
        rows = []
        for ticker, position in st.session_state.positions.items():
            price = st.session_state.prices.get(ticker, position["entry"])
            rows.append({
                "Activo": ticker, "Dirección": position["side"], "Entrada (€)": position["entry"],
                "Precio actual (€)": price, "Nominal (€)": position["notional"],
                "P&L abierto (€)": unrealized_pnl(position, price),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

st.subheader("Historial de operaciones")
if st.session_state.trades:
    history_df = pd.DataFrame(st.session_state.trades)
    st.dataframe(history_df.iloc[::-1], use_container_width=True, hide_index=True)
    st.download_button("Descargar historial CSV", history_df.to_csv(index=False).encode("utf-8"),
                       "paper_trading_history.csv", "text/csv")
else:
    st.caption("Todavía no hay operaciones registradas.")

st.caption("Las señales necesitan aproximadamente 60 minutos de observaciones reales recopiladas por esta sesión. Momentum y rupturas son reglas experimentales, no una estrategia validada. Los datos DexScreener pueden no incluir un par elegible; en ese caso se muestra el motivo y no se inventan valores. Los SHORT son posiciones teóricas de simulación, no ventas reales.")
