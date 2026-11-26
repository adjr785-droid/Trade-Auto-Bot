import streamlit as st
import requests
import json
import pandas as pd
import time
import base64

# --- REPLACE THESE WITH YOUR DETAILS ---
GITHUB_USER = "your_github_username"
REPO_NAME = "your_repo_name"
GITHUB_TOKEN = "your_personal_access_token"

st.set_page_config(page_title="Trading Bot Dashboard", layout="wide")
st.title("🤖 Bitget Trading Bot Dashboard")

def fetch_github_file(filepath):
    url = f"https://api.github.com/repos/{GITHUB_USER}/{REPO_NAME}/contents/{filepath}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        content = base64.b64decode(response.json()['content']).decode('utf-8')
        return json.loads(content)
    return {}

def trigger_github_action():
    url = f"https://api.github.com/repos/{GITHUB_USER}/{REPO_NAME}/actions/workflows/run.yml/dispatches"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    requests.post(url, headers=headers, json={"ref": "main"})
    st.toast("Workflow triggered! The bot will wake up in a few seconds.")

col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("📈 Active Positions")
    positions = fetch_github_file("positions.json")
    if not positions:
        st.info("No active positions right now.")
    else:
        df_data = []
        for sym, data in positions.items():
            df_data.append({
                "Coin": sym.replace("-USDT", ""),
                "Entry Price": data.get("entry_price"),
                "Stop Loss": data.get("stop_loss"),
                "Peak Price": data.get("peak_price"),
                "Strategy": data.get("strategy", "UNKNOWN")
            })
        st.dataframe(pd.DataFrame(df_data), use_container_width=True)

with col2:
    st.subheader("⚙️ Bot Controls")
    if st.button("🔄 Force Bot Run", use_container_width=True):
        trigger_github_action()
    
    st.write("---")
    
    st.subheader("💰 Portfolio")
    ledger = fetch_github_file("profit_ledger.json")
    st.metric("Total Realized PnL", f"${ledger.get('total_realized_usdt', 0.0):.2f}")
    st.metric("Total Trades", ledger.get('trade_count', 0))

st.write("---")
st.caption("Data refreshes every 60 seconds. Built for GitHub Actions.")
time.sleep(60)
st.rerun()
