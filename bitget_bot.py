import requests, json, os, time, ccxt, subprocess, traceback, re
from google import genai

TG_TOKEN = os.environ.get('TG_TOKEN')
TG_CHAT_ID = os.environ.get('TG_CHAT_ID')
DB_FILE = "positions.json"
STATE_FILE = "tg_state.json"
SETTINGS_FILE = "settings.json"
QUEUE_FILE = "queue.json"
LEDGER_FILE = "profit_ledger.json"
COOLDOWN_FILE = ".cmd_cooldown.json"

STABLECOINS = ['USDC','USDT','DAI','FDUSD','TUSD','USDD','USDE']
LEV = ['3L','3S','5L','5S','10L','10S','2L','2S']
BL = ['RPANW','CT','ONE']

exchange = ccxt.bitget({
    'apiKey': os.environ.get('BITGET_API_KEY'),
    'secret': os.environ.get('BITGET_API_SECRET'),
    'password': os.environ.get('BITGET_API_PASSPHRASE'),
    'enableRateLimit': True
})
client = genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))

_CACHE = {}
_CACHE_TTL = 20  # seconds

# ================= TELEGRAM =================

def tg(m):
    """Markdown message with plain-text fallback."""
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": m, "parse_mode": "Markdown"},
            timeout=10
        )
        if not r.ok:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT_ID, "text": m},
                timeout=10
            )
    except:
        pass

def tg_ai(m):
    """HTML message with plain-text fallback."""
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": m, "parse_mode": "HTML"},
            timeout=10
        )
        if not r.ok:
            clean = re.sub(r'<[^>]+>', '', m)
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT_ID, "text": clean},
                timeout=10
            )
    except:
        pass

# ================= IO =================

def load(f, default=None):
    if default is None:
        default = {}
    try:
        if os.path.exists(f):
            with open(f) as fh:
                return json.load(fh)
        return default
    except:
        return default

def save(f, d):
    try:
        with open(f, "w") as fh:
            json.dump(d, fh, indent=4)
    except Exception as e:
        print(f"save {f} failed: {e}")

def settings():
    d = {
        "rsi_min": 42, "rsi_max": 48, "stop_loss_pct": 15, "sl_sell_percent": 0.5,
        "dca_reserve": 2.0, "auto_buy_enabled": True, "auto_buy_amount_usdt": 5.75,
        "max_tokens_before_consolidation": 3
    }
    s = load(SETTINGS_FILE, d)
    for k, v in d.items():
        s.setdefault(k, v)
    return s

def cooldown(key, secs=30):
    """True if still cooling down (i.e. should skip)."""
    d = load(COOLDOWN_FILE, {})
    now = time.time()
    if now - d.get(key, 0) < secs:
        return True
    d[key] = now
    save(COOLDOWN_FILE, d)
    return False

# ================= MARKET DATA =================

def _compute_data(sym):
    try:
        o = exchange.fetch_ohlcv(sym.replace("-", "/"), '1h', limit=50)
        c = [float(k[4]) for k in o[:-1]]  # closed candles only
        g = [max(0, c[i] - c[i-1]) for i in range(1, len(c))]
        l = [max(0, c[i-1] - c[i]) for i in range(1, len(c))]
        ag = sum(g[-14:]) / 14
        al = sum(l[-14:]) / 14
        r = 100.0 if al == 0 else round(100 - (100 / (1 + (ag / al))), 2)
        return c[-1], c[-2], r, round(sum(c[-5:]) / 5, 6), round(sum(c[-10:]) / 10, 6)
    except:
        return None, None, None, None, None

def data(sym):
    """Cached analysis data (closed candles, RSI, MAs)."""
    now = time.time()
    hit = _CACHE.get(sym)
    if hit and (now - hit[0]) < _CACHE_TTL:
        return hit[1]
    result = _compute_data(sym)
    _CACHE[sym] = (now, result)
    return result

def live_price(sym):
    """Live ticker price — use this for ANY execution decision."""
    try:
        return exchange.fetch_ticker(sym.replace("-", "/"))['last']
    except:
        return None

def clear_cache():
    _CACHE.clear()

# ================= EXCHANGE HELPERS =================

def cancel_sell_orders(sym):
    cs = sym.replace("-", "/")
    try:
        for o in exchange.fetch_open_orders(cs):
            if o['side'] == 'sell':
                exchange.cancel_order(o['id'], cs)
    except:
        pass

def market_buy(cs, usdt):
    """Market buy, returns (filled_qty, avg_price)."""
    p = live_price(cs.replace("/", "-"))
    if not p:
        raise Exception(f"No live price for {cs}")
    qty = float(exchange.amount_to_precision(cs, usdt / p))
    order = exchange.create_market_buy_order(cs, qty)
    filled = order.get('filled')
    avg = order.get('average')
    if not filled or not avg:
        time.sleep(1.0)
        try:
            o = exchange.fetch_order(order['id'], cs)
            filled = o.get('filled', qty)
            avg = o.get('average', p)
        except:
            filled, avg = qty, p
    return float(filled), float(avg)

def hard_sl(sym, qty, pr):
    """Place stop-loss sell on Bitget. Returns True on success."""
    cs = sym.replace("-", "/")
    try:
        cancel_sell_orders(sym)
        if qty <= 0:
            return True
        params = {
            'triggerPrice': pr,
            'triggerType': 'market_price',
            'stopLossPrice': pr,
        }
        exchange.create_order(cs, 'market', 'sell', qty, None, params)
        return True
    except Exception as e:
        tg(f"⚠️ *SL PLACEMENT FAILED* {sym} @ {pr}: `{e}`")
        return False

def sell(sym, pct, reason):
    """Sell `pct` of total balance. Returns True on success (incl. zero balance)."""
    try:
        cs = sym.replace("-", "/")
        coin = sym.split("-")[0]
        
        # Cancel open sells so `free` reflects total
        cancel_sell_orders(sym)
        time.sleep(0.5)
        
        fb = exchange.fetch_balance()['free'].get(coin, 0)
        if fb <= 0:
            return True  # nothing left = success
        
        p = live_price(sym)
        if not p:
            return False
        
        qty = fb * pct
        
        # Dust: upgrade to full sell
        if qty * p < 1.02 and pct < 1.0:
            qty = fb
            reason += " (dust→full)"
        
        qty = float(exchange.amount_to_precision(cs, qty))
        if qty <= 0:
            return True
        
        exchange.create_market_sell_order(cs, qty)
        
        db = load(DB_FILE)
        if sym in db:
            pnl = (p - db[sym]["entry_price"]) * qty
            L = load(LEDGER_FILE)
            L['total_realized_usdt'] = L.get('total_realized_usdt', 0.0) + pnl
            L['trade_count'] = L.get('trade_count', 0) + 1
            save(LEDGER_FILE, L)
            emoji = '🟢' if pnl >= 0 else '🔴'
            tg(f"✅ *SOLD ({reason})*\n🪙 {qty} {sym}\n{emoji} PnL: ${pnl:.2f}")
        return True
    except Exception as e:
        tg(f"❌ *SELL FAILED* {sym}: `{e}`")
        return False

# ================= AI =================

def ask_ai(prompt):
    try:
        db = load(DB_FILE)
        q = load(QUEUE_FILE)
        s = settings()
        bal = exchange.fetch_balance()['free'].get('USDT', 0)
        _, _, btc_rsi, _, _ = data("BTC-USDT")
        
        pos_lines = []
        for sym, d in list(db.items())[:5]:
            p = live_price(sym)
            if p:
                pnl = ((p - d['entry_price']) / d['entry_price']) * 100
                pos_lines.append(f"  {sym}: entry={d['entry_price']}, now={p}, pnl={pnl:+.1f}%")
        if not pos_lines:
            pos_lines = ["  (none)"]
        
        context = (
            f"System Data:\n"
            f"- USDT Balance: {bal:.2f}\n"
            f"- Auto-Buy Size: ${s['auto_buy_amount_usdt']}\n"
            f"- BTC RSI: {btc_rsi}\n"
            f"- Active Positions ({len(db)}):\n" + "\n".join(pos_lines) + "\n"
            f"- Queue size: {len(q)}\n"
            f"- Rules: RSI {s['rsi_min']}-{s['rsi_max']}, SL {s['stop_loss_pct']}%\n\n"
        )
        ai_prompt = (
            "You are an expert crypto trading analyst. Use this live data to answer the user. "
            "Format using Telegram HTML tags (<b>, <i>). Use newlines, NOT <br>.\n"
            f"{context}\nUser Question: {prompt}"
        )
        response = client.models.generate_content(
            model='gemini-2.5-flash', contents=ai_prompt
        )
        text = response.text or "(no response)"
        tg_ai(f"🧠 <b>AI ANALYST:</b>\n\n{text}")
    except Exception as e:
        tg(f"❌ AI Error: {e}")

# ================= MANAGE =================

def manage():
    db = load(DB_FILE)
    s = settings()
    for sym, d in list(db.items()):
        _, _, r, _, _ = data(sym)
        live = live_price(sym)
        if not live:
            continue
        
        inc = ((live - d["entry_price"]) / d["entry_price"]) * 100
        coin = sym.split('-')[0]
        
        # ---- Stop-loss handling ----
        if live <= d["stop_loss"]:
            if not d.get("sl_hit"):
                pct = s.get('sl_sell_percent', 0.5)
                total_bal = exchange.fetch_balance()['total'].get(coin, 0)
                if (total_bal * pct * live) < 1.02:
                    pct = 1.0
                
                if sell(sym, pct, "PARTIAL STOP LOSS"):
                    rem = exchange.fetch_balance()['total'].get(coin, 0)
                    # Fully closed (either by request or dust rule)
                    if rem <= 0 or (rem * live) < 1.02:
                        db.pop(sym, None)
                        save(DB_FILE, db)
                        continue
                    
                    db[sym]["sl_hit"] = True
                    new_sl = round(live * (1 - s['stop_loss_pct']/100), 6)
                    db[sym]["stop_loss"] = new_sl
                    save(DB_FILE, db)
                    hard_sl(sym, rem, new_sl)
                    tg(f"⚠️ *PARTIAL STOP LOSS!*\nSold {pct*100:.0f}% of {sym}.\nRemaining {rem:.6f} protected at {new_sl}.")
            else:
                if sell(sym, 1.0, "FINAL STOP LOSS"):
                    db.pop(sym, None)
                    save(DB_FILE, db)
            continue
        
        # ---- Profit-taking ----
        if r is not None and r >= 75:
            if sell(sym, 1.0, "RSI 75"):
                db.pop(sym, None)
                save(DB_FILE, db)
        elif r is not None and r >= 70 and not d.get("took_profit_50"):
            if sell(sym, 0.5, "RSI 70"):
                if sym in db:
                    db[sym]["took_profit_50"] = True
                    save(DB_FILE, db)
        elif inc >= 10.0 and not d.get("took_profit_10"):
            if sell(sym, 0.25, "+10%"):
                if sym in db:
                    db[sym]["took_profit_10"] = True
                    save(DB_FILE, db)

def consolidate():
    db = load(DB_FILE)
    if len(db) <= 2:
        return
    tg("⚖️ *CONSOLIDATING...*")
    rk = []
    for sym, d in db.items():
        p, _, _, _, _ = data(sym)
        if p and "entry_price" in d:
            rk.append({"s": sym, "pnl": ((p - d['entry_price']) / d['entry_price']) * 100})
    rk.sort(key=lambda x: x["pnl"], reverse=True)
    for t in rk[2:]:
        if sell(t['s'], 1.0, "Consolidation"):
            db.pop(t['s'], None)
            save(DB_FILE, db)

# ================= QUEUE EXECUTION =================

def exec_queue():
    q = load(QUEUE_FILE)
    db = load(DB_FILE)
    s = settings()
    if not q:
        return
    sq = sorted(q.items(), key=lambda x: x[1].get('score', 0), reverse=True)
    
    try:
        ub = exchange.fetch_balance()['free'].get('USDT', 0)
        for sym, qd in sq:
            p = live_price(sym)
            if not p:
                continue
            
            is_dca = sym in db
            spendable = ub if is_dca else (ub - s.get('dca_reserve', 2.0))
            
            # ---- Free funds from weakest position if needed ----
            if spendable < 1.02 and not is_dca and len(db) > 0:
                weakest_sym = None
                lowest = 999
                for h_sym, h in db.items():
                    hp, _, _, _, _ = data(h_sym)
                    if hp:
                        pnl = ((hp - h['entry_price']) / h['entry_price']) * 100
                        if pnl < lowest:
                            lowest = pnl
                            weakest_sym = h_sym
                
                if weakest_sym:
                    base_coin = weakest_sym.split('-')[0]
                    cancel_sell_orders(weakest_sym)
                    time.sleep(0.5)
                    h_bal = exchange.fetch_balance()['free'].get(base_coin, 0)
                    hp = live_price(weakest_sym)
                    
                    if h_bal > 0 and hp:
                        val = min(s['auto_buy_amount_usdt'], h_bal * hp)
                        qty_sell = float(exchange.amount_to_precision(
                            weakest_sym.replace("-", "/"), val / hp))
                        
                        if qty_sell * hp < (h_bal * hp) * 0.95:
                            exchange.create_market_sell_order(
                                weakest_sym.replace("-", "/"), qty_sell)
                            tg(f"🔄 *PARTIAL SWAP:* Sold {qty_sell} {base_coin} to fund {sym}.")
                            
                            time.sleep(2)
                            ub = exchange.fetch_balance()['free'].get('USDT', 0)
                            spendable = ub - s.get('dca_reserve', 2.0)
                            
                            rem = exchange.fetch_balance()['total'].get(base_coin, 0)
                            if rem > 0 and (rem * hp) > 1.02:
                                hard_sl(weakest_sym, rem, db[weakest_sym]['stop_loss'])
                            else:
                                db.pop(weakest_sym, None)
                                save(DB_FILE, db)
            
            if spendable < 1.02:
                q.pop(sym, None)
                save(QUEUE_FILE, q)
                continue
            
            sp = min(spendable, s['auto_buy_amount_usdt'])
            cs = sym.replace("-", "/")
            coin = sym.split('-')[0]
            
            # ---- DCA branch ----
            if is_dca:
                if db[sym].get('dca_count', 0) >= 2:
                    q.pop(sym, None)
                    save(QUEUE_FILE, q)
                    continue
                
                old_total = exchange.fetch_balance()['total'].get(coin, 0)
                try:
                    filled, avg = market_buy(cs, sp)
                except Exception as e:
                    tg(f"❌ DCA buy failed for {sym}: {e}")
                    continue
                
                new_total = old_total + filled
                old_entry = db[sym]['entry_price']
                new_entry = (
                    ((old_total * old_entry) + (filled * avg)) / new_total
                    if new_total > 0 else avg
                )
                new_sl = round(new_entry * (1 - s['stop_loss_pct']/100), 6)
                
                db[sym].update({
                    'entry_price': round(new_entry, 6),
                    'stop_loss': new_sl,
                    'took_profit_10': False,
                    'took_profit_50': False,
                    'dca_count': db[sym].get('dca_count', 0) + 1,
                    'sl_hit': False,
                })
                save(DB_FILE, db)
                hard_sl(sym, new_total * s.get('sl_sell_percent', 0.5), new_sl)
                tg(f"🛒 *AUTO-DCA EXECUTED!*\n{sym} | 💵 {sp:.2f} @ {avg:.6f}\n📉 New Entry: {new_entry:.6f}\n🛑 New SL: {new_sl}")
            
            # ---- New position branch ----
            else:
                if len(db) >= s["max_tokens_before_consolidation"]:
                    continue
                
                try:
                    filled, avg = market_buy(cs, sp)
                except Exception as e:
                    tg(f"❌ Buy failed for {sym}: {e}")
                    continue
                
                # Guard against stale/tight SL from queue — recompute from actual fill
                max_sl = round(avg * (1 - s['stop_loss_pct']/100), 6)
                sl = qd.get('sl') or max_sl
                if sl >= avg or sl > max_sl:
                    sl = max_sl
                
                db[sym] = {
                    "entry_price": round(avg, 6),
                    "safe_zone": round(avg, 6),
                    "stop_loss": sl,
                    "took_profit_10": False,
                    "took_profit_50": False,
                    "dca_count": 0,
                    "sl_hit": False,
                }
                save(DB_FILE, db)
                hard_sl(sym, filled * s.get('sl_sell_percent', 0.5), sl)
                tg(f"🛒 *EXECUTED!*\n{sym} | 💵 {sp:.2f} @ {avg:.6f}\n🛑 SL: {sl}")
            
            q.pop(sym, None)
            save(QUEUE_FILE, q)
            ub = exchange.fetch_balance()['free'].get('USDT', 0)
            time.sleep(2)
    except Exception as e:
        tg(f"⚠️ *QUEUE BUY FAILED:* `{e}`")

# ================= SCANNER =================

def scan():
    db = load(DB_FILE)
    q = load(QUEUE_FILE)
    s = settings()
    if not s["auto_buy_enabled"]:
        return
    _, _, br, _, _ = data("BTC-USDT")
    if br and br < 35:
        return
    if len(db) >= s["max_tokens_before_consolidation"]:
        consolidate()
        db = load(DB_FILE)
    try:
        tk = exchange.fetch_tickers()
        mk = exchange.load_markets()
    except:
        return
    v = []
    for sym, t in tk.items():
        if sym.endswith('/USDT'):
            b = sym.split('/')[0]
            if (b in STABLECOINS or b in BL
                    or ((b.startswith('X') or b.startswith('R')) and len(b) <= 6)
                    or any(b.upper().endswith(x) for x in LEV)
                    or (sym in mk and not mk[sym].get('spot', False))):
                continue
            vol = t['quoteVolume'] or 0
            if vol >= 1000000:
                v.append({'sym': sym.replace("/", "-"), 'vol': vol, 'chg': t['percentage'] or 0})
    v.sort(key=lambda x: x['vol'], reverse=True)
    ct = int(time.time())
    qu = False
    
    for sym, d in list(q.items()):
        if ct - d.get('timestamp', 0) > 86400:
            q.pop(sym, None)
            qu = True
    
    for t in v[15:50]:
        p, pr, r, ma5, ma10 = data(t['sym'])
        if not p or r is None:
            continue
        setup = None
        sl = 0
        if t['chg'] > 1.0 and s['rsi_min'] <= r <= s['rsi_max'] and p > ma5 > ma10:
            setup = "TREND"
            sl = round(ma5 * (1 - s['stop_loss_pct']/100), 6)
        elif t['chg'] < -8.0 and r < 30 and p > pr:
            setup = "OVERSOLD"
            sl = round(p * (1 - s['stop_loss_pct']/100), 6)  # uses config, not hardcoded 5%
        if setup:
            rr = (p * 0.10) / (p - sl) if (p - sl) > 0 else 0
            sc = (t['vol'] / 1e6) + (rr * 10)
            if t['sym'] not in q or q[t['sym']].get('score', 0) < sc:
                if t['sym'] not in q:
                    tg(f"📥 *QUEUED:* {t['sym']}\nScore: {sc:.1f}")
                q[t['sym']] = {"timestamp": ct, "type": setup, "sl": sl, "score": sc}
                qu = True
    if qu:
        save(QUEUE_FILE, q)

# ================= DISPLAY =================

def fmt_price(p):
    if p is None:
        return "N/A"
    if p >= 100:
        return f"{p:.2f}"
    elif p >= 1:
        return f"{p:.4f}"
    elif p >= 0.001:
        return f"{p:.6f}"
    else:
        return f"{p:.8f}"

def format_table(headers, rows, widths):
    top = "+" + "+".join(["-" * (w + 2) for w in widths]) + "+"
    header_str = "| " + " | ".join([h.ljust(w) for h, w in zip(headers, widths)]) + " |"
    row_strs = ["| " + " | ".join([str(val).ljust(w) for val, w in zip(r, widths)]) + " |" for r in rows]
    return "\n".join([top, header_str, top] + row_strs + [top])

def gl():
    try:
        tk = exchange.fetch_tickers()
    except:
        return "❌ Error", "❌ Error"
    v = []
    for sym, t in tk.items():
        if sym.endswith('/USDT'):
            b = sym.split('/')[0]
            if (b in STABLECOINS or b in BL
                    or ((b.startswith('X') or b.startswith('R')) and len(b) <= 6)
                    or any(b.upper().endswith(x) for x in LEV)):
                continue
            vol = t['quoteVolume'] or 0
            if vol >= 1000000:
                v.append({'sym': b, 'chg': t['percentage'] or 0, 'price': t['last'] or 0})
    v.sort(key=lambda x: x['chg'], reverse=True)
    
    def build(title, data):
        rows = [[x['sym'], fmt_price(x['price']), f"{x['chg']:+.1f}%"] for x in data]
        table = format_table(["Coin", "Price", "Change"], rows, [8, 12, 8])
        return f"*{title}*\n```\n{table}\n```"
    
    return build("🚀 GAINERS (24H)", v[:10]), build("🩸 LOSERS (24H)", v[-10:][::-1])

# ================= TELEGRAM COMMANDS =================

def tg_check():
    st = load(STATE_FILE)
    lid = st.get("last_update_id", 0)
    try:
        res = requests.get(
            f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates?offset={lid+1}",
            timeout=10
        ).json()
        if not (res.get("ok") and res.get("result")):
            return
        
        for upd in res["result"]:
            lid = upd["update_id"]
            if "message" not in upd or "text" not in upd["message"]:
                continue
            if "date" in upd["message"] and int(time.time()) - upd["message"]["date"] > 900:
                continue
            
            txt = upd["message"]["text"].strip()
            low = txt.lower()
            
            if low.startswith("/ai"):
                ask_ai(txt[3:].strip())
            
            elif low == "/help":
                tg("🤖 *COMMANDS*\n"
                   "`/ai [question]` - Ask AI\n"
                   "`/analyze SOL`\n"
                   "`/gainers` / `/losers`\n"
                   "`/queue` / `/balance`\n"
                   "`/audit` / `/status` / `/pnl`\n"
                   "`/buy SOL 5.75`\n"
                   "`/dca SOL 5.75`\n"
                   "`/set sl_sell_percent 0.5`\n"
                   "`/set dca_reserve 2.0`")
            
            elif low == "/balance":
                b = exchange.fetch_balance()['free']
                u = b.get('USDT', 0)
                rows = [["USDT", f"{u:.2f}", f"${u:.2f}"]]
                for c, a in b.items():
                    if a > 0 and c not in ['USDT', 'USDC']:
                        p = live_price(f"{c}-USDT")
                        if p and a * p >= 0.10:
                            rows.append([c, f"{a:.4f}", f"${a*p:.2f}"])
                table = format_table(["Asset", "Amount", "Est. Val"], rows, [7, 10, 9])
                tg(f"💰 *BALANCE*\n```\n{table}\n```")
            
            elif low == "/queue":
                q = load(QUEUE_FILE)
                if not q:
                    tg("📥 Queue empty.")
                else:
                    sq = sorted(q.items(), key=lambda x: x[1].get('score', 0), reverse=True)
                    rows = [[s, f"{d.get('score', 0):.1f}", d.get('type', 'N/A')] for s, d in sq]
                    table = format_table(["Coin", "Score", "Setup"], rows, [7, 6, 8])
                    tg(f"📥 *PRIORITY QUEUE*\n```\n{table}\n```")
            
            elif "/status" in low:
                db = load(DB_FILE)
                if not db:
                    tg("🛑 No active trades.")
                else:
                    rows = []
                    for s, d in db.items():
                        p = live_price(s)
                        if p:
                            pnl = ((p - d['entry_price']) / d['entry_price']) * 100
                            rows.append([s.split('-')[0], fmt_price(d['entry_price']), fmt_price(p), f"{pnl:+.1f}%"])
                    table = format_table(["Coin", "Entry", "Current", "PnL"], rows, [7, 8, 8, 7])
                    tg(f"🟢 *ACTIVE TRADES*\n```\n{table}\n```")
            
            elif low == "/audit":
                b = exchange.fetch_balance()['free']
                u = b.get('USDT', 0)
                q = load(QUEUE_FILE)
                s = settings()
                rows = [
                    ["API Status", "OK 🟢"],
                    ["USDT Balance", f"{u:.2f}"],
                    ["Active Trades", f"{len(load(DB_FILE))}"],
                    ["Queue Size", f"{len(q)}"],
                    ["RSI Range", f"{s['rsi_min']}-{s['rsi_max']}"],
                    ["Stop Loss", f"{s['stop_loss_pct']}%"],
                    ["SL Sell %", f"{s.get('sl_sell_percent', 0.5)*100}%"],
                    ["DCA Reserve", f"${s.get('dca_reserve', 2.0)}"],
                    ["Auto-Buy", f"${s['auto_buy_amount_usdt']}"],
                ]
                table = format_table(["Metric", "Value"], rows, [12, 10])
                tg(f"🛠️ *SYSTEM AUDIT*\n```\n{table}\n```")
            
            elif low == "/pnl":
                L = load(LEDGER_FILE)
                p = L.get('total_realized_usdt', 0.0)
                c = L.get('trade_count', 0)
                rows = [["Total PnL", f"${p:.2f}"], ["Trades Closed", f"{c}"]]
                table = format_table(["Metric", "Value"], rows, [12, 10])
                tg(f"📊 *PROFIT & LOSS*\n```\n{table}\n```")
            
            elif low.startswith("/analyze"):
                parts = txt.split()
                if len(parts) > 1:
                    c = parts[1].upper()
                    pr, _, r, ma5, ma10 = data(f"{c}-USDT")
                    tg(f"🧠 *{c}*\nPrice: {pr}\nRSI: {r}\nMA5: {ma5}\nMA10: {ma10}")
            
            elif low == "/gainers":
                g, _ = gl()
                tg(g)
            elif low == "/losers":
                _, l = gl()
                tg(l)
            
            elif low.startswith("/buy"):
                parts = txt.split()
                if len(parts) == 3:
                    c = parts[1].upper()
                    try:
                        amt = float(parts[2])
                    except:
                        tg("❌ Invalid amount.")
                        continue
                    sym = f"{c}-USDT"
                    
                    if cooldown(f"buy:{sym}", 30):
                        tg(f"⏳ Already processing /buy {c}, ignoring duplicate.")
                        continue
                    
                    db = load(DB_FILE)
                    if sym in db:
                        tg(f"❌ Already have active position for {c}.")
                    else:
                        ub = exchange.fetch_balance()['free'].get('USDT', 0)
                        if ub < amt:
                            tg(f"❌ *BUY FAILED:* You only have ${ub:.2f} USDT.")
                        else:
                            try:
                                old_total = exchange.fetch_balance()['total'].get(c, 0)
                                filled, avg = market_buy(f"{c}/USDT", amt)
                                new_total = old_total + filled
                                sl = round(avg * (1 - settings()['stop_loss_pct']/100), 6)
                                db[sym] = {
                                    "entry_price": round(avg, 6),
                                    "safe_zone": round(avg, 6),
                                    "stop_loss": sl,
                                    "took_profit_10": False,
                                    "took_profit_50": False,
                                    "dca_count": 0,
                                    "sl_hit": False,
                                }
                                save(DB_FILE, db)
                                hard_sl(sym, new_total * settings().get('sl_sell_percent', 0.5), sl)
                                tg(f"✅ *BOUGHT:* {c} @ {avg:.6f} | SL: {sl}")
                            except Exception as e:
                                tg(f"❌ *BUY FAILED:* {e}")
            
            elif low.startswith("/dca"):
                parts = txt.split()
                if len(parts) == 3:
                    c = parts[1].upper()
                    try:
                        amt = float(parts[2])
                    except:
                        tg("❌ Invalid amount.")
                        continue
                    sym = f"{c}-USDT"
                    
                    if cooldown(f"dca:{sym}", 30):
                        tg(f"⏳ Already processing /dca {c}, ignoring duplicate.")
                        continue
                    
                    db = load(DB_FILE)
                    if sym not in db:
                        tg(f"❌ No active bot trade for {c}.")
                    else:
                        ub = exchange.fetch_balance()['free'].get('USDT', 0)
                        if ub < amt:
                            tg(f"❌ *DCA FAILED:* You only have ${ub:.2f} USDT.")
                        else:
                            try:
                                old_total = exchange.fetch_balance()['total'].get(c, 0)
                                filled, avg = market_buy(f"{c}/USDT", amt)
                                new_total = old_total + filled
                                old_entry = db[sym]['entry_price']
                                new_entry = (
                                    ((old_total * old_entry) + (filled * avg)) / new_total
                                    if new_total > 0 else avg
                                )
                                sl = round(new_entry * (1 - settings()['stop_loss_pct']/100), 6)
                                db[sym].update({
                                    "entry_price": round(new_entry, 6),
                                    "stop_loss": sl,
                                    "took_profit_10": False,
                                    "took_profit_50": False,
                                    "dca_count": db[sym].get('dca_count', 0) + 1,
                                    "sl_hit": False,
                                })
                                save(DB_FILE, db)
                                hard_sl(sym, new_total * settings().get('sl_sell_percent', 0.5), sl)
                                tg(f"🛒 *DCA:* {c} | {amt} USDT @ {avg:.6f}\n📉 New Entry: {new_entry:.6f}\n🛑 SL: {sl}")
                            except Exception as e:
                                tg(f"❌ *DCA FAILED:* {e}")
        
        st["last_update_id"] = lid
        save(STATE_FILE, st)
    except Exception as e:
        print(f"TG Err: {e}")

# ================= MAIN =================

if __name__ == "__main__":
    try:
        clear_cache()
        tg_check()
        manage()
        scan()
        exec_queue()
    except Exception as e:
        try:
            tg(f"🚨 *CRASH:* `{e}`\n`{traceback.format_exc()[-200:]}`")
        except:
            pass
    
    # Git sync — isolated so one failure can't kill the rest
    for cmd in [["git", "pull", "--rebase"],
                ["git", "add", "."],
                ["git", "commit", "-m", "Auto"],
                ["git", "push"]]:
        try:
            subprocess.run(cmd, capture_output=True, timeout=30)
        except Exception as e:
            print(f"git {cmd[1]} failed: {e}")
