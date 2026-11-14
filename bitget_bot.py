import requests
import json
import os
import time
import ccxt

# Load from GitHub Secrets
TG_TOKEN = os.environ.get('TG_TOKEN')
TG_CHAT_ID = os.environ.get('TG_CHAT_ID')
DB_FILE = "positions.json"
STATE_FILE = "tg_state.json"
PENDING_FILE = "pending_trade.json"
SYNC_FILE = "sync_state.json"

STABLECOINS = ['USDC', 'USDT', 'DAI', 'FDUSD', 'TUSD', 'USDD', 'USDE', 'ONE', 'CT']
MAX_AUTO_BUY = 20.0 

# Initialize BITGET
exchange = ccxt.bitget({
    'apiKey': os.environ.get('BITGET_API_KEY'),
    'secret': os.environ.get('BITGET_API_SECRET'),
    'password': os.environ.get('BITGET_API_PASSPHRASE'),
    'enableRateLimit': True,
})

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

def load_json(file):
    if not os.path.exists(file): return {}
    with open(file, "r") as f: return json.load(f)

def save_json(file, data):
    with open(file, "w") as f: json.dump(data, f, indent=4)

# --- NEW: CLEAR MEMORY FUNCTION ---
def clear_memory():
    """Wipes all tracking files clean."""
    save_json(DB_FILE, {})
    save_json(PENDING_FILE, {})
    save_json(SYNC_FILE, {})
    send_telegram("🧹 *MEMORY CLEARED!*\nAll active trades, pending alerts, and sync history have been wiped clean.")

def get_market_data(symbol):
    try:
        ccxt_symbol = symbol.replace("-", "/")
        ohlcv = exchange.fetch_ohlcv(ccxt_symbol, timeframe='1h', limit=50)
        closes = [float(candle[4]) for candle in ohlcv[:-1]] 
        
        current_price = closes[-1]
        prev_close = closes[-2]
        
        gains = [max(0, closes[i] - closes[i-1]) for i in range(1, len(closes))]
        losses = [max(0, closes[i-1] - closes[i]) for i in range(1, len(closes))]
        avg_gain = sum(gains[-14:])/14
        avg_loss = sum(losses[-14:])/14
        rsi = 100.0 if avg_loss == 0 else round(100 - (100 / (1 + (avg_gain / avg_loss))), 2)
            
        ma5 = round(sum(closes[-5:]) / 5, 6)
        ma10 = round(sum(closes[-10:]) / 10, 6)
        return current_price, prev_close, rsi, ma5, ma10, min(closes[-5:])
    except Exception:
        return None, None, None, None, None, None

def execute_sell(symbol, percentage, reason):
    try:
        ccxt_symbol = symbol.replace("-", "/")
        base_coin = symbol.split("-")[0]
        balance = exchange.fetch_balance()
        free_balance = balance['free'].get(base_coin, 0)
        
        if free_balance > 0:
            qty_to_sell = float(exchange.amount_to_precision(ccxt_symbol, free_balance * percentage))
            exchange.create_market_sell_order(ccxt_symbol, qty_to_sell)
            send_telegram(f"✅ *AUTO-SOLD ({reason})*\n🪙 Sold {percentage*100}% of {symbol}.\n💰 Amount: {qty_to_sell} {base_coin}")
            return True
        else:
            send_telegram(f"⚠️ *WARNING:* Tried to sell {symbol}, but balance is 0.")
            return False
    except Exception as e:
        send_telegram(f"❌ *AUTO-SELL FAILED for {symbol}:*\n`{e}`")
        return False

def sync_wallet():
    db = load_json(DB_FILE)
    synced = load_json(SYNC_FILE)
    
    try:
        balance = exchange.fetch_balance()
        free = balance['free']
    except Exception:
        return

    for symbol in list(db.keys()):
        coin = symbol.split("-")[0]
        if free.get(coin, 0) < 0.0001: 
            del db[symbol]
            send_telegram(f"👻 *GHOST TRADE REMOVED:* {symbol} was manually sold. Memory cleared.")
            save_json(DB_FILE, db)

    for coin, amt in free.items():
        if amt > 0 and coin not in STABLECOINS:
            symbol = f"{coin}-USDT"
            if symbol not in db and coin not in synced:
                price, _, rsi, _, _, _ = get_market_data(symbol)
                if price:
                    usdt_value = amt * price
                    if usdt_value >= 1.02:
                        db[symbol] = {
                            "entry_price": price, "safe_zone": price, 
                            "stop_loss": round(price * 0.85, 6),
                            "took_profit_10": False, "took_profit_50": False
                        }
                        synced[coin] = True
                        save_json(DB_FILE, db)
                        save_json(SYNC_FILE, synced)
                        send_telegram(f"🔄 *MANUAL BUY DETECTED*\n🪙 Found {amt} of {coin} (Worth ~${usdt_value:.2f}).\n💰 Tracking Entry: ~{price}\n🛑 Stop Loss: 15%.")

def check_telegram_commands():
    state = load_json(STATE_FILE)
    last_id = state.get("last_update_id", 0)
    
    url = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates?offset={last_id + 1}"
    try:
        res = requests.get(url, timeout=10).json()
        if res.get("ok") and len(res.get("result", [])) > 0:
            for update in res["result"]:
                last_id = update["update_id"]
                if "message" in update and "text" in update["message"]:
                    text = update["message"]["text"].lower()
                    
                    if text == "/balance":
                        try:
                            bal = exchange.fetch_balance()
                            free = bal['free']
                            msg = "💰 *BITGET SPOT BALANCE*\n\n"
                            found = False
                            for coin, amt in free.items():
                                if amt > 0:
                                    msg += f"🪙 {coin}: `{amt}`\n"
                                    found = True
                            if not found: msg += "No funds available."
                            send_telegram(msg)
                        except Exception as e:
                            send_telegram(f"❌ *BALANCE FAILED:* {e}")

                    elif text == "/sync":
                        sync_wallet()
                        send_telegram("🔄 *WALLET SYNC FORCED.*")

                    # --- CLEAR COMMAND ---
                    elif text == "/clear":
                        clear_memory()

                    elif text.startswith("/buy"):
                        parts = text.split()
                        if len(parts) == 3:
                            coin = parts[1].upper()
                            usdt_amount = float(parts[2])
                            symbol = f"{coin}/USDT"
                            try:
                                ticker = exchange.fetch_ticker(symbol)
                                current_price = ticker['last']
                                raw_qty = usdt_amount / current_price
                                qty = float(exchange.amount_to_precision(symbol, raw_qty))
                                
                                exchange.create_market_buy_order(symbol, qty)
                                send_telegram(f"✅ *ORDER EXECUTED!*\n🪙 Bought {qty} of {coin}.\n💵 Spent: ~{usdt_amount} USDT\n💰 Price: {current_price}")
                                
                                db = load_json(DB_FILE)
                                db[coin + "-USDT"] = {
                                    "entry_price": current_price, "safe_zone": current_price, 
                                    "stop_loss": round(current_price * 0.85, 6),
                                    "took_profit_10": False, "took_profit_50": False
                                }
                                save_json(DB_FILE, db)
                                
                                pending = load_json(PENDING_FILE)
                                if coin + "-USDT" in pending:
                                    del pending[coin + "-USDT"]
                                    save_json(PENDING_FILE, pending)
                            except Exception as e:
                                send_telegram(f"❌ *ORDER FAILED:* {e}")
                        else:
                            send_telegram("❌ Format: `/buy [COIN] [USDT_AMOUNT]`.")

                    elif text.startswith("/sell"):
                        parts = text.split()
                        if len(parts) == 2:
                            coin = parts[1].upper()
                            symbol = f"{coin}-USDT"
                            if execute_sell(symbol, 1.0, "Manual Override"):
                                db = load_json(DB_FILE)
                                if symbol in db:
                                    del db[symbol]
                                    save_json(DB_FILE, db)
                        else:
                            send_telegram("❌ Format: `/sell [COIN]`.")

                    elif "/status" in text:
                        db = load_json(DB_FILE)
                        if not db: send_telegram("🛑 *STATUS:* No active trades.")
                        else:
                            for sym, data in db.items():
                                price, _, rsi, _, _, _ = get_market_data(sym)
                                if price:
                                    inc = ((price - data['entry_price']) / data['entry_price']) * 100
                                    send_telegram(f"🟢 *ACTIVE:* {sym}\n💰 Entry: {data['entry_price']}\n📊 Current: {price} ({inc:+.1f}%)\n🎯 RSI: {rsi}")
                                    
                    elif "/help" in text:
                        send_telegram("🤖 *COMMANDS:*\n/balance - Check funds\n/sync - Force wallet sync\n/buy [coin] [usdt] - Buy\n/sell [coin] - Sell All\n/clear - Wipe memory\n/status - Check active trades")
            
            state["last_update_id"] = last_id
            save_json(STATE_FILE, state)
    except Exception as e:
        print(f"Telegram Error: {e}")

def manage_active_trades():
    db = load_json(DB_FILE)
    if not db: return
    
    for symbol, data in list(db.items()):
        price, prev, rsi, ma5, ma10, _ = get_market_data(symbol)
        if not price: continue
        
        inc = ((price - data["entry_price"]) / data["entry_price"]) * 100
        
        if price <= data["stop_loss"]:
            if execute_sell(symbol, 1.0, "STOP LOSS"): del db[symbol]; save_json(DB_FILE, db)
            continue
        if rsi >= 75:
            if execute_sell(symbol, 1.0, "RSI 75 HIT"): del db[symbol]; save_json(DB_FILE, db)
            continue
        elif rsi >= 70 and not data.get("took_profit_50"):
            if execute_sell(symbol, 0.50, "RSI 70 HIT"): db[symbol]["took_profit_50"] = True; save_json(DB_FILE, db)
        elif inc >= 10.0 and not data.get("took_profit_10"):
            if execute_sell(symbol, 0.25, "+10% TARGET"): db[symbol]["took_profit_10"] = True; save_json(DB_FILE, db)

def check_pending_trades():
    pending = load_json(PENDING_FILE)
    if not pending: return
    
    current_time = int(time.time())
    
    for symbol, data in list(pending.items()):
        time_elapsed = current_time - data["timestamp"]
        
        if time_elapsed >= 900:
            try:
                balance = exchange.fetch_balance()
                usdt_balance = balance['free'].get('USDT', 0)
                
                target_spend = usdt_balance * 0.50 
                
                if target_spend < 1.02:
                    target_spend = usdt_balance 
                
                if target_spend > MAX_AUTO_BUY:
                    target_spend = MAX_AUTO_BUY
                
                if target_spend >= 1.02:
                    ticker = exchange.fetch_ticker(symbol.replace("-", "/"))
                    current_price = ticker['last']
                    raw_qty = target_spend / current_price
                    qty = float(exchange.amount_to_precision(symbol.replace("-", "/"), raw_qty))
                    
                    exchange.create_market_buy_order(symbol.replace("-", "/"), qty)
                    
                    db = load_json(DB_FILE)
                    db[symbol] = {
                        "entry_price": current_price, "safe_zone": current_price, 
                        "stop_loss": round(current_price * 0.85, 6),
                        "took_profit_10": False, "took_profit_50": False
                    }
                    save_json(DB_FILE, db)
                    
                    send_telegram(f"🤖 *AUTO-EXECUTED (15m Timeout)*\n🪙 Bought {qty} of {symbol}.\n💵 Spent: ~{target_spend:.2f} USDT\n💰 Price: {current_price}")
                else:
                    send_telegram(f"⚠️ *AUTO-BUY SKIPPED:* Not enough USDT.\nBalance: {usdt_balance:.2f} (Min required: 1.02)")
                
                del pending[symbol]
                save_json(PENDING_FILE, pending)
                
            except Exception as e:
                send_telegram(f"❌ *AUTO-BUY FAILED for {symbol}:*\n`{e}`")
                del pending[symbol]
                save_json(PENDING_FILE, pending)

def scan_for_new_trades():
    db = load_json(DB_FILE)
    pending = load_json(PENDING_FILE)
    if len(db) >= 1 or len(pending) >= 1: return 

    try: tickers = exchange.fetch_tickers()
    except Exception: return
        
    valid = []
    for sym, t in tickers.items():
        if sym.endswith('/USDT'):
            base = sym.split('/')[0]
            if base in STABLECOINS or (base.startswith('X') and len(base) <= 6): continue
            
            vol = t['quoteVolume'] if t['quoteVolume'] else 0
            if vol >= 1000000: 
                change = t['percentage'] if t['percentage'] else 0
                valid.append({'symbol': sym.replace("/", "-"), 'vol': vol, 'change': change})
                
    valid.sort(key=lambda x: x['vol'], reverse=True)
    to_check = valid[15:50] 
    
    candidates = []
    for t in to_check:
        price, prev, rsi, ma5, ma10, low = get_market_data(t['symbol'])
        if not price: continue
        
        if t['change'] > 1.0 and 42 <= rsi <= 48 and price > ma5 and ma5 > ma10:
            safe_entry = ma5
            sl = round(ma5 * 0.85, 6) 
            tp1 = round(safe_entry * 1.10, 6) 
            tp2 = round(safe_entry * 1.20, 6)
            rr = (tp1 - safe_entry) / (safe_entry - sl) if (safe_entry - sl) > 0 else 0
            if rr >= 1.8:
                candidates.append({"symbol": t['symbol'], "type": "TREND PULLBACK", "entry": safe_entry, "sl": sl, "tp1": tp1, "tp2": tp2, "rsi": rsi, "vol": t['vol'], "rr": rr})

        elif t['change'] < -8.0 and rsi < 30 and price > prev:
            safe_entry = round(low * 1.005, 6)
            sl = round(low * 0.85, 6)          
            tp1 = round(safe_entry * 1.10, 6)          
            tp2 = round(safe_entry * 1.20, 6)
            rr = (tp1 - safe_entry) / (safe_entry - sl) if (safe_entry - sl) > 0 else 0
            if rr >= 1.8:
                candidates.append({"symbol": t['symbol'], "type": "OVERSOLD BOUNCE", "entry": safe_entry, "sl": sl, "tp1": tp1, "tp2": tp2, "rsi": rsi, "vol": t['vol'], "rr": rr})
                
    candidates.sort(key=lambda x: x['vol'], reverse=True)
    
    if candidates:
        best = candidates[0] 
        coin_symbol = best['symbol'].split('-')[0] 
        
        pending[best['symbol']] = {"timestamp": int(time.time())}
        save_json(PENDING_FILE, pending)
        
        icon = "🏆" if best['type'] == "TREND PULLBACK" else "🩸"
        msg = (
            f"{icon} *{best['type']}*\n"
            f"🪙 *{best['symbol']}*\n\n"
            f"🎯 *Entry:* {best['entry']}\n"
            f"🛑 *Stop Loss:* {best['sl']} (15%)\n"
            f"💰 *TP1:* {best['tp1']} (10%)\n"
            f"💰 *TP2:* {best['tp2']} (20%)\n\n"
            f"📊 *RSI:* {best['rsi']} | *R:R:* 1:{best['rr']:.1f}\n\n"
            f"👉 *Approve now:* `/buy {coin_symbol} 50`\n"
            f"*(Auto-buys 50% of balance in 15m if no reply)*"
        )
        send_telegram(msg)

if __name__ == "__main__":
    check_telegram_commands() 
    sync_wallet() 
    manage_active_trades()    
    check_pending_trades() 
    scan_for_new_trades()     
