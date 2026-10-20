# Crypto Paper Trading Bot

Aplicación Streamlit para simular operaciones LONG y SHORT en BTC, ETH y SOL con capital virtual de 800 €.

## Ejecutar localmente

```bash
python -m venv .venv
source .venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
streamlit run app.py
```

## Desplegar en Streamlit Community Cloud

1. Crea un repositorio público en GitHub.
2. Sube `app.py`, `requirements.txt` y `README.md` a la raíz del repositorio.
3. En Streamlit Community Cloud, crea una app desde ese repositorio y selecciona `app.py`.

## Datos y limitaciones

- Consulta manualmente CoinGecko API pública para precios en EUR y variación de 24 horas.
- La regla automática es didáctica: LONG si el cambio 24 h supera el umbral y SHORT si cae por debajo del umbral negativo.
- No hay órdenes reales ni conexión a una cuenta de exchange.
- El estado de sesión de Streamlit no es persistente y puede perderse al reiniciar.
