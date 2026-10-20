import time
from datetime import datetime, timezone

import requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Crypto Paper Bot", page_icon="📈", layout="wide")

API_URL = "https://api.coingecko.com/api/v3/simple/price"
COINS = {
    "BTC": {"id": "bitcoin", "name": "Bitcoin"},
    "ETH": {"id": "ethereum", "name": "Ethereum"},
    "SOL": {"id": "solana", "name": "Solana"},
}
FEE_RATE = 0.001  # 0.1% per side, simulated
DEFAULT_CAPITAL = 800.0

st.title("📈 Crypto Paper Trading Bot")
st.caption("Simulación únicamente · BTC / ETH / SOL · Sin órdenes ni dinero real")

if "cash" not in st.session_state:
    st.session_state.cash = DEFAULT_CAPITAL
if "starting_capital" not in st.session_state:
    st.session_state.starting_capital = DEFAULT_CAPITAL
if "positions" not in st.session_state:
    st.session_state.positions = {}
if "trades" not in st.session_state:
    st.session_state.trades = []
if "last_prices" not in st.session_state:
    st.session_state.last_prices = {}
if "last_market_data" not in st.session_state:
    st.session_state.last_market_data = None
if "last_update" not in st.session_state:
    st.session_state.last_update = None

def fetch_market_data():
    params = {
        "ids": ",".join(c["id"] for c in COINS.values()),
        "vs_currencies": "eur",
        "include_24hr_change": "true",
    }
    response = requests.get(API_URL, params=params, timeout=12,
                            headers={"accept": "application/json"})
    response.raise_for_status()
    payload = response.json()
    rows = []
    for ticker, coin in COINS.items():
        raw = payload.get(coin["id"])
        if not isinstance(raw, dict) or "eur" not in raw:
            raise ValueError(f"La API no devolvió un precio válido para {ticker}.")
        price = float(raw["eur"])
        change = raw.get("eur_24h_change")
        rows.append({
            "Ticker": ticker,
            "Criptomoneda": coin["name"],
            "Precio (€)": price,
            "Cambio 24 h (%)": float(change) if change is not None else None,
        })
    return pd.DataFrame(rows)

def get_price(ticker):
    return float(st.session_state.last_prices[ticker])

def position_value(position, price):
    return position["quantity"] * price

def unrealized_pnl(position, price):
    direction = 1 if position["side"] == "LONG" else -1
    return direction * position["quantity"] * (price - position["entry_price"])

def open_position(ticker, side, allocation_pct):
    if ticker in st.session_state.positions:
        st.warning(f"Ya existe una posición abierta en {ticker}.")
        return
    if ticker not in st.session_state.last_prices:
        st.error("Actualiza los precios antes de abrir una posición.")
        return
    price = get_price(ticker)
    allocation = st.session_state.cash * (allocation_pct / 100.0)
    entry_fee = allocation * FEE_RATE
    if allocation <= 0 or allocation + entry_fee > st.session_state.cash:
        st.error("Saldo virtual insuficiente.")
        return
    quantity = allocation / price
    st.session_state.cash -= allocation + entry_fee
    st.session_state.positions[ticker] = {
        "ticker": ticker,
        "side": side,
        "entry_price": price,
        "quantity": quantity,
        "notional": allocation,
        "entry_fee": entry_fee,
        "opened_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
    }
    st.session_state.trades.append({
        "Fecha": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "Activo": ticker, "Acción": f"ABRIR {side}",
        "Precio (€)": price, "Cantidad": quantity,
        "P&L realizado (€)": 0.0, "Comisión (€)": entry_fee,
    })
    st.success(f"Posición {side} simulada abierta en {ticker}.")

def close_position(ticker):
    position = st.session_state.positions.get(ticker)
    if not position:
        st.warning(f"No hay posición abierta en {ticker}.")
        return
    price = get_price(ticker)
    gross_pnl = unrealized_pnl(position, price)
    exit_notional = position["quantity"] * price
    exit_fee = exit_notional * FEE_RATE
    net_pnl = gross_pnl - position["entry_fee"] - exit_fee
    # Cash tracks initial capital less entry fees plus realized trading P&L.
    st.session_state.cash += position["notional"] + gross_pnl - exit_fee
    st.session_state.trades.append({
        "Fecha": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "Activo": ticker, "Acción": f"CERRAR {position['side']}",
        "Precio (€)": price, "Cantidad": position["quantity"],
        "P&L realizado (€)": net_pnl, "Comisión (€)": exit_fee,
    })
    del st.session_state.positions[ticker]
    st.success(f"Posición cerrada. P&L neto estimado: {net_pnl:+.2f} €")

def reset_simulation():
    st.session_state.cash = DEFAULT_CAPITAL
    st.session_state.starting_capital = DEFAULT_CAPITAL
    st.session_state.positions = {}
    st.session_state.trades = []

with st.sidebar:
    st.header("Configuración demo")
    allocation_pct = st.slider("Capital por operación (%)", min_value=5, max_value=50, value=10, step=5)
    threshold = st.slider("Umbral de señal 24 h (%)", min_value=0.5, max_value=5.0, value=1.0, step=0.5)
    st.caption("La señal automática usa el cambio de 24 h como regla sencilla de demostración, no como estrategia validada.")
    if st.button("Reiniciar simulación", use_container_width=True):
        reset_simulation()
        st.rerun()

left, right = st.columns([1, 1])
with left:
    if st.button("🔄 Consultar / actualizar precios", type="primary", use_container_width=True):
        try:
            market_df = fetch_market_data()
            st.session_state.last_market_data = market_df
            st.session_state.last_prices = dict(zip(market_df["Ticker"], market_df["Precio (€)"]))
            st.session_state.last_update = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            st.session_state.market_error = None
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else "desconocido"
            st.session_state.market_error = f"Error HTTP {code}. La API puede estar limitando temporalmente las consultas."
        except requests.RequestException as exc:
            st.session_state.market_error = f"Error de conexión: {exc}"
        except (ValueError, TypeError, KeyError) as exc:
            st.session_state.market_error = f"Datos recibidos no válidos: {exc}"
with right:
    st.info("Pulsa actualizar para consultar datos públicos reales. No se actualizan en segundo plano.")

if st.session_state.get("market_error"):
    st.error(st.session_state.market_error)
if st.session_state.last_update:
    st.caption(f"Última actualización correcta: {st.session_state.last_update}")

if st.session_state.last_market_data is None:
    st.warning("Todavía no hay datos de mercado cargados. Pulsa «Consultar / actualizar precios».")
else:
    st.subheader("Mercado actual")
    st.dataframe(
        st.session_state.last_market_data.style.format({
            "Precio (€)": "€{:,.2f}",
            "Cambio 24 h (%)": "{:+.2f}%",
        }, na_rep="N/D"),
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Señales de demostración")
    signal_rows = []
    for _, row in st.session_state.last_market_data.iterrows():
        change = row["Cambio 24 h (%)"]
        if pd.isna(change):
            signal = "SIN DATOS"
        elif change >= threshold:
            signal = "LONG"
        elif change <= -threshold:
            signal = "SHORT"
        else:
            signal = "ESPERAR"
        signal_rows.append({"Activo": row["Ticker"], "Cambio 24 h (%)": change, "Señal": signal})
    st.dataframe(pd.DataFrame(signal_rows), use_container_width=True, hide_index=True)

    st.subheader("Operar en modo simulado")
    cols = st.columns(3)
    for idx, ticker in enumerate(COINS):
        with cols[idx]:
            st.markdown(f"**{ticker} · {COINS[ticker]['name']}**")
            st.caption(f"Precio: €{get_price(ticker):,.2f}")
            if ticker in st.session_state.positions:
                p = st.session_state.positions[ticker]
                pnl = unrealized_pnl(p, get_price(ticker))
                st.write(f"Posición: **{p['side']}**")
                st.write(f"P&L abierto: **{pnl:+.2f} €**")
                st.button("Cerrar posición", key=f"close_{ticker}", use_container_width=True,
                          on_click=close_position, args=(ticker,))
            else:
                c1, c2 = st.columns(2)
                c1.button("🟢 LONG", key=f"long_{ticker}", use_container_width=True,
                          on_click=open_position, args=(ticker, "LONG", allocation_pct))
                c2.button("🔴 SHORT", key=f"short_{ticker}", use_container_width=True,
                          on_click=open_position, args=(ticker, "SHORT", allocation_pct))

    st.subheader("Automatización demo")
    auto_enabled = st.toggle("Activar señales automáticas (solo simulación)", value=False)
    if auto_enabled:
        st.warning("La automatización solo evalúa señales cuando pulsas actualizar. No mantiene un proceso continuo.")
        if st.button("Ejecutar una ronda automática", use_container_width=True):
            opened = []
            for _, row in st.session_state.last_market_data.iterrows():
                ticker, change = row["Ticker"], row["Cambio 24 h (%)"]
                if ticker in st.session_state.positions or pd.isna(change):
                    continue
                if change >= threshold:
                    open_position(ticker, "LONG", allocation_pct)
                    opened.append(ticker)
                elif change <= -threshold:
                    open_position(ticker, "SHORT", allocation_pct)
                    opened.append(ticker)
            if not opened:
                st.info("No hay nuevas señales que cumplan el umbral.")

    st.subheader("Cartera virtual")
    open_pnl = 0.0
    gross_exposure = 0.0
    for ticker, position in st.session_state.positions.items():
        price = st.session_state.last_prices.get(ticker, position["entry_price"])
        open_pnl += unrealized_pnl(position, price)
        gross_exposure += position_value(position, price)
    equity = st.session_state.cash + gross_exposure + sum(
        (unrealized_pnl(p, st.session_state.last_prices.get(t, p["entry_price"]))
         for t, p in st.session_state.positions.items()), 0.0
    )
    # Correct equity for shorts: cash is not credited with short-sale proceeds in this simplified model.
    # Display equity as cash plus marked position P&L plus reserved notional.
    equity = st.session_state.cash + sum(
        p["notional"] + unrealized_pnl(p, st.session_state.last_prices.get(t, p["entry_price"]))
        for t, p in st.session_state.positions.items()
    )
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Saldo disponible", f"€{st.session_state.cash:,.2f}")
    m2.metric("Valor cartera aprox.", f"€{equity:,.2f}")
    m3.metric("P&L no realizado", f"{open_pnl:+.2f} €")
    m4.metric("Rentabilidad aprox.", f"{(equity / st.session_state.starting_capital - 1) * 100:+.2f}%")

    if st.session_state.positions:
        pos_rows = []
        for ticker, p in st.session_state.positions.items():
            price = st.session_state.last_prices.get(ticker, p["entry_price"])
            pos_rows.append({
                "Activo": ticker, "Tipo": p["side"], "Entrada (€)": p["entry_price"],
                "Precio actual (€)": price, "Cantidad": p["quantity"],
                "P&L abierto (€)": unrealized_pnl(p, price), "Abierta": p["opened_at"],
            })
        st.dataframe(pd.DataFrame(pos_rows), use_container_width=True, hide_index=True)
    else:
        st.caption("No hay posiciones abiertas.")

st.subheader("Historial de operaciones")
if st.session_state.trades:
    st.dataframe(pd.DataFrame(st.session_state.trades).iloc[::-1], use_container_width=True, hide_index=True)
    csv = pd.DataFrame(st.session_state.trades).to_csv(index=False).encode("utf-8")
    st.download_button("Descargar historial CSV", csv, "historial_paper_trading.csv", "text/csv")
else:
    st.caption("Todavía no se han realizado operaciones simuladas.")

st.divider()
st.caption("Aviso: esto es un simulador educativo. El cambio de 24 h no predice el futuro. Las posiciones, el saldo y el historial se guardan en la sesión de Streamlit y pueden perderse al reiniciar la sesión. No hay conexión con exchanges ni ejecución real.")
