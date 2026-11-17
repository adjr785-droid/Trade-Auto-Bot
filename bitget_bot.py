import requests,json,os,time,ccxt,subprocess,traceback,re,datetime
from google import genai

TG_TOKEN=os.environ.get('TG_TOKEN');TG_CHAT_ID=os.environ.get('TG_CHAT_ID')
DB="positions.json";ST="tg_state.json";CFG="settings.json";QF="queue.json";LF="profit_ledger.json";CD=".cmd_cooldown.json";SC="scan_state.json";HIST="trade_history.json"
STABLE=['USDC','USDT','DAI','FDUSD','TUSD','USDD','USDE'];LEV=['3L','3S','5L','5S','10L','10S','2L','2S'];BL=['RPANW','CT','ONE']

ex=ccxt.bitget({'apiKey':os.environ.get('BITGET_API_KEY'),'secret':os.environ.get('BITGET_API_SECRET'),'password':os.environ.get('BITGET_API_PASSPHRASE'),'enableRateLimit':True})
ex.options['createMarketBuyOrderRequiresPrice'] = False

client=genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))
_C={};_T=20

def get_reply_kb():
    kb=[["📊 Menu","▶️ Resume","🚨 PANIC"]]
    return {"keyboard": kb, "resize_keyboard": True, "is_persistent": True}

def _send(m,mode,buttons=None):
    try:
        payload={"chat_id":TG_CHAT_ID,"text":m,"parse_mode":mode}
        if buttons:payload["reply_markup"]=buttons
        else:payload["reply_markup"]=get_reply_kb()
        r=requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",json=payload,timeout=10)
        if not r.ok:
            c=re.sub(r'<[^>]+>','',m) if mode=="HTML" else m;payload["text"]=c
            r2=requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",json=payload,timeout=10)
            if not r2.ok: print(f"TG SEND ERROR: {r2.text}")
    except Exception as e: print(f"TG EXCEPTION: {e}")

def tg(m):_send(m,"Markdown")
def tg_ai(m):_send(m,"HTML")

def tg_inline_menu():
    s=cfg();tb={"text":"📈 Trail: ON","callback_data":"trail_off"} if s.get('trail_pct',5.0)>0 else {"text":"📉 Trail: OFF","callback_data":"trail_on"}
    btns={"inline_keyboard":[[{"text":"▶️ Resume","callback_data":"/resume"},{"text":"⏸ Pause","callback_data":"/pause"}],[{"text":"🔄 Sync Wallet","callback_data":"/sync"},{"text":"🛒 Buy Info","callback_data":"buy_info"}],[{"text":"🟢 Status","callback_data":"/status"},{"text":"💰 Balance","callback_data":"/balance"}],[{"text":"📊 Summary","callback_data":"/summary"},tb],[{"text":"🚀 Gainers","callback_data":"/gainers"},{"text":"🩸 Losers","callback_data":"/losers"}],[{"text":"🧠 Ask AI","callback_data":"ai_info"}]]}
    _send("🕹️ *MAIN MENU*\nSelect an option below:","Markdown",btns)

def load(f,d=None):
    d=[] if f==HIST else ({} if d is None else d)
    try:
        if os.path.exists(f):
            with open(f) as fh:return json.load(fh)
    except Exception as e:print(f"LOAD ERR {f}: {e}")
    return d

def save(f,d):
    try:
        with open(f,"w") as fh:json.dump(d,fh,indent=4)
    except Exception as e:print(f"SAVE ERR {f}: {e}")

def cfg():
    d={"rsi_min":42,"rsi_max":48,"stop_loss_pct":8,"auto_buy_enabled":True,"auto_buy_amount_usdt":5.75,"max_tokens_before_consolidation":3,"max_hold_hours":48,"trail_pct":5.0,"crash_guard_pct":18.0}
    s=load(CFG,d)
    for k,v in d.items():s.setdefault(k,v)
    return s

def in_cd(k,s=5):return (time.time()-load(CD,{}).get(k,0))<s
def mk_cd(k):d=load(CD,{});d[k]=time.time();save(CD,d)
def cd(k,s=5):
    if in_cd(k,s):return True
    mk_cd(k);return False

def _calc(sym):
    try:
        o=ex.fetch_ohlcv(sym.replace("-","/"),'1h',limit=50);c=[float(k[4]) for k in o[:-1]]
        g=[max(0,c[i]-c[i-1]) for i in range(1,len(c))];l=[max(0,c[i-1]-c[i]) for i in range(1,len(c))]
        ag=sum(g[-14:])/14;al=sum(l[-14:])/14;r=100.0 if al==0 else round(100-(100/(1+(ag/al))),2)
        return c[-1],c[-2],r,round(sum(c[-5:])/5,6),round(sum(c[-10:])/10,6)
    except:return None,None,None,None,None

def data(sym):
    h=_C.get(sym)
    if h and (time.time()-h[0])<_T:return h[1]
    r=_calc(sym);_C[sym]=(time.time(),r);return r

def live(sym):
    try:return ex.fetch_ticker(sym.replace("-","/"))['last']
    except:return None

def clr():_C.clear()

def cancel_sells(sym):
    try:
        for o in ex.fetch_open_orders(sym.replace("-","/")):
            if o['side']=='sell':ex.cancel_order(o['id'],sym.replace("-","/"))
    except:pass

def m_buy(cs,usdt):
    p=live(cs.replace("/","-"))
    if not p:raise Exception(f"No price for {cs}")
    try:
        o=ex.create_market_buy_order(cs,usdt)
    except Exception:
        ex.options['createMarketBuyOrderRequiresPrice'] = True
        q=float(ex.amount_to_precision(cs,usdt/p))
        o=ex.create_market_buy_order(cs,q,p)
    f,a=o.get('filled'),o.get('average')
    if not f or not a:
        time.sleep(1)
        try:o2=ex.fetch_order(o['id'],cs);f=o2.get('filled',usdt/p);a=o2.get('average',p)
        except:f,a=usdt/p,p
    return float(f),float(a)

def hard_sl(sym,qty,pr):
    try:
        cancel_sells(sym)
        if qty<=0:return True
        ex.create_order(sym.replace("-","/"),'market','sell',qty,None,{'triggerPrice':pr,'triggerType':'market_price'})
        return True
    except Exception as e:tg(f"⚠️ *SL FAILED* {sym}@{pr}:`{e}`");return False

def place_trailing_sl(sym,qty,pct):
    try:
        cancel_sells(sym);time.sleep(0.5)
        ex.create_order(sym.replace("-","/"),'market','sell',qty,None,{'trailingPercent':pct})
        tg(f"📈 *NATIVE TRAILING SL PLACED:* {sym} @ {pct}%")
        return True
    except Exception as e:
        tg(f"⚠️ *NATIVE TRAIL FAILED* {sym}: `{e}`. Falling back to local.")
        return hard_sl(sym,qty,live(sym)*0.95)

def sell(sym,pct,reason):
    try:
        cs=sym.replace("-","/");coin=sym.split("-")[0];cancel_sells(sym);time.sleep(0.5)
        fb=ex.fetch_balance()['free'].get(coin,0)
        if fb<=0:return True
        p=live(sym)
        if not p:return False
        q=fb*pct
        if q*p<1.02 and pct<1.0:q=fb;reason+=" (dust→full)"
        q=float(ex.amount_to_precision(cs,q))
        if q<=0:return True
        ex.create_market_sell_order(cs,q);db=load(DB)
        if sym in db:
            pnl=(p-db[sym]["entry_price"])*q;L=load(LF)
            L['total_realized_usdt']=L.get('total_realized_usdt',0.0)+pnl;L['trade_count']=L.get('trade_count',0)+1;save(LF,L)
            H=load(HIST);H.append({"sym":sym,"pnl":pnl,"time":time.time(),"reason":reason});save(HIST,H)
            tg(f"✅ *SOLD ({reason})*\n🪙 {q} {sym}\n{'🟢' if pnl>=0 else '🔴'} PnL: ${pnl:.2f}")
        return True
    except Exception as e:tg(f"❌ *SELL FAILED* {sym}:`{e}`");return False

def cmd_sell(txt):
    parts=txt.split()
    if len(parts)!=3:tg("❌ Usage: `/sell COIN AMOUNT_USDT`");return
    c=parts[1].upper().replace("-USDT","").replace("/USDT","")
    try:amt=float(parts[2])
    except:tg("❌ Invalid amount.");return
    sym=f"{c}-USDT"
    if cd(f"sell:{sym}",5):tg(f"⏳ Processing /sell {c}...");return
    db=load(DB)
    if sym not in db:tg(f"❌ No active bot trade for {c}.\n💡 *Tip:* If you bought it manually, type `/sync` to add it to the bot first.");return
    p=live(sym)
    if not p:tg(f"❌ No price for {c}.");return
    fb=ex.fetch_balance()['free'].get(c,0)
    if fb<=0:tg(f"❌ You have 0 {c} available to sell.");return
    target_qty=amt/p
    if target_qty >= fb:qty=fb;amt_str="ALL"
    else:qty=float(ex.amount_to_precision(sym.replace("-","/"),target_qty));amt_str=f"${amt:.2f}"
    if qty<=0:tg(f"❌ Amount too small after precision rounding.");return
    try:
        cancel_sells(sym);time.sleep(0.5)
        ex.create_market_sell_order(sym.replace("-","/"),qty)
        pnl=(p-db[sym]["entry_price"])*qty;L=load(LF)
        L['total_realized_usdt']=L.get('total_realized_usdt',0.0)+pnl;L['trade_count']=L.get('trade_count',0)+1;save(LF,L)
        H=load(HIST);H.append({"sym":sym,"pnl":pnl,"time":time.time(),"reason":f"MANUAL SELL {amt_str}"});save(HIST,H)
        rem=ex.fetch_balance()['total'].get(c,0)
        if rem<=0 or (rem*p)<1.02:db.pop(sym,None);save(DB,db);tg(f"✅ *MANUALLY SOLD:* {qty} {c} @ {p:.6f}\nPnL: ${pnl:.2f}\nPosition closed.")
        else:
            tot=ex.fetch_balance()['total'].get(c,0);hard_sl(sym,tot,db[sym]['stop_loss']);save(DB,db)
            tg(f"✅ *MANUALLY SOLD:* {qty} {c} @ {p:.6f}\nPnL: ${pnl:.2f}\nRemaining: {rem:.4f} {c} (SL Replaced)")
    except Exception as e:tg(f"❌ *SELL FAILED:* {e}")

def cmd_sync():
    tg("🔄 Syncing manual coins to bot database...")
    try: b=ex.fetch_balance()['total']
    except: tg("❌ Failed to fetch balance."); return
    db=load(DB); added=[]
    for c,a in b.items():
        if c in STABLE or a<=0: continue
        sym=f"{c}-USDT"; p=live(sym)
        if p and (a*p)>1.02:
            if sym not in db:
                sl=round(p*(1-cfg()['stop_loss_pct']/100),6)
                db[sym]={"entry_price":p,"safe_zone":p,"stop_loss":sl,"peak_price":p,"last_price":p,"opened_at":time.time()}
                added.append(sym); hard_sl(sym,a,sl)
    if added:
        save(DB,db); tg(f"✅ *SYNCED:* Added {len(added)} coins to DB:\n`{chr(10).join(added)}`\n_Entry price set to current market price. You can now sell them via the bot._")
    else: tg("✅ No new manual coins found to sync.")

def panic_sell():
    db=load(DB); q=load(QF)
    if not db:tg(f"🚨 *PANIC:* No open positions to sell. Your balance is ${ex.fetch_balance()['free'].get('USDT',0):.2f} USDT.");return
    tg("🚨 *PANIC COMMAND INITIATED!* Selling all positions...")
    for sym in list(db.keys()):sell(sym, 1.0, "PANIC")
    save(DB, {}); save(QF, {})
    s=cfg(); s['auto_buy_enabled']=False; save(CFG, s)
    tg("🛑 *PANIC COMPLETE.* All positions sold. Auto-buy PAUSED.")

def get_summary_text():
    H=load(HIST);now=time.time()
    day=[t for t in H if now-t['time']<=86400];week=[t for t in H if now-t['time']<=604800]
    def calc(lst):
        if not lst:return 0.0,0,0.0
        return sum(t['pnl'] for t in lst),len(lst),(len([t for t in lst if t['pnl']>0])/len(lst)*100)
    dp,dc,dw=calc(day);wp,wc,ww=calc(week)
    return f"📊 *PERFORMANCE SUMMARY*\n\n*24 HOURS*\nPnL: ${dp:.2f}\nTrades: {dc}\nWin Rate: {dw:.1f}%\n\n*7 DAYS*\nPnL: ${wp:.2f}\nTrades: {wc}\nWin Rate: {ww:.1f}%"

def reconcile():
    try:bal=ex.fetch_balance()['total']
    except:return load(DB)
    db=load(DB)
    changed=False
    for sym in list(db.keys()):
        if bal.get(sym.split('-')[0],0)<=0:
            tg(f"🧹 Reconciled: {sym} not on exchange, removing")
            db.pop(sym,None);changed=True
            
    for c,a in bal.items():
        if c in STABLE or a<=0: continue
        sym=f"{c}-USDT"
        if sym not in db:
            p=live(sym)
            if p and (a*p)>1.02:
                sl=round(p*(1-cfg()['stop_loss_pct']/100),6)
                db[sym]={"entry_price":p,"safe_zone":p,"stop_loss":sl,"peak_price":p,"last_price":p,"opened_at":time.time()}
                tg(f"🔄 *AUTO-SYNC:* Detected manual buy! Added {sym}.\nEntry (current price): {p:.6f}\nSL set to: {sl}")
                hard_sl(sym,a,sl)
                changed=True
                
    if changed:save(DB,db)
    return db

def ask_ai(p):
    try:
        db=load(DB);q=load(QF);s=cfg();bal=ex.fetch_balance()['free'].get('USDT',0);_,_,br,_,_=data("BTC-USDT")
        pos=[f"  {sym}:entry={d['entry_price']},pnl={((live(sym) or d['entry_price'])-d['entry_price'])/d['entry_price']*100:+.1f}%" for sym,d in list(db.items())[:5]] or ["  (none)"]
        ctx=f"USDT:{bal:.2f}|AutoBuy:${s['auto_buy_amount_usdt']}|BTC RSI:{br}|Pos({len(db)}):\n"+"\n".join(pos)+f"\nQueue:{len(q)}|RSI {s['rsi_min']}-{s['rsi_max']},SL {s['stop_loss_pct']}%\n\n"
        r=client.models.generate_content(model='gemini-2.5-flash',contents=f"Expert crypto analyst. Use Telegram HTML (<b>,<i>), newlines NOT <br>.\n{ctx}Q:{p}")
        tg_ai(f"🧠 <b>AI:</b>\n\n{r.text or '(empty)'}")
    except Exception as e:tg(f"❌ AI Error:{e}")

def manage():
    db=load(DB);s=cfg()
    for sym,d in list(db.items()):
        if 'opened_at' not in d:d['opened_at']=time.time();db[sym]=d;save(DB,db)
        _,_,r,_,_=data(sym);lv=live(sym)
        if not lv:continue
        inc=((lv-d["entry_price"])/d["entry_price"])*100;coin=sym.split('-')[0]
        target_base_sl=round(d['entry_price']*(1-s['stop_loss_pct']/100),6)
        if d['stop_loss']<target_base_sl:
            if lv<=target_base_sl:
                if sell(sym,1.0,f"SL HIT ({s['stop_loss_pct']}%)"):db.pop(sym,None);save(DB,db)
                continue
            else:
                d['stop_loss']=target_base_sl;tot=ex.fetch_balance()['total'].get(coin,0)
                if tot>0:hard_sl(sym,tot,target_base_sl);tg(f"🛡️ *SL TIGHTENED:* {sym} moved to {target_base_sl} ({s['stop_loss_pct']}%)")
        last_p=d.get('last_price',0)
        if last_p>0:
            drop=(lv-last_p)/last_p*100
            if drop<=-s.get('crash_guard_pct',18):
                if sell(sym,1.0,f"CRASH GUARD ({drop:.1f}%)"):db.pop(sym,None);save(DB,db);continue
        d['last_price']=lv
        if inc>=5.0 and d['stop_loss']<d['entry_price']:
            d['stop_loss']=d['entry_price'];tot=ex.fetch_balance()['total'].get(coin,0)
            if tot>0:hard_sl(sym,tot,d['stop_loss']);tg(f"🛡️ *BREAKEVEN LOCKED:* {sym} moved to {d['stop_loss']}")
        if s.get('trail_pct',0)>0:
            peak=d.get('peak_price',d['entry_price'])
            if lv>peak:
                peak=lv;d['peak_price']=peak;trail_sl=round(peak*(1-s.get('trail_pct',5)/100),6)
                if trail_sl>d['stop_loss']:
                    d['stop_loss']=trail_sl;tot=ex.fetch_balance()['total'].get(coin,0)
                    if tot>0:place_trailing_sl(sym,tot,s.get('trail_pct',5))
        hold_h=(time.time()-d.get('opened_at',time.time()))/3600
        if hold_h>s.get('max_hold_hours',48):
            if sell(sym,1.0,f"TIME STOP {hold_h:.0f}h"):db.pop(sym,None);save(DB,db)
            continue
        if lv<=d["stop_loss"]:
            if sell(sym,1.0,"STOP LOSS"):db.pop(sym,None);save(DB,db)
            continue
        if r and r>=75:
            if sell(sym,1.0,"RSI 75"):db.pop(sym,None);save(DB,db)

def consolidate():
    db=load(DB)
    if len(db)<=2:return
    tg("⚖️ *CONSOLIDATING...*")
    rk=[{"s":sym,"pnl":((p-d['entry_price'])/d['entry_price'])*100} for sym,d in db.items() if (p:=live(sym)) and "entry_price" in d]
    if not rk:return
    rk.sort(key=lambda x:x["pnl"],reverse=True)
    for t in rk[2:]:
        if sell(t['s'],1.0,"Consolidation"):db.pop(t['s'],None);save(DB,db)

def exec_q():
    q=load(QF);db=load(DB);s=cfg()
    if not q:return
    sq=sorted(q.items(),key=lambda x:x[1].get('score',0),reverse=True)
    try:
        ub=ex.fetch_balance()['free'].get('USDT',0)
        for sym,qd in sq:
            if in_cd(f"fail_{sym}",300):continue
            if sym in db:q.pop(sym,None);save(QF,q);continue
            p=live(sym)
            if not p:continue
            spend=ub
            if spend<1.02 and db:
                wk=None;low=999
                for hs,hd in db.items():
                    hp=live(hs)
                    if hp:
                        pnl=((hp-hd['entry_price'])/hd['entry_price'])*100
                        if pnl<low:low=pnl;wk=hs
                if wk:
                    bc=wk.split('-')[0]; new_sc=qd.get('score',0)
                    if new_sc>25 or low<0:
                        tg(f"🔄 *ROTATION SELL:* Dropping {wk} (PnL {low:.1f}%) to buy {sym} (Score {new_sc:.1f})")
                        if sell(wk,1.0,"ROTATION"):
                            db.pop(wk,None);save(DB,db);time.sleep(2);ub=ex.fetch_balance()['free'].get('USDT',0);spend=ub
                    else:
                        cancel_sells(wk);time.sleep(0.5)
                        hb=ex.fetch_balance()['free'].get(bc,0);hp=live(wk)
                        if hb>0 and hp:
                            v=min(s['auto_buy_amount_usdt'],hb*hp);qs=float(ex.amount_to_precision(wk.replace("-","/"),v/hp))
                            if qs*hp<(hb*hp)*0.95:
                                ex.create_market_sell_order(wk.replace("-","/"),qs);tg(f"🔄 *PARTIAL SWAP:* Sold {qs} {bc} for {sym}.")
                                time.sleep(2);ub=ex.fetch_balance()['free'].get('USDT',0);spend=ub
                                rem=ex.fetch_balance()['total'].get(bc,0)
                                if rem>0 and (rem*hp)>1.02:hard_sl(wk,rem,db[wk]['stop_loss'])
                                else:db.pop(wk,None);save(DB,db)
            if spend<1.00:q.pop(sym,None);save(QF,q);continue
            sp=min(spend,s['auto_buy_amount_usdt']);cs=sym.replace("-","/")
            if len(db)>=s["max_tokens_before_consolidation"]:continue
            try:f,a=m_buy(cs,sp)
            except Exception as e:tg(f"❌ Buy fail {sym}:{e}");mk_cd(f"fail_{sym}");continue
            maxsl=round(a*(1-s['stop_loss_pct']/100),6);sl=qd.get('sl') or maxsl
            if sl>=a or sl>maxsl:sl=maxsl
            db[sym]={"entry_price":round(a,6),"safe_zone":round(a,6),"stop_loss":sl,"peak_price":a,"last_price":a,"opened_at":time.time()}
            save(DB,db);hard_sl(sym,f,sl);tg(f"🛒 *EXECUTED!*\n{sym}|💵{sp:.2f}@{a:.6f}\n🛑SL:{sl}")
            q.pop(sym,None);save(QF,q);ub=ex.fetch_balance()['free'].get('USDT',0);time.sleep(2)
    except Exception as e:tg(f"⚠️ *QUEUE FAILED:* `{e}`")

def scan():
    st=load(SC)
    if time.time()-st.get('last',0)<3540:return
    db=load(DB);q=load(QF);s=cfg()
    if not s["auto_buy_enabled"]:return
    _,_,br,_,_=data("BTC-USDT")
    if br and br<35:return
    if len(db)>=s["max_tokens_before_consolidation"]:consolidate();db=load(DB)
    try:tk=ex.fetch_tickers();mk=ex.load_markets()
    except:return
    v=[]
    for sym,t in tk.items():
        if not sym.endswith('/USDT'):continue
        b=sym.split('/')[0]
        if (b in STABLE or b in BL or ((b.startswith('X') or b.startswith('R')) and len(b)<=6) or any(b.upper().endswith(x) for x in LEV) or (sym in mk and not mk[sym].get('spot',False))):continue
        vol=t['quoteVolume'] or 0
        if vol>=1000000:v.append({'sym':sym.replace("/","-"),'vol':vol,'chg':t['percentage'] or 0})
    v.sort(key=lambda x:x['vol'],reverse=True);ct=int(time.time());qu=False
    for sym,d in list(q.items()):
        if ct-d.get('timestamp',0)>86400:q.pop(sym,None);qu=True
    for t in v[15:100]: 
        p,pr,r,ma5,ma10=data(t['sym'])
        if not p or r is None:continue
        setup=sl=None
        if t['chg']>1.0 and s['rsi_min']<=r<=s['rsi_max'] and p>ma5>ma10:setup="TREND";sl=round(ma5*(1-s['stop_loss_pct']/100),6)
        elif t['chg']<-8.0 and r<30 and p>pr:setup="OVERSOLD";sl=round(p*(1-s['stop_loss_pct']/100),6)
        if setup:
            rr=(p*0.10)/(p-sl) if (p-sl)>0 else 0;sc=(t['vol']/1e6)+(rr*10)
            if t['sym'] not in q or q[t['sym']].get('score',0)<sc:
                if t['sym'] not in q:tg(f"📥 *QUEUED:* {t['sym']}\nScore:{sc:.1f}")
                q[t['sym']]={"timestamp":ct,"type":setup,"sl":sl,"score":sc};qu=True
    if qu:save(QF,q)
    st['last']=time.time();save(SC,st)

def check_daily_summary():
    st=load(SC)
    now=datetime.datetime.now();today=now.strftime('%Y-%m-%d')
    if now.hour >= 20 and st.get('last_summary_day') != today:
        tg(get_summary_text());st['last_summary_day']=today;save(SC,st)

def fp(p):
    if p is None:return "N/A"
    if p>=100:return f"{p:.2f}"
    if p>=1:return f"{p:.4f}"
    if p>=0.001:return f"{p:.6f}"
    return f"{p:.8f}"

def tbl(h,r,w):
    bar="+"+"+".join(["-"*(x+2) for x in w])+"+"
    hd="| "+" | ".join([a.ljust(b) for a,b in zip(h,w)])+" |"
    bd=["| "+" | ".join([str(v).ljust(x) for v,x in zip(rw,w)])+" |" for rw in r]
    return "\n".join([bar,hd,bar]+bd+[bar])

def get_market_row(sym, chg=None):
    if chg is None:
        try: chg = ex.fetch_ticker(sym.replace("-","/"))['percentage'] or 0
        except: chg = 0
    p = live(sym)
    _,_,r,_,_ = data(sym)
    return [sym.split('-')[0], fp(p), f"{chg:+.1f}%", f"{r:.1f}" if r is not None else "N/A"]

def gl():
    try:tk=ex.fetch_tickers()
    except:return "❌","❌"
    v=[]
    for sym,t in tk.items():
        if not sym.endswith('/USDT'):continue
        b=sym.split('/')[0]
        if (b in STABLE or b in BL or ((b.startswith('X') or b.startswith('R')) and len(b)<=6) or any(b.upper().endswith(x) for x in LEV)):continue
        if (t['quoteVolume'] or 0)>=1000000:v.append({'sym':sym.replace("/","-"),'chg':t['percentage'] or 0})
    v.sort(key=lambda x:x['chg'],reverse=True)
    headers=["Asset","Price","24H%","RSI"];widths=[8,10,8,6]
    def build(title, data):
        rows=[get_market_row(x['sym'], x['chg']) for x in data]
        return f"*{title}*\n```\n{tbl(headers, rows, widths)}\n```"
    return build("🚀 GAINERS (24H)",v[:5]),build("🩸 LOSERS (24H)",v[-5:][::-1])

def cmd_buy(txt):
    parts=txt.split()
    if len(parts)!=3: tg("❌ Usage: `/buy COIN USDT_AMOUNT`\nExample: `/buy BTC 5.75`"); return
    c=parts[1].upper().replace("-USDT","").replace("/USDT","")
    try:amt=float(parts[2])
    except:tg("❌ Invalid amount.");return
    sym=f"{c}-USDT"
    if cd(f"buy:{sym}",5):tg(f"⏳ Already processing /buy {c}.");return
    db=load(DB)
    if sym in db:tg(f"❌ Active position for {c}.");return
    ub=ex.fetch_balance()['free'].get('USDT',0)
    if ub<amt:tg(f"❌ *BUY FAILED:* You typed ${amt:.2f}, but only have ${ub:.2f} USDT.");return
    try:
        old=ex.fetch_balance()['total'].get(c,0);f,a=m_buy(f"{c}/USDT",amt);tot=old+f
        sl=round(a*(1-cfg()['stop_loss_pct']/100),6)
        db[sym]={"entry_price":round(a,6),"safe_zone":round(a,6),"stop_loss":sl,"peak_price":a,"last_price":a,"opened_at":time.time()}
        save(DB,db);hard_sl(sym,tot,sl)
        tg(f"✅ *BOUGHT:* {c} @ {a:.6f}\n📉 Entry:{a:.6f}\n🛑 SL:{sl}\n📈 Trailing: Active")
    except Exception as e:tg(f"❌ *BUY FAILED:* {e}\n*(Note: Bitget minimum is usually $5 USDT for most coins)*")

def cmd_set(txt):
    parts=txt.split()
    if len(parts)!=3:tg("❌ Usage: `/set <key> <value>`");return
    key,val=parts[1],parts[2];s=cfg()
    if key not in s:tg(f"❌ Unknown key. Valid: `{'`, `'.join(s.keys())}`");return
    try:
        if isinstance(s[key],bool):new=val.lower() in ('true','1','yes','on')
        elif isinstance(s[key],float):new=float(val)
        else:new=int(val)
    except:tg(f"❌ Invalid value for {key}");return
    s[key]=new;save(CFG,s);tg(f"✅ `{key}` = `{new}`")

def tg_check():
    st=load(ST);lid=st.get("last_update_id",0)
    try:
        res=requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates?offset={lid+1}",timeout=10).json()
        if not (res.get("ok") and res.get("result")):return
        for upd in res["result"]:
            lid=upd["update_id"];txt=""
            if "callback_query" in upd:
                cb=upd["callback_query"];txt=cb["data"]
                requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/answerCallbackQuery",json={"callback_query_id":cb["id"]})
            elif "message" in upd and "text" in upd["message"]:
                if "date" in upd["message"] and int(time.time())-upd["message"]["date"]>1800:
                    st["last_update_id"]=lid; save(ST,st);continue
                txt=upd["message"]["text"].strip()
            if not txt: continue
            low=txt.lower()
            
            if low.startswith("/ai "):ask_ai(txt[4:].strip())
            elif low=="/ai":ask_ai("Give me a market overview.")
            elif low=="ai_info":tg("🧠 *AI ANALYST*\nTo ask the AI a question, type:\n`/ai <your question>`\n\nExample:\n`/ai what is the market sentiment right now?`")
            elif low=="buy_info":tg("🛒 *BUY COMMAND*\nUsage: `/buy COIN AMOUNT`\nExample: `/buy SOL 5.75`\n\n_Minimum buy is usually $5 USDT._")
            elif low in ("/menu","📊 menu"):tg_inline_menu()
            elif low=="/panic" or low=="🚨 panic":panic_sell()
            elif low=="/sync":cmd_sync()
            elif low=="/help":tg("🤖 *CMDS*\n`/menu` `/ai [q]` `/sync`\n`/gainers` `/losers` `/summary`\n`/balance` `/status` `/pnl`\n`/buy COIN AMT` `/sell COIN AMT`\n`/set key value` `/pause` `/resume` `/panic`")
            elif low=="trail_on":
                s=cfg();s['trail_pct']=5.0;save(CFG,s);tg("📈 Trailing Stop-Loss turned ON (5%)");tg_inline_menu()
            elif low=="trail_off":
                s=cfg();s['trail_pct']=0.0;save(CFG,s);tg("📉 Trailing Stop-Loss turned OFF");tg_inline_menu()
            elif low=="/balance":
                b=ex.fetch_balance()['free'];u=b.get('USDT',0);rows=[["USDT",f"{u:.2f}",f"${u:.2f}"]]
                for c,a in b.items():
                    if a>0 and c not in ['USDT','USDC']:
                        p=live(f"{c}-USDT")
                        if p and a*p>=0.10:rows.append([c,f"{a:.4f}",f"${a*p:.2f}"])
                tg(f"💰 *BALANCE*\n```\n{tbl(['Asset','Amount','Est.Val'],rows,[7,10,9])}\n```")
            elif low=="/summary":tg(get_summary_text())
            elif low=="/status":
                db=load(DB)
                if not db:tg("🛑 No active trades.")
                else:
                    rows=[[s.split('-')[0],fp(d['entry_price']),fp(p),f"{((p-d['entry_price'])/d['entry_price'])*100:+.1f}%"] for s,d in db.items() if (p:=live(s))]
                    tg(f"🟢 *ACTIVE*\n```\n{tbl(['Coin','Entry','Current','PnL'],rows,[7,8,8,7])}\n```")
            elif low=="/pnl":
                L=load(LF);rows=[["Total PnL",f"${L.get('total_realized_usdt',0.0):.2f}"],["Trades",f"{L.get('trade_count',0)}"]]
                tg(f"📊 *P&L*\n```\n{tbl(['Metric','Value'],rows,[12,10])}\n```")
            elif low=="/gainers":g,_=gl();tg(g)
            elif low=="/losers":_,l=gl();tg(l)
            elif low.startswith("/buy"):cmd_buy(txt)
            elif low.startswith("/sell"):cmd_sell(txt)
            elif low.startswith("/set"):cmd_set(txt)
            elif low=="/pause":
                s=cfg();s['auto_buy_enabled']=False;save(CFG,s);tg("⏸ Auto-buy paused.")
            elif low=="/resume" or low=="▶️ resume":
                s=cfg();s['auto_buy_enabled']=True;save(CFG,s);tg("▶️ Auto-buy resumed.")
        st["last_update_id"]=lid;save(ST,st)
    except Exception as e:print(f"TG Err:{e}")

if __name__=="__main__":
    try:clr();reconcile();tg_check();manage();check_daily_summary();scan();exec_q()
    except Exception as e:
        try:tg(f"🚨 *CRASH:* `{e}`\n`{traceback.format_exc()[-200:]}`")
        except:pass
    for c in [["git","pull","--rebase"],["git","add","."],["git","commit","-m","Auto"],["git","push"]]:
        try:subprocess.run(c,capture_output=True,timeout=30)
        except Exception as e:print(f"git {c[1]}:{e}")
