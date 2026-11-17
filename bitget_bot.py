import requests, json, os, time, ccxt, subprocess, traceback, re, datetime
from google import genai

TG_TOKEN, TG_CHAT_ID = os.environ.get('TG_TOKEN'), os.environ.get('TG_CHAT_ID')
DB, ST, CFG, QF, LF, CD, SC, HIST = "positions.json", "tg_state.json", "settings.json", "queue.json", "profit_ledger.json", ".cmd_cooldown.json", "scan_state.json", "trade_history.json"
STABLE, LEV, BL = ['USDC', 'USDT', 'DAI', 'FDUSD', 'TUSD', 'USDD', 'USDE'], ['3L', '3S', '5L', '5S', '10L', '10S', '2L', '2S'], ['RPANW', 'CT', 'ONE']
ex = ccxt.bitget({'apiKey': os.environ.get('BITGET_API_KEY'), 'secret': os.environ.get('BITGET_API_SECRET'), 'password': os.environ.get('BITGET_API_PASSPHRASE'), 'enableRateLimit': True})
ex.options['createMarketBuyOrderRequiresPrice'] = False
client = genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))
_C, _TC = {}, {}

def rkb(): return {"keyboard":[[{"text":"📊 Status"},{"text":"💰 Balance"},{"text":"📋 Queue"}],[{"text":"🔍 Scan"},{"text":"🤖 AI"},{"text":"📈 Summary"}],[{"text":"🛒 BUY"},{"text":"🔴 SELL"},{"text":"🚨 PANIC"}],[{"text":"▶️ Resume"},{"text":"⏸ Pause"},{"text":"⚙️ Audit"}]],"resize_keyboard":True,"is_persistent":True}
_BTN = {"📊 status":"/status","💰 balance":"/balance","📋 queue":"/queue","🔍 scan":"/scan","🤖 ai":"/ai_prompt","📈 summary":"/summary","🛒 buy":"/buy_prompt","🔴 sell":"/sell_prompt","🚨 panic":"/panic_confirm","▶️ resume":"/resume","⏸ pause":"/pause","⚙️ audit":"/audit"}

def _send(m, mode, buttons=None, kb=False):
    try:
        p = {"chat_id": TG_CHAT_ID, "text": m, "parse_mode": mode}
        if buttons: p["reply_markup"] = buttons
        elif kb: p["reply_markup"] = rkb()
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json=p, timeout=10)
        if not r.ok:
            print(f"SEND FAIL: {r.text[:200]}")
            p["text"] = re.sub(r'<[^>]+>', '', m) if mode == "HTML" else re.sub(r'[*_`]', '', m)
            requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json=p, timeout=10)
    except Exception as e: print(f"SEND ERR: {e}")
def tg(m, kb=False): _send(m, "Markdown", kb=kb)
def tg_ai(m): _send(m, "HTML")

def sentiment(r, p, ma5, ma10, chg):
    if r is None: return "N/A"
    if r <= 28 and chg > 0: return "OVERSOLD"
    if r <= 35: return "WEAK"
    if r >= 72: return "OVERBOUGHT"
    if r >= 60 and p and ma5 and ma10 and p > ma5 > ma10: return "BULLISH"
    if r <= 42: return "BEARISH"
    if p and ma5 and ma10 and p > ma5 > ma10: return "UPTREND"
    if p and ma5 and ma10 and p < ma5 < ma10: return "DOWNTREND"
    return "NEUTRAL"
def tickers():
    if _TC and (time.time() - _TC.get('t', 0)) < 30: return _TC['d']
    try: d = ex.fetch_tickers()
    except: d = _TC.get('d', {})
    _TC.update({'t': time.time(), 'd': d}); return d
def _row(sym, chg=None, amt=None):
    t = tickers().get(sym.replace("-", "/"), {})
    if chg is None: chg = t.get('percentage') or 0
    p, pr, r, ma5, ma10 = data(sym)
    lv = t.get('last') or p
    a = f"{sym.split('-')[0]}({amt:.2f})" if amt is not None else sym.split('-')[0]
    return [a, fp(lv), f"{chg:+.1f}%", f"{r:.0f}" if r is not None else "N/A", fp(ma5), fp(ma10), sentiment(r, lv, ma5, ma10, chg)]
def fp(p):
    if p is None: return "N/A"
    if p >= 100: return f"{p:.2f}"
    if p >= 1: return f"{p:.4f}"
    if p >= 0.001: return f"{p:.6f}"
    return f"{p:.8f}"
def tbl(h, r, w):
    bar = "+" + "+".join(["-" * (x + 2) for x in w]) + "+"
    hd = "| " + " | ".join([a.ljust(b) for a, b in zip(h, w)]) + " |"
    bd = ["| " + " | ".join([str(v).ljust(x) for v, x in zip(rw, w)]) + " |" for rw in r]
    return "\n".join([bar, hd, bar] + bd + [bar])
def std_tbl(rows): return tbl(["Asset", "Price", "24H%", "RSI", "MA5", "MA10", "Sentiment"], rows, [12, 10, 7, 5, 10, 10, 9])

def load(f, d=None):
    d = [] if f == HIST else ({} if d is None else d)
    try:
        if os.path.exists(f):
            with open(f) as fh: return json.load(fh)
    except: pass
    return d
def save(f, d):
    try:
        with open(f, "w") as fh: json.dump(d, fh, indent=4)
    except Exception as e: print(f"save {f}:{e}")
def cfg():
    d = {"rsi_min": 42, "rsi_max": 48, "stop_loss_pct": 8, "auto_buy_enabled": True, "auto_buy_amount_usdt": 5.75, "max_tokens_before_consolidation": 3, "max_hold_hours": 48, "trail_pct": 5.0, "crash_guard_pct": 18.0}
    s = load(CFG, d)
    for k, v in d.items(): s.setdefault(k, v)
    return s
def in_cd(k, s=30): return (time.time() - load(CD, {}).get(k, 0)) < s
def mk_cd(k): d = load(CD, {}); d[k] = time.time(); save(CD, d)
def cd(k, s=30):
    if in_cd(k, s): return True
    mk_cd(k); return False

def _calc(sym):
    try:
        o = ex.fetch_ohlcv(sym.replace("-", "/"), '1h', limit=50)
        c = [float(k[4]) for k in o[:-1]]
        g = [max(0, c[i]-c[i-1]) for i in range(1, len(c))]
        l = [max(0, c[i-1]-c[i]) for i in range(1, len(c))]
        ag, al = sum(g[-14:])/14, sum(l[-14:])/14
        r = 100.0 if al == 0 else round(100-(100/(1+(ag/al))), 2)
        return c[-1], c[-2], r, round(sum(c[-5:])/5, 6), round(sum(c[-10:])/10, 6)
    except: return None, None, None, None, None
def data(sym):
    h = _C.get(sym)
    if h and (time.time() - h[0]) < 20: return h[1]
    r = _calc(sym); _C[sym] = (time.time(), r); return r
def live(sym):
    try: return ex.fetch_ticker(sym.replace("-", "/"))['last']
    except: return None
def cancel_sells(sym):
    try:
        for o in ex.fetch_open_orders(sym.replace("-", "/")):
            if o['side'] == 'sell':
                try: ex.cancel_order(o['id'], sym.replace("-", "/"))
                except: pass
    except: pass

def m_buy(cs, usdt):
    p = live(cs.replace("/", "-"))
    if not p: raise Exception(f"No price {cs}")
    q = float(ex.amount_to_precision(cs, usdt / p))
    if q <= 0: raise Exception("Amount too small")
    try: o = ex.create_market_buy_order(cs, q)
    except: o = ex.create_market_buy_order(cs, q, p)
    f, a = o.get('filled'), o.get('average')
    for _ in range(5):
        if f and a and f > 0: break
        time.sleep(1)
        try:
            o2 = ex.fetch_order(o['id'], cs)
            f, a = o2.get('filled', f), o2.get('average', a)
            if o2.get('status') == 'closed': break
        except: pass
    if not f or f <= 0: raise Exception("Order not filled")
    return float(f), float(a or p)
def hard_sl(sym, qty, pr):
    try:
        cancel_sells(sym); time.sleep(0.5)
        if qty <= 0: return True
        ex.create_order(sym.replace("-", "/"), 'market', 'sell', qty, None, {'triggerPrice': pr, 'triggerType': 'market_price', 'stopLossPrice': pr})
        return True
    except Exception as e:
        tg(f"⚠️ *SL FAILED* {sym}@{pr}: `{e}`"); return False
def sell(sym, pct, reason):
    try:
        cs, coin = sym.replace("-", "/"), sym.split("-")[0]
        cancel_sells(sym); time.sleep(2)
        bal, tot = {}, 0
        for _ in range(3):
            bal = ex.fetch_balance(); tot = bal.get('total', {}).get(coin, 0)
            if tot > 0: break
            time.sleep(1)
        if tot <= 0: return True
        free = bal.get('free', {}).get(coin, 0)
        qty = free if free > 0 else tot
        p = live(sym)
        if not p: return False
        q = qty * pct
        if q * p < 1.02 and pct < 1.0: q = qty; reason += " (dust→full)"
        q = float(ex.amount_to_precision(cs, q))
        if q <= 0: return True
        ex.create_market_sell_order(cs, q)
        db = load(DB)
        if sym in db:
            pnl = (p - db[sym]["entry_price"]) * q
            L = load(LF)
            L['total_realized_usdt'] = L.get('total_realized_usdt', 0.0) + pnl
            L['trade_count'] = L.get('trade_count', 0) + 1
            save(LF, L)
            H = load(HIST)
            H.append({"sym": sym, "pnl": pnl, "time": time.time(), "reason": reason})
            save(HIST, H)
            tg(f"✅ *SOLD ({reason})*\n🪙 `{q}` {sym}\n{'🟢' if pnl >= 0 else '🔴'} PnL: ${pnl:.2f}")
        return True
    except Exception as e:
        tg(f"❌ *SELL FAILED* {sym}: `{e}`"); return False
def panic_sell():
    db = load(DB)
    if not db: tg("🚨 *PANIC:* No positions.")
    else:
        tg("🚨 *PANIC COMMAND INITIATED!*")
        ok, fail = [], []
        for sym in list(db.keys()):
            if sell(sym, 1.0, "PANIC"): db.pop(sym, None); save(DB, db); ok.append(sym)
            else: fail.append(sym)
        save(QF, {})
        msg = f"🛑 *PANIC COMPLETE*\n✅ Sold: {len(ok)}\n❌ Failed: {len(fail)}"
        if fail: msg += "\n" + "\n".join(fail)
        tg(msg)
    s = cfg(); s['auto_buy_enabled'] = False; save(CFG, s)
def _norm_sym(c):
    c = c.upper().replace("-", "").replace("/", "")
    if c.endswith("USDT") and len(c) > 4: c = c[:-4]
    return c

def reconcile():
    db = load(DB)
    if not db: return db
    try: bal = ex.fetch_balance()['total']
    except: return db
    ch = False
    for sym in list(db.keys()):
        if bal.get(sym.split('-')[0], 0) <= 0:
            tg(f"🧹 Reconciled: {sym} not on exchange, removing"); db.pop(sym, None); ch = True
    if ch: save(DB, db)
    return db
def manage():
    db, s = load(DB), cfg()
    for sym, d in list(db.items()):
        if 'opened_at' not in d: d['opened_at'] = time.time(); db[sym] = d; save(DB, db)
        _, _, r, _, _ = data(sym)
        lv = live(sym)
        if not lv: continue
        inc = ((lv - d["entry_price"]) / d["entry_price"]) * 100
        coin = sym.split('-')[0]
        tsl = round(d['entry_price'] * (1 - s['stop_loss_pct'] / 100), 6)
        if d['stop_loss'] < tsl:
            if lv <= tsl:
                if sell(sym, 1.0, f"SL HIT ({s['stop_loss_pct']}%)"): db.pop(sym, None); save(DB, db)
                continue
            d['stop_loss'] = tsl
            tot = ex.fetch_balance()['total'].get(coin, 0)
            if tot > 0: hard_sl(sym, tot, tsl); tg(f"🛡️ *SL TIGHTENED:* {sym} → {tsl}")
        lp = d.get('last_price', 0)
        if lp > 0 and (lv - lp) / lp * 100 <= -s.get('crash_guard_pct', 18):
            if sell(sym, 1.0, "CRASH GUARD"): db.pop(sym, None); save(DB, db); continue
        d['last_price'] = lv
        if inc >= 5.0 and d['stop_loss'] < d['entry_price']:
            d['stop_loss'] = d['entry_price']
            tot = ex.fetch_balance()['total'].get(coin, 0)
            if tot > 0: hard_sl(sym, tot, d['stop_loss']); tg(f"🛡️ *BREAKEVEN:* {sym} → {d['stop_loss']}")
        if s.get('trail_pct', 0) > 0:
            peak = d.get('peak_price', d['entry_price'])
            if lv > peak:
                peak = lv; d['peak_price'] = peak
                tsl2 = round(peak * (1 - s.get('trail_pct', 5) / 100), 6)
                if tsl2 > d['stop_loss']:
                    d['stop_loss'] = tsl2
                    tot = ex.fetch_balance()['total'].get(coin, 0)
                    if tot > 0: hard_sl(sym, tot, tsl2); tg(f"📈 *TRAIL UPDATED:* {sym} → {tsl2}")
        hh = (time.time() - d.get('opened_at', time.time())) / 3600
        if hh > s.get('max_hold_hours', 48):
            if sell(sym, 1.0, f"TIME STOP {hh:.0f}h"): db.pop(sym, None); save(DB, db)
            continue
        if lv <= d["stop_loss"]:
            if sell(sym, 1.0, "STOP LOSS"): db.pop(sym, None); save(DB, db)
            continue
        if r and r >= 75:
            if sell(sym, 1.0, "RSI 75"): db.pop(sym, None); save(DB, db)
def consolidate():
    db = load(DB)
    if len(db) <= 2: return
    tg("⚖️ *CONSOLIDATING...*")
    rk = [{"s": k, "pnl": ((p - d['entry_price']) / d['entry_price']) * 100} for k, d in db.items() if (p := live(k)) and "entry_price" in d]
    if not rk: return
    rk.sort(key=lambda x: x["pnl"], reverse=True)
    for t in rk[2:]:
        if sell(t['s'], 1.0, "Consolidation"): db.pop(t['s'], None); save(DB, db)
def get_summary_text():
    H, now = load(HIST), time.time()
    day = [t for t in H if now - t['time'] <= 86400]
    week = [t for t in H if now - t['time'] <= 604800]
    def calc(lst):
        if not lst: return 0.0, 0, 0.0
        pnl = sum(t['pnl'] for t in lst); wins = len([t for t in lst if t['pnl'] > 0])
        return pnl, len(lst), (wins / len(lst) * 100)
    dp, dc, dw = calc(day); wp, wc, ww = calc(week)
    return f"📊 *PERFORMANCE*\n\n*24H*\nPnL: ${dp:.2f}\nTrades: {dc}\nWin: {dw:.1f}%\n\n*7D*\nPnL: ${wp:.2f}\nTrades: {wc}\nWin: {ww:.1f}%"
def ask_ai(p):
    try:
        db, q, s = load(DB), load(QF), cfg()
        bal = ex.fetch_balance()['free'].get('USDT', 0)
        _, _, br, _, _ = data("BTC-USDT")
        pos = [f"  {sym}:entry={d['entry_price']},pnl={((live(sym) or d['entry_price'])-d['entry_price'])/d['entry_price']*100:+.1f}%" for sym, d in list(db.items())[:5]] or ["  (none)"]
        ctx = f"USDT:{bal:.2f}|AutoBuy:${s['auto_buy_amount_usdt']}|BTC RSI:{br}|Pos({len(db)}):\n" + "\n".join(pos) + f"\nQueue:{len(q)}|RSI {s['rsi_min']}-{s['rsi_max']},SL {s['stop_loss_pct']}%\n\n"
        r = client.models.generate_content(model='gemini-2.5-flash', contents=f"Expert crypto analyst. Use Telegram HTML (<b>,<i>), newlines NOT <br>.\n{ctx}Q:{p}")
        tg_ai(f"🧠 <b>AI:</b>\n\n{r.text or '(empty)'}")
    except Exception as e: tg(f"❌ AI Error: {e}")

def scan():
    st = load(SC)
    if time.time() - st.get('last', 0) < 3540: return
    db, q, s = load(DB), load(QF), cfg()
    if not s["auto_buy_enabled"]: return
    _, _, br, _, _ = data("BTC-USDT")
    if br and br < 35: return
    if len(db) >= s["max_tokens_before_consolidation"]: consolidate(); db = load(DB)
    try: tk, mk = ex.fetch_tickers(), ex.load_markets()
    except: return
    v = []
    for sym, t in tk.items():
        if not sym.endswith('/USDT'): continue
        b = sym.split('/')[0]
        if (b in STABLE or b in BL or ((b.startswith('X') or b.startswith('R')) and len(b) <= 6) or any(b.upper().endswith(x) for x in LEV) or (sym in mk and not mk[sym].get('spot', False))): continue
        if (t['quoteVolume'] or 0) >= 1000000: v.append({'sym': sym.replace("/", "-"), 'vol': t['quoteVolume'], 'chg': t['percentage'] or 0})
    v.sort(key=lambda x: x['vol'], reverse=True); ct = int(time.time()); qu = False
    for sym, d in list(q.items()):
        if ct - d.get('timestamp', 0) > 86400: q.pop(sym, None); qu = True
    for t in v[15:100]:
        p, pr, r, ma5, ma10 = data(t['sym'])
        if not p or r is None: continue
        setup = sl = None
        if t['chg'] > 1.0 and s['rsi_min'] <= r <= s['rsi_max'] and p > ma5 > ma10: setup = "TREND"; sl = round(ma5 * (1 - s['stop_loss_pct'] / 100), 6)
        elif t['chg'] < -8.0 and r < 30 and p > pr: setup = "OVERSOLD"; sl = round(p * (1 - s['stop_loss_pct'] / 100), 6)
        if setup:
            rr = (p * 0.10) / (p - sl) if (p - sl) > 0 else 0; sc = (t['vol'] / 1e6) + (rr * 10)
            if t['sym'] not in q or q[t['sym']].get('score', 0) < sc:
                if t['sym'] not in q:
                    try: row = _row(t['sym'])
                    except: row = ["-"] * 7
                    tg(f"📥 *QUEUED* {setup}\n```\n{std_tbl([row])}\n```\nScore {sc:.1f}")
                q[t['sym']] = {"timestamp": ct, "type": setup, "sl": sl, "score": sc}; qu = True
    if qu: save(QF, q)
    st['last'] = time.time(); save(SC, st)
def check_daily_summary():
    st, now = load(SC), datetime.datetime.now()
    if now.hour >= 20 and st.get('last_summary_day') != now.strftime('%Y-%m-%d'): tg(get_summary_text()); st['last_summary_day'] = now.strftime('%Y-%m-%d'); save(SC, st)
def exec_q():
    q, db, s = load(QF), load(DB), cfg()
    if not q: return
    sq = sorted(q.items(), key=lambda x: x[1].get('score', 0), reverse=True)
    try:
        ub = ex.fetch_balance()['free'].get('USDT', 0)
        for sym, qd in sq:
            if in_cd(f"fail_{sym}", 300): continue
            if sym in db: q.pop(sym, None); save(QF, q); continue
            if not live(sym): continue
            spend = max(0.0, ub - 2.00)
            if spend < 1.02 and db:
                wk, low = None, 999
                for hs, hd in db.items():
                    hp = live(hs)
                    if hp:
                        pnl = ((hp - hd['entry_price']) / hd['entry_price']) * 100
                        if pnl < low: low, wk = pnl, hs
                if wk:
                    nsc = qd.get('score', 0)
                    if nsc > 25 or low < 0:
                        tg(f"🔄 *ROTATION:* {wk} → {sym}")
                        if sell(wk, 1.0, "ROTATION"):
                            db.pop(wk, None); save(DB, db); time.sleep(2)
                            ub = ex.fetch_balance()['free'].get('USDT', 0); spend = max(0.0, ub - 2.00)
                    else:
                        cancel_sells(wk); time.sleep(0.5); bc = wk.split('-')[0]
                        hb, hp = ex.fetch_balance()['free'].get(bc, 0), live(wk)
                        if hb > 0 and hp:
                            v = min(s['auto_buy_amount_usdt'], hb * hp); qs = float(ex.amount_to_precision(wk.replace("-", "/"), v / hp))
                            if qs * hp < (hb * hp) * 0.95:
                                ex.create_market_sell_order(wk.replace("-", "/"), qs); tg(f"🔄 *PARTIAL SWAP:* {qs} {bc} → {sym}")
                                time.sleep(2); ub = ex.fetch_balance()['free'].get('USDT', 0); spend = max(0.0, ub - 2.00)
                                rem = ex.fetch_balance()['total'].get(bc, 0)
                                if rem > 0 and (rem * hp) > 1.02: hard_sl(wk, rem, db[wk]['stop_loss'])
                                else: db.pop(wk, None); save(DB, db)
            if spend < 1.02: q.pop(sym, None); save(QF, q); continue
            sp = min(spend, s['auto_buy_amount_usdt'])
            if len(db) >= s["max_tokens_before_consolidation"]: continue
            try: f, a = m_buy(sym.replace("-", "/"), sp)
            except Exception as e: tg(f"❌ Buy fail {sym}: {e}"); mk_cd(f"fail_{sym}"); continue
            maxsl = round(a * (1 - s['stop_loss_pct'] / 100), 6); sl = qd.get('sl') or maxsl
            if sl >= a or sl > maxsl: sl = maxsl
            db[sym] = {"entry_price": round(a, 6), "safe_zone": round(a, 6), "stop_loss": sl, "took_profit_10": False, "took_profit_50": False, "sl_hit": False, "opened_at": time.time(), "peak_price": a, "last_price": a}
            save(DB, db); hard_sl(sym, f, sl)
            try: tg(f"🛒 *EXECUTED*\n```\n{std_tbl([_row(sym)])}\n```\nSpent ${sp:.2f} | SL {sl}")
            except: tg(f"🛒 *EXECUTED:* {sym} @ {a}")
            q.pop(sym, None); save(QF, q); ub = ex.fetch_balance()['free'].get('USDT', 0); time.sleep(2)
    except Exception as e: tg(f"⚠️ *QUEUE FAILED:* `{e}`")

def gl():
    t, v = tickers(), []
    for sym, tk in t.items():
        if not sym.endswith('/USDT'): continue
        b = sym.split('/')[0]
        if (b in STABLE or b in BL or ((b.startswith('X') or b.startswith('R')) and len(b) <= 6) or any(b.upper().endswith(x) for x in LEV)): continue
        if (tk['quoteVolume'] or 0) >= 1000000: v.append({'sym': b + '-USDT', 'chg': tk['percentage'] or 0})
    v.sort(key=lambda x: x['chg'], reverse=True)
    return [_row(x['sym'], chg=x['chg']) for x in v[:8]], [_row(x['sym'], chg=x['chg']) for x in v[-8:][::-1]]
def cmd_buy(txt):
    parts = txt.split()
    if len(parts) != 3: tg("❌ *Usage:* `/buy SYMBOL AMOUNT`"); return
    c = _norm_sym(parts[1])
    try: amt = float(parts[2])
    except: tg("❌ Invalid amount."); return
    sym = f"{c}-USDT"
    if cd(f"buy:{sym}", 30): tg("⏳ Already processing."); return
    db = load(DB)
    if sym in db: tg(f"❌ Active position for {c}."); return
    try:
        if sym.replace("-", "/") not in ex.load_markets(): tg(f"❌ Symbol `{sym}` not on Bitget."); return
    except: pass
    ub = ex.fetch_balance()['free'].get('USDT', 0)
    if ub < amt: tg(f"❌ *BUY FAILED:* Only ${ub:.2f} (need ${amt:.2f})."); return
    try:
        old = ex.fetch_balance()['total'].get(c, 0)
        f, a = m_buy(f"{c}/USDT", amt); tot = old + f
        sl = round(a * (1 - cfg()['stop_loss_pct'] / 100), 6)
        db[sym] = {"entry_price": round(a, 6), "safe_zone": round(a, 6), "stop_loss": sl, "took_profit_10": False, "took_profit_50": False, "sl_hit": False, "opened_at": time.time(), "peak_price": a, "last_price": a}
        save(DB, db)
        if tot > 0: hard_sl(sym, tot, sl)
        try: tg(f"✅ *BOUGHT*\n```\n{std_tbl([_row(sym)])}\n```\nSpent ${amt:.2f} | Filled {f} | SL {sl}")
        except: tg(f"✅ *BOUGHT:* {c} @ {a:.6f}\nSL {sl}")
    except Exception as e: tg(f"❌ *BUY FAILED:* {e}"); mk_cd(f"fail_buy:{sym}")
def cmd_sell(txt):
    parts = txt.split()
    if len(parts) < 2: tg("❌ *Usage:* `/sell SYMBOL [PCT]`"); return
    c = _norm_sym(parts[1]); sym = f"{c}-USDT"; db = load(DB)
    if sym not in db: tg(f"❌ No position for {c}."); return
    s = cfg()
    if len(parts) >= 3:
        try:
            v = float(parts[2]); s['trail_pct'] = v if v > 0 else s['trail_pct']; save(CFG, s)
        except: pass
    d, lv = db[sym], live(sym)
    if not lv: tg(f"❌ No price for {c}."); return
    if s['trail_pct'] <= 0:
        if sell(sym, 1.0, "MANUAL SELL"): db.pop(sym, None); save(DB, db)
        return
    tsl = round(lv * (1 - s['trail_pct'] / 100), 6)
    d['peak_price'], d['stop_loss'] = lv, tsl
    db[sym] = d; save(DB, db)
    tot = ex.fetch_balance()['total'].get(c, 0)
    if tot > 0: hard_sl(sym, tot, tsl)
    try: tg(f"🔴 *TRAILING SELL SET* ({s['trail_pct']}%)\n```\n{std_tbl([_row(sym)])}\n```\nTrail SL {tsl}")
    except: tg(f"🔴 *TRAIL SET:* {c} | SL {tsl}")
def cmd_set(txt):
    parts = txt.split()
    if len(parts) != 3: tg("❌ Usage: `/set <key> <value>`"); return
    key, val, s = parts[1], parts[2], cfg()
    if key not in s: tg("❌ Unknown key."); return
    try:
        if isinstance(s[key], bool): new = val.lower() in ('true', '1', 'yes', 'on')
        elif isinstance(s[key], float): new = float(val)
        else: new = int(val)
    except: tg("❌ Invalid value."); return
    s[key] = new; save(CFG, s); tg(f"✅ `{key}` = `{new}`")

def init_bot():
    try:
        r = requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/getWebhookInfo", timeout=10).json()
        if r.get('ok') and r.get('result', {}).get('url'):
            requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/deleteWebhook", timeout=10)
    except: pass
    tg(f"🤖 *BOT ONLINE*\nRun @ {datetime.datetime.now().strftime('%H:%M UTC')}")
def tg_check():
    st = load(ST); lid = st.get("last_update_id", 0)
    try:
        res = requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates?offset={lid+1}", timeout=10).json()
        if not (res.get("ok") and res.get("result")): return
        for upd in res["result"]:
            lid = upd["update_id"]; txt = ""
            if "callback_query" in upd:
                cb = upd["callback_query"]; txt = cb["data"]
                requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/answerCallbackQuery", json={"callback_query_id": cb["id"]})
            elif "message" in upd and "text" in upd["message"]:
                if "date" in upd["message"] and int(time.time()) - upd["message"]["date"] > 1800: st["last_update_id"] = lid; save(ST, st); continue
                txt = upd["message"]["text"].strip()
            if not txt: continue
            low = txt.lower()
            if low in _BTN: low = _BTN[low]
            if low.startswith("/ai ") or low == "/ai": ask_ai(txt[3:].strip() or "(no question)")
            elif low == "/ai_prompt": tg("🧠 Type: `/ai <question>`", kb=True)
            elif low in ("/menu", "/start", "/help"): tg("🤖 *COMMANDS*\n`/menu` `/status` `/balance` `/queue` `/scan`\n`/summary` `/audit` `/pnl` `/gainers` `/losers`\n`/buy SYM AMT` `/sell SYM [PCT]`\n`/ai [q]` `/analyze SYM`\n`/pause` `/resume` `/panic`\n`/set key value`", kb=True)
            elif low == "/buy_prompt": tg("🛒 Type: `/buy SYMBOL AMOUNT`\nExample: `/buy SOL 5.75`", kb=True)
            elif low == "/sell_prompt": tg("🔴 Type: `/sell SYMBOL [PCT]`\nExample: `/sell BTW 5`", kb=True)
            elif low == "/panic_confirm": _send("⚠️ <b>CONFIRM PANIC SELL?</b>\nSells ALL positions at market.", "HTML", {"inline_keyboard": [[{"text": "✅ YES", "callback_data": "/panic_yes"}, {"text": "❌ Cancel", "callback_data": "/panic_cancel"}]]})
            elif low == "/panic_yes": panic_sell()
            elif low == "/panic_cancel": tg("✅ Cancelled.")
            elif low == "/balance":
                b = ex.fetch_balance()['free']; rows = []
                for c, a in b.items():
                    if a <= 0: continue
                    if c == 'USDT': rows.append([f"USDT({a:.2f})", "1.00", "+0.0%", "N/A", "N/A", "N/A", "N/A"]); continue
                    p = live(f"{c}-USDT")
                    if not p or a * p < 0.10: continue
                    try: rows.append(_row(f"{c}-USDT", amt=a))
                    except: rows.append([f"{c}({a:.2f})", fp(p), "-", "-", "-", "-", "-"])
                tg(f"💰 *BALANCE*\n```\n{std_tbl(rows)}\n```") if rows else tg("💰 Empty balance.")
            elif low == "/queue":
                q = load(QF)
                if not q: tg("📥 Queue empty.")
                else:
                    sq = sorted(q.items(), key=lambda x: x[1].get('score', 0), reverse=True)
                    foot = [[s.split('-')[0], f"{d.get('score',0):.1f}", d.get('type', '-')] for s, d in sq]
                    tg(f"📥 *QUEUE*\n```\n{std_tbl([_row(s) for s, _ in sq])}\n```\n```\n{tbl(['Coin','Score','Setup'],foot,[8,6,8])}\n```")
            elif low == "/summary": tg(get_summary_text())
            elif low == "/scan":
                st2 = load(SC); st2['last'] = 0; save(SC, st2)
                tg("🔍 Scanning..."); scan(); q = load(QF)
                if q:
                    sq = sorted(q.items(), key=lambda x: x[1].get('score', 0), reverse=True)
                    tg(f"🔍 *SCAN COMPLETE*\n```\n{std_tbl([_row(s) for s, _ in sq])}\n```")
                else: tg("🔍 No setups found.")
            elif low == "/status":
                db = load(DB)
                if not db: tg("🛑 No active trades.")
                else:
                    rows = []
                    for s, d in db.items():
                        p = live(s)
                        if not p: continue
                        pnl = (p - d['entry_price']) / d['entry_price'] * 100
                        try: r = _row(s); r[0] = f"{s.split('-')[0]}({pnl:+.0f}%)"; rows.append(r)
                        except: rows.append([f"{s.split('-')[0]}({pnl:+.0f}%)", fp(p), "-", "-", "-", "-", "-"])
                    tg(f"🟢 *ACTIVE*\n```\n{std_tbl(rows)}\n```")
            elif low == "/audit":
                u, q, s = ex.fetch_balance()['free'].get('USDT', 0), load(QF), cfg()
                rows = [["API", "OK 🟢"], ["USDT", f"{u:.2f}"], ["Active", f"{len(load(DB))}"], ["Queue", f"{len(q)}"], ["RSI", f"{s['rsi_min']}-{s['rsi_max']}"], ["SL", f"{s['stop_loss_pct']}%"], ["Trail", f"{s.get('trail_pct',5)}%"], ["Guard", f"{s.get('crash_guard_pct',18)}%"], ["AutoBuy", f"${s['auto_buy_amount_usdt']}"], ["Paused", "Yes ⏸" if not s['auto_buy_enabled'] else "No ▶️"]]
                tg(f"🛠️ *AUDIT*\n```\n{tbl(['Metric','Value'],rows,[12,10])}\n```")
            elif low == "/pnl":
                L = load(LF)
                tg(f"📊 *P&L*\n```\n{tbl(['Metric','Value'],[['Total PnL',f\"${L.get('total_realized_usdt',0.0):.2f}\"],['Trades',f\"{L.get('trade_count',0)}\"]],[12,10])}\n```")
            elif low.startswith("/analyze"):
                parts = txt.split()
                if len(parts) < 2: tg("❌ Usage: `/analyze SYMBOL`"); continue
                c = _norm_sym(parts[1]); sym = f"{c}-USDT"
                try: tg(f"🧠 *{c}*\n```\n{std_tbl([_row(sym)])}\n```")
                except: tg(f"❌ Could not analyze {c}")
            elif low == "/gainers":
                g, _ = gl()
                if g: tg(f"🚀 *GAINERS*\n```\n{std_tbl(g)}\n```")
            elif low == "/losers":
                _, l = gl()
                if l: tg(f"🩸 *LOSERS*\n```\n{std_tbl(l)}\n```")
            elif low.startswith("/buy"): cmd_buy(txt)
            elif low.startswith("/sell") and low != "/sell_prompt": cmd_sell(txt)
            elif low.startswith("/set"): cmd_set(txt)
            elif low == "/pause": s = cfg(); s['auto_buy_enabled'] = False; save(CFG, s); tg("⏸ Paused.")
            elif low == "/resume": s = cfg(); s['auto_buy_enabled'] = True; save(CFG, s); tg("▶️ Resumed.")
            elif low == "trail_on": s = cfg(); s['trail_pct'] = 5.0; save(CFG, s); tg("📈 Trail ON")
            elif low == "trail_off": s = cfg(); s['trail_pct'] = 0.0; save(CFG, s); tg("📉 Trail OFF")
        st["last_update_id"] = lid; save(ST, st)
    except Exception as e: print(f"TG Err:{e}")

if __name__ == "__main__":
    print(f"=== RUN START {datetime.datetime.now()} ===")
    try:
        init_bot(); reconcile(); tg_check(); manage(); check_daily_summary(); scan(); exec_q()
    except Exception as e:
        err = f"🚨 *CRASH:* `{e}`\n`{traceback.format_exc()[-300:]}`"
        print(err)
        try: tg(err)
        except: pass
    for c in [["git", "pull", "--rebase"], ["git", "add", "."], ["git", "commit", "-m", "Auto"], ["git", "push"]]:
        try: subprocess.run(c, capture_output=True, timeout=30)
        except: pass
    print("=== RUN END ===")
