import requests,json,os,time,ccxt,subprocess,traceback,re,datetime,sys
from google import genai
try:import fcntl
except ImportError:fcntl=None
TK=os.environ.get('TG_TOKEN');CHAT=str(os.environ.get('TG_CHAT_ID',''));TZ=float(os.environ.get('TZ_OFFSET_HOURS','1'));API=f"https://api.telegram.org/bot{TK}"
DB,ST,CFG,QF,LF,CD,SC,HIST,LOCK="positions.json","tg_state.json","settings.json","queue.json","profit_ledger.json",".cmd_cooldown.json","scan_state.json","trade_history.json",".bot.lock"
FILES=[DB,ST,CFG,QF,LF,CD,SC,HIST]
STABLE=['USDC','USDT','DAI','FDUSD','TUSD','USDD','USDE'];LEV=['3L','3S','5L','5S','10L','10S','2L','2S'];BL=['RPANW','CT','ONE']
ex=ccxt.bitget({'apiKey':os.environ.get('BITGET_API_KEY'),'secret':os.environ.get('BITGET_API_SECRET'),'password':os.environ.get('BITGET_API_PASSPHRASE'),'enableRateLimit':True});ex.options['createMarketBuyOrderRequiresPrice']=False
client=genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))
GEMINI_MODELS=[m for m in [os.environ.get('GEMINI_MODEL'),'gemini-3.8-flash','gemini-3.6-flash','gemini-2.5-flash'] if m]
_C={};KB={"keyboard":[["📊 Menu","▶️ Resume","🚨 PANIC"]],"resize_keyboard":True,"is_persistent":True}
S=lambda s:s.replace("-","/")
TR=lambda t:{'trigger':True} if t else {}
B=lambda k,c:ex.fetch_balance()[k].get(c,0)
skip=lambda b:b in STABLE or b in BL or ((b.startswith('X') or b.startswith('R')) and len(b)<=6) or any(b.upper().endswith(x) for x in LEV)
NP=lambda e,sl:{"entry_price":e,"safe_zone":e,"stop_loss":sl,"peak_price":e,"last_price":e,"opened_at":time.time()}
STOP_OK="🛡️ Exchange stop-loss: Active\n📈 Bot-side trailing: Active"
STOP_NO="⚠️ Exchange stop NOT placed.\n📈 Bot-side stop + trailing still protect it"

def _send(m,mode,btn=None):
    try:
        rm=btn or KB;p={"chat_id":CHAT,"text":m,"reply_markup":rm}
        if mode:p["parse_mode"]=mode
        if not requests.post(API+"/sendMessage",json=p,timeout=10).ok:
            p.pop("parse_mode",None);p["text"]=re.sub(r'<[^>]+>','',m) if mode=="HTML" else m
            r=requests.post(API+"/sendMessage",json=p,timeout=10)
            if not r.ok:print("TG SEND ERROR:",r.text)
    except Exception as e:print("TG EXCEPTION:",e)
tg=lambda m:_send(m,"Markdown");tg_ai=lambda m:_send(m,"HTML")

def menu():
    s=cfg();b=lambda t,c:{"text":t,"callback_data":c};tb=b("📈 Trail: ON","trail_off") if s.get('trail_pct',8.0)>0 else b("📉 Trail: OFF","trail_on")
    _send("🕹️ *MAIN MENU*\nSelect an option below:","Markdown",{"inline_keyboard":[[b("▶️ Resume","/resume"),b("⏸ Pause","/pause")],[b("🔄 Sync","/sync"),b("🛠️ Audit","/audit")],[b("🟢 Status","/status"),b("💰 Balance","/balance")],[b("📊 Summary","/summary"),tb],[b("🚀 Gainers","/gainers"),b("🩸 Losers","/losers")],[b("🛒 Buy Info","buy_info"),b("💸 Sell Info","sell_info")],[b("🧠 Ask AI","ai_info")]]})

def panic_prompt():_send("🚨 *CONFIRM PANIC*\nThis sells ALL positions at market and pauses auto-buy.\nConfirm within 15 minutes.","Markdown",{"inline_keyboard":[[{"text":"✅ YES, SELL EVERYTHING","callback_data":"panic_confirm"},{"text":"❌ Cancel","callback_data":"panic_cancel"}]]})

def load(f,d=None):
    d=[] if f==HIST else ({} if d is None else d)
    try:
        if os.path.exists(f):
            with open(f) as h:return json.load(h)
    except Exception as e:print(f"LOAD ERR {f}: {e}")
    return d

def save(f,d):
    try:
        with open(f+".tmp","w") as h:json.dump(d,h,indent=4)
        os.replace(f+".tmp",f)
    except Exception as e:print(f"SAVE ERR {f}: {e}")

def cfg():
    d={"rsi_min":42,"rsi_max":48,"stop_loss_pct":12,"auto_buy_enabled":True,"auto_buy_amount_usdt":10.0,"max_tokens_before_consolidation":3,"max_hold_hours":48,"trail_pct":8.0,"crash_guard_pct":18.0,"rsi_exit":75,"btc_rsi_min":45}
    s=load(CFG,d)
    for k,v in d.items():s.setdefault(k,v)
    return s

def in_cd(k,s=5):return time.time()-load(CD,{}).get(k,0)<s
def mk_cd(k):d=load(CD,{});d[k]=time.time();save(CD,d)
def cd(k,s=5):
    if in_cd(k,s):return True
    mk_cd(k);return False

def _rsi(c,n=14):
    if len(c)<n+1:return None
    d=[c[i]-c[i-1] for i in range(1,len(c))];ag=sum(max(0,x) for x in d[:n])/n;al=sum(max(0,-x) for x in d[:n])/n
    for x in d[n:]:ag=(ag*(n-1)+max(0,x))/n;al=(al*(n-1)+max(0,-x))/n
    return 100.0 if al==0 else round(100-100/(1+ag/al),2)

def _calc(sym):
    try:
        o=ex.fetch_ohlcv(S(sym),'1h',limit=100)
        if len(o)<50:return (None,)*5
        c=[float(k[4]) for k in o[:-1]]
        return float(o[-1][4]),c[-1],_rsi(c),round(sum(c[-5:])/5,6),round(sum(c[-10:])/10,6)
    except Exception as e:print(f"CALC ERR {sym}: {e}");return (None,)*5

def data(sym):
    h=_C.get(sym)
    if h and time.time()-h[0]<20:return h[1]
    r=_calc(sym);_C[sym]=(time.time(),r);return r

def live(sym):
    try:return ex.fetch_ticker(S(sym))['last']
    except:return None

def open_sells(sym):
    res=[];ok=True
    for t in (False,True):
        try:res+=[(o,t) for o in ex.fetch_open_orders(S(sym),params=TR(t)) if o.get('side')=='sell']
        except Exception as e:ok=False;print(f"OPEN ORDERS ERR {sym} trigger={t}: {e}")
    return res,ok

def cancel_sells(sym):
    f=0
    for o,t in open_sells(sym)[0]:
        try:ex.cancel_order(o['id'],S(sym),TR(t))
        except Exception as e:f+=1;print(f"CANCEL ERR {sym} {o.get('id')}: {e}")
    return f

def free_qty(sym,d):
    try:b=B('free',sym.split('-')[0]);return min(d,b) if b>0 else d
    except Exception:return d

def m_buy(cs,usdt):
    p=live(cs.replace("/","-"))
    if not p:raise Exception(f"No price for {cs}")
    try:o=ex.create_order(cs,'market','buy',usdt,None,{'createMarketBuyOrderRequiresPrice':False})
    except Exception as e1:
        try:o=ex.create_order(cs,'market','buy',float(ex.amount_to_precision(cs,usdt/p)),p)
        except Exception as e2:raise Exception(f"{e1} | fallback: {e2}")
    f,a=o.get('filled'),o.get('average')
    if not f or not a:
        time.sleep(1)
        try:o2=ex.fetch_order(o['id'],cs);f=o2.get('filled') or usdt/p;a=o2.get('average') or p
        except:f,a=usdt/p,p
    return float(f),float(a)

def hard_sl(sym,qty,pr):
    try:
        cs=S(sym)
        if cancel_sells(sym):print(f"WARN: some old sell orders for {sym} could not be cancelled")
        time.sleep(0.5)
        try:qty=float(ex.amount_to_precision(cs,qty))
        except Exception:print(f"SL skipped {sym}: amount below exchange minimum (dust)");return False
        pr=float(ex.price_to_precision(cs,pr))
        if qty<=0 or qty*pr<1.0:print(f"SL skipped {sym}: position too small");return False
        ex.create_order(cs,'market','sell',qty,None,{'triggerPrice':pr});return True
    except Exception as e:tg(f"⚠️ *SL FAILED* {sym}@{pr}:`{e}`");return False

def place_trailing_sl(sym,qty):
    # Exchange side = ONE stop-loss trigger order (starts at entry-SL%; manage() raises it as the price climbs).
    # Never send 'trailingPercent' on spot: it can be executed as a plain MARKET SELL (instant liquidation).
    ent=load(DB).get(sym,{}).get('entry_price') or live(sym)
    if not ent:return False
    px=round(ent*(1-cfg()['stop_loss_pct']/100),6);ok=hard_sl(sym,qty,px)
    if ok:
        db=load(DB)
        if sym in db:db[sym]['xsl']=px;save(DB,db)
    return ok

def ratchet(sym,d,lvl,lv):
    # keep the exchange stop in step with the bot's own stop level: only ever moves UP, and only when it gains >1%
    if lvl<=d.get('xsl',0)*1.01 or lvl>=lv*0.995:return
    q=B('total',sym.split('-')[0])
    if hard_sl(sym,q,lvl):d['xsl']=lvl
    elif place_trailing_sl(sym,q):d['xsl']=round(d['entry_price']*(1-cfg()['stop_loss_pct']/100),6)  # new stop failed: restore the base stop we cancelled

def rec(sym,pnl,why):
    L=load(LF);L['total_realized_usdt']=L.get('total_realized_usdt',0.0)+pnl;L['trade_count']=L.get('trade_count',0)+1;save(LF,L)
    H=load(HIST);H.append({"sym":sym,"pnl":pnl,"time":time.time(),"reason":why});save(HIST,H)

def sell(sym,pct,reason):
    try:
        cs=S(sym);coin=sym.split("-")[0];cancel_sells(sym);time.sleep(0.5);fb=B('free',coin)
        if fb<=0:tg(f"ℹ️ {sym} has 0 balance ({reason}) - removed from tracking, no PnL logged.");return True
        p=live(sym)
        if not p:return False
        q=fb*pct
        if q*p<1.02 and pct<1.0:q=fb;reason+=" (dust→full)"
        q=float(ex.amount_to_precision(cs,q))
        if q<=0:return True
        o=ex.create_market_sell_order(cs,q);db=load(DB);px=(o or {}).get('average') or p
        if sym in db:
            pnl=(px-db[sym]["entry_price"])*q;rec(sym,pnl,reason)
            tg(f"✅ *SOLD ({reason})*\n🪙 {q} {sym}\n{'🟢' if pnl>=0 else '🔴'} PnL: ${pnl:.2f}")
        return True
    except Exception as e:tg(f"❌ *SELL FAILED* {sym}:`{e}`");return False

def parse(txt,usage,allow_all=False):
    p=txt.split()
    if len(p)!=3:tg(usage);return None
    c=p[1].upper().replace("-USDT","").replace("/USDT","");a=p[2].upper()
    if allow_all and a=="ALL":return c,None
    try:a=float(a)
    except:tg("❌ Invalid amount."+(" Use a number or `ALL`." if allow_all else ""));return None
    if a<=0:tg("❌ Amount must be positive.");return None
    return c,a

def cmd_buy(txt):
    r=parse(txt,"❌ Usage: `/buy COIN USDT_AMOUNT`\nExample: `/buy BTC 5`")
    if not r:return
    c,amt=r;sym=f"{c}-USDT"
    if cd(f"buy:{sym}",5):tg(f"⏳ Processing /buy {c}...");return
    db=load(DB);old=db.get(sym)
    ub=B('free','USDT')
    if ub<amt:tg(f"❌ *BUY FAILED:* You only have ${ub:.2f} USDT.");return
    try:
        ob=B('total',c) if old else 0.0
        f,a=m_buy(f"{c}/USDT",amt);sl=cfg()['stop_loss_pct']
        if old:
            e0=old['entry_price'];avg=round((ob*e0+f*a)/(ob+f),6) if ob>0 else round(a,6)
            old.update(entry_price=avg,safe_zone=avg,stop_loss=round(avg*(1-sl/100),6),last_price=a,peak_price=max(old.get('peak_price',avg),a));db[sym]=old;save(DB,db);ok=place_trailing_sl(sym,free_qty(sym,ob+f))
            tg(f"➕ *DCA ADDED:* {c} @ {a:.6f}\nSpent: ${amt:.2f}\nNew avg entry: {avg:.6f} (was {e0:.6f})\n"+(STOP_OK if ok else STOP_NO))
        else:
            db[sym]=NP(round(a,6),round(a*(1-sl/100),6));save(DB,db);ok=place_trailing_sl(sym,free_qty(sym,f))
            tg(f"✅ *BOUGHT:* {c} @ {a:.6f}\nSpent: ${amt:.2f}\n"+(STOP_OK if ok else STOP_NO))
    except Exception as e:tg(f"❌ *BUY FAILED:* {e}")

def cmd_sell(txt):
    r=parse(txt,"❌ Usage: `/sell COIN USDT_AMOUNT` (or `ALL`)",True)
    if not r:return
    c,amt=r;sym=f"{c}-USDT";cs=f"{c}/USDT"
    if cd(f"sell:{sym}",5):tg(f"⏳ Processing /sell {c}...");return
    db=load(DB)
    if sym not in db:tg(f"❌ No active bot trade for {c}.\n💡 *Tip:* Type `/sync` if manual.");return
    p=live(sym)
    if not p:tg(f"❌ No price for {c}.");return
    try:
        cancel_sells(sym);time.sleep(0.5);fb=B('free',c)
        if fb<=0:tg(f"❌ You have 0 {c} available.");return
        q,a_s=(fb,"ALL") if amt is None or amt/p>=fb else (amt/p,f"${amt:.2f}")
        q=float(ex.amount_to_precision(cs,q))
        if q<=0:tg("❌ Amount too small.");return
        o=ex.create_market_sell_order(cs,q);px=(o or {}).get('average') or p;pnl=(px-db[sym]["entry_price"])*q;rec(sym,pnl,f"MANUAL SELL {a_s}");time.sleep(1);rem=B('total',c)
        if rem<=0 or rem*p<1.02:db.pop(sym,None);save(DB,db);tg(f"✅ *SOLD:* {q} {c} @ {px:.6f}\nPnL: ${pnl:.2f}\nPosition closed.")
        else:place_trailing_sl(sym,rem);save(DB,db);tg(f"✅ *SOLD:* {q} {c} @ {px:.6f}\nPnL: ${pnl:.2f}\nRemaining: {rem:.4f} {c} (stop re-armed)")
    except Exception as e:tg(f"❌ *SELL FAILED:* {e}")

def cmd_sync():
    tg("🔄 Syncing manual coins to bot database...")
    try:b=ex.fetch_balance()['total']
    except:tg("❌ Failed to fetch balance.");return
    db=load(DB);added=[]
    for c,a in b.items():
        if c in STABLE or a<=0:continue
        sym=f"{c}-USDT";p=live(sym)
        if p and a*p>1.02 and sym not in db:
            db[sym]=NP(p,round(p*(1-cfg()['stop_loss_pct']/100),6));save(DB,db);added.append(sym);place_trailing_sl(sym,a)
    tg(f"✅ *SYNCED:* Added {len(added)} coins to DB:\n`{chr(10).join(added)}`\n_Bot-side stop-loss + trailing active._" if added else "✅ No new manual coins found to sync.")

def panic_sell():
    db=load(DB)
    if not db:
        try:u=B('free','USDT')
        except Exception:u=0
        tg(f"🚨 *PANIC:* No open positions to sell. Your balance is ${u:.2f} USDT.");return
    tg("🚨 *PANIC COMMAND INITIATED!* Selling all positions...");failed=[]
    for sym in list(db):
        if sell(sym,1.0,"PANIC"):db.pop(sym,None)
        else:failed.append(sym)
    save(DB,db);save(QF,{});s=cfg();s['auto_buy_enabled']=False;save(CFG,s)
    tg(f"⚠️ *PANIC INCOMPLETE.* Still held (sell failed): `{', '.join(failed)}`\nAuto-buy PAUSED. Retry with /panic or sell manually." if failed else "🛑 *PANIC COMPLETE.* All positions sold. Auto-buy PAUSED.")

def get_summary_text():
    H=load(HIST);now=time.time()
    def calc(l):return (sum(t['pnl'] for t in l),len(l),len([t for t in l if t['pnl']>0])/len(l)*100) if l else (0.0,0,0.0)
    dp,dc,dw=calc([t for t in H if now-t['time']<=86400]);wp,wc,ww=calc([t for t in H if now-t['time']<=604800])
    return f"📊 *PERFORMANCE SUMMARY*\n\n*24 HOURS*\nPnL: ${dp:.2f}\nTrades: {dc}\nWin Rate: {dw:.1f}%\n\n*7 DAYS*\nPnL: ${wp:.2f}\nTrades: {wc}\nWin Rate: {ww:.1f}%"

def get_balance_text():
    b=ex.fetch_balance()['free']
    lines=[f"USDT: ${b.get('USDT',0):.2f}"]
    for c,a in b.items():
        if a>0 and c not in ['USDT','USDC']:
            p=live(f"{c}-USDT")
            if p and a*p>=0.10:
                lines.append(f"{c}: {a:.4f} (about ${a*p:.2f})")
    return "\n".join(lines)

def check_hourly_balance():
    st=load(SC);now=time.time()
    if now-st.get('last_bal_alert',0)>=3600:tg(f"💰 *HOURLY BALANCE*\n{get_balance_text()}");st['last_bal_alert']=now;save(SC,st)

def audit_system():
    issues=[];db=load(DB);q=load(QF)
    try:bal=ex.fetch_balance()['total']
    except Exception as e:tg(f"⚠️ *AUDIT WARNING:* Failed to fetch exchange data: `{e}`");return "⚠️ Audit failed."
    for sym in list(db):
        amt=bal.get(sym.split('-')[0],0)
        px=live(sym)
        if amt<=0 or (px and amt*px<1.02):db.pop(sym,None);issues.append(f"🧹 Removed ghost/dust position: {sym}");continue
        orders,ok=open_sells(sym)
        if not ok:continue
        if len(orders)!=1 and not in_cd(f"auditsl_{sym}",1800):
            mk_cd(f"auditsl_{sym}");issues.append(f"🚨 {sym}: found {len(orders)} sell/stop orders (expected 1). Re-arming...");save(DB,db);place_trailing_sl(sym,amt)
    for c,a in bal.items():
        if c in STABLE or a<=0:continue
        sym=f"{c}-USDT"
        if sym not in db:
            p=live(sym)
            if p and a*p>1.02:
                db[sym]=NP(p,round(p*(1-cfg()['stop_loss_pct']/100),6));save(DB,db);issues.append(f"🔄 Auto-synced orphaned balance: {sym} (Entry: {p:.6f})");place_trailing_sl(sym,a)
    qc=False
    for sym in list(q):
        if sym in db or time.time()-q[sym].get('timestamp',0)>86400:q.pop(sym,None);qc=True
    if issues or qc:
        save(DB,db)
        if qc:save(QF,q)
        if issues:m="🛠️ *AUTO-AUDIT COMPLETE*\nFound and fixed issues:\n\n"+"\n".join(issues);tg(m);return m
    return "✅ *AUDIT COMPLETE:* No issues found. System healthy."

def ask_ai(p):
    try:
        db=load(DB);q=load(QF);s=cfg();bal=B('free','USDT');br=data("BTC-USDT")[2]
        pos=[f"  {sym}:entry={d['entry_price']},pnl={((live(sym) or d['entry_price'])-d['entry_price'])/d['entry_price']*100:+.1f}%" for sym,d in list(db.items())[:5]] or ["  (none)"]
        ctx=f"USDT:{bal:.2f}|AutoBuy:${s['auto_buy_amount_usdt']}|BTC RSI:{br}|Pos({len(db)}):\n"+"\n".join(pos)+f"\nQueue:{len(q)}|RSI {s['rsi_min']}-{s['rsi_max']},SL {s['stop_loss_pct']}%\n\n"
        txt=err=used=None
        for m in GEMINI_MODELS:
            try:txt=client.models.generate_content(model=m,contents=f"Expert crypto analyst. Plain text only, no markdown, under 3000 characters.\n{ctx}Q:{p}").text;used=m;break
            except Exception as e:err=e
        if not txt:raise err or Exception("empty reply")
        _send(f"🧠 AI ({used}):\n\n{txt[:3800]}",None)
    except Exception as e:tg(f"❌ AI Error: {e}")

def manage():
    db=load(DB);s=cfg();tp=s.get('trail_pct',0) or 0
    for sym,d in list(db.items()):
        d.setdefault('opened_at',time.time());r=data(sym)[2];lv=live(sym)
        if not lv:continue
        e=d["entry_price"];inc=(lv-e)/e*100;d['peak_price']=max(d.get('peak_price',e),lv);lp=d.get('last_price',0)
        if lp>0:
            drop=(lv-lp)/lp*100
            if drop<=-s.get('crash_guard_pct',18) and sell(sym,1.0,f"CRASH GUARD ({drop:.1f}%)"):db.pop(sym,None);save(DB,db);continue
        d['last_price']=lv
        if inc>=5.0 and d['stop_loss']<e:d['stop_loss']=e
        c=[(round(e*(1-s['stop_loss_pct']/100),6),f"SL HIT ({s['stop_loss_pct']}%)"),(d['stop_loss'],"STOP LOSS")]
        if tp>0:c.append((d['peak_price']*(1-tp/100),f"TRAILING STOP ({tp}%)"))
        lvl,why=max(c,key=lambda x:x[0])
        if lv<=lvl:
            if sell(sym,1.0,why):db.pop(sym,None);save(DB,db)
            continue
        mh=s.get('max_hold_hours',48)
        if (time.time()-d['opened_at'])/3600>mh and inc<=1.0:
            if sell(sym,1.0,f"TIME STOP {mh}h ({inc:+.1f}%)"):db.pop(sym,None);save(DB,db)
            continue
        if r and s['rsi_exit']>0 and r>=s['rsi_exit'] and sell(sym,1.0,f"RSI {s['rsi_exit']}"):db.pop(sym,None);save(DB,db);continue
        ratchet(sym,d,lvl,lv)
        db[sym]=d
    save(DB,db)

def consolidate():
    db=load(DB);mx=cfg()['max_tokens_before_consolidation']
    if len(db)<=mx:return
    now=time.time();rk=[]
    for sym,d in db.items():
        p=live(sym)
        if p and d.get('entry_price') and now-d.get('opened_at',0)>=21600:rk.append({"s":sym,"pnl":(p-d['entry_price'])/d['entry_price']*100})
    if not rk:return
    rk.sort(key=lambda x:x["pnl"]);ex_n=len(db)-mx
    tg(f"⚖️ *CONSOLIDATING:* {len(db)} positions, limit {mx}. Trimming worst {min(ex_n,len(rk))}.")
    for t in rk[:ex_n]:
        if sell(t['s'],1.0,"Consolidation"):db.pop(t['s'],None);save(DB,db)

def btc_blocked(s):
    m=s.get('btc_rsi_min',0)
    if m<=0:return False
    br=data("BTC-USDT")[2]
    if br is not None and br>=m:return False
    cd("btcblock",21600) or tg(f"⏸ *BTC FILTER:* BTC RSI is {br} (needs ≥ {m}). Auto-buying paused until it recovers.")
    return True

def exec_q():
    q=load(QF);db=load(DB);s=cfg();mx=s["max_tokens_before_consolidation"]
    if not q or not s["auto_buy_enabled"] or btc_blocked(s):return
    try:
        ub=B('free','USDT')
        for sym,qd in sorted(q.items(),key=lambda x:x[1].get('score',0),reverse=True):
            if in_cd(f"fail_{sym}",300):continue
            if sym in db:q.pop(sym,None);save(QF,q);continue
            if not live(sym):continue
            full=len(db)>=mx;spend=ub
            if spend<1.02 and db:
                wk=None;low=999
                for hs,hd in db.items():
                    hp=live(hs)
                    if hp:
                        pn=(hp-hd['entry_price'])/hd['entry_price']*100
                        if pn<low:low=pn;wk=hs
                if wk:
                    bc=wk.split('-')[0]
                    if qd.get('score',0)>35 or low<0:
                        tg(f"🔄 *ROTATION SELL:* Dropping {wk} (PnL {low:.1f}%) to buy {sym}")
                        if sell(wk,1.0,"ROTATION"):db.pop(wk,None);save(DB,db);time.sleep(2);ub=B('free','USDT');spend=ub
                    elif not full:
                        cancel_sells(wk);time.sleep(0.5);hb=B('free',bc);hp=live(wk)
                        if hb>0 and hp:
                            qs=float(ex.amount_to_precision(S(wk),min(s['auto_buy_amount_usdt'],hb*hp)/hp))
                            if qs*hp<hb*hp*0.95:
                                ex.create_market_sell_order(S(wk),qs);tg(f"🔄 *PARTIAL SWAP:* Sold {qs} {bc} for {sym}.");time.sleep(2);ub=B('free','USDT');spend=ub;rem=B('total',bc)
                                if rem>0 and rem*hp>1.02:place_trailing_sl(wk,rem)
                                else:db.pop(wk,None);save(DB,db)
                            else:place_trailing_sl(wk,hb)
                        elif hb>0:place_trailing_sl(wk,hb)
            if len(db)>=mx:continue
            if spend<1.00:q.pop(sym,None);save(QF,q);continue
            sp=min(spend,s['auto_buy_amount_usdt'])
            try:f,a=m_buy(S(sym),sp)
            except Exception as e:tg(f"❌ Buy fail {sym}:{e}");mk_cd(f"fail_{sym}");continue
            mxs=round(a*(1-s['stop_loss_pct']/100),6);sl=qd.get('sl') or mxs
            if sl>=a or sl>mxs:sl=mxs
            db[sym]=NP(round(a,6),sl);save(DB,db);ok=place_trailing_sl(sym,free_qty(sym,f));tg(f"🛒 *EXECUTED!*\n{sym}|💵{sp:.2f}@{a:.6f}\n"+(STOP_OK if ok else STOP_NO))
            q.pop(sym,None);save(QF,q);ub=B('free','USDT');time.sleep(2)
    except Exception as e:tg(f"⚠️ *QUEUE FAILED:* `{e}`")

def scan():
    st=load(SC)
    if time.time()-st.get('last',0)<3540:return
    db=load(DB);q=load(QF);s=cfg()
    if not s["auto_buy_enabled"]:return
    if btc_blocked(s):return
    if len(db)>s["max_tokens_before_consolidation"]:consolidate()
    try:tk=ex.fetch_tickers();mk=ex.load_markets()
    except:return
    v=[{'sym':k.replace("/","-"),'vol':t['quoteVolume'] or 0,'chg':t['percentage'] or 0} for k,t in tk.items() if k.endswith('/USDT') and not skip(k.split('/')[0]) and not (k in mk and not mk[k].get('spot',False))]
    v.sort(key=lambda x:x['vol'],reverse=True);ct=int(time.time());qu=False
    for sym,d in list(q.items()):
        if ct-d.get('timestamp',0)>86400:q.pop(sym,None);qu=True
    for i,t in enumerate(v[15:500]):
        if t['vol']<(1000000 if i<=84 else 3000000 if i<=183 else 4000000 if i<=383 else 5000000):continue
        p,pr,r,ma5,ma10=data(t['sym'])
        if not p or r is None:continue
        setup=sl=None
        if 1.0<t['chg']<30.0 and s['rsi_min']<=r<=s['rsi_max'] and p>ma5>ma10:setup="TREND";sl=round(ma5*(1-s['stop_loss_pct']/100),6)
        elif -30.0<t['chg']<-8.0 and r<30 and p>pr:setup="OVERSOLD";sl=round(p*(1-s['stop_loss_pct']/100),6)
        if setup:
            rr=(p*0.10)/(p-sl) if (p-sl)>0 else 0;sc=t['vol']/1e6+rr*10
            if t['sym'] not in q or q[t['sym']].get('score',0)<sc:
                if t['sym'] not in q:tg(f"📥 *QUEUED:* {t['sym']}\nScore:{sc:.1f}")
                q[t['sym']]={"timestamp":ct,"type":setup,"sl":sl,"score":sc,"price":p,"chg":t['chg']};qu=True
    if qu:save(QF,q)
    st['last']=time.time();save(SC,st)
    if q:
        lines=[]
        for sym,d in sorted(q.items(),key=lambda x:x[1].get('score',0),reverse=True):
            lines.append(f"{sym.split('-')[0]} | Price: {fp(d.get('price'))} | 24H: {d.get('chg',0):+.1f}% | Score: {d.get('score',0):.1f}")
        tg(f"🔍 *SCAN COMPLETE*\nQueue has {len(q)} setup(s):\n" + "\n".join(lines))
    else:tg("🔍 *SCAN COMPLETE*\nNo setups found in this cycle.")

def check_daily_summary():
    st=load(SC);now=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=TZ)));today=now.strftime('%Y-%m-%d')
    if now.hour>=20 and st.get('last_summary_day')!=today:tg(get_summary_text());st['last_summary_day']=today;save(SC,st)

def fp(p):
    if p is None:return "N/A"
    return f"{p:.2f}" if p>=100 else f"{p:.4f}" if p>=1 else f"{p:.6f}" if p>=0.001 else f"{p:.8f}"

def get_market_row(sym,chg=None):
    if chg is None:
        try:chg=ex.fetch_ticker(S(sym))['percentage'] or 0
        except:chg=0
    r=data(sym)[2]
    rsi=f"{r:.1f}" if r is not None else "N/A"
    return f"{sym.split('-')[0]} | Price: {fp(live(sym))} | 24H: {chg:+.1f}% | RSI: {rsi}"

def gl():
    try:tk=ex.fetch_tickers()
    except:return "❌","❌"
    v=sorted([{'sym':k.replace("/","-"),'chg':t['percentage'] or 0} for k,t in tk.items() if k.endswith('/USDT') and not skip(k.split('/')[0]) and (t['quoteVolume'] or 0)>=1000000],key=lambda x:x['chg'],reverse=True)
    def build(title,items):
        lines=[get_market_row(x['sym'],x['chg']) for x in items]
        return f"*{title}*\n" + ("\n".join(lines) if lines else "No results found.")
    return build("🚀 GAINERS (24H)",v[:5]),build("🩸 LOSERS (24H)",v[-5:][::-1])

def cmd_set(txt):
    p=txt.split()
    if len(p)!=3:tg("❌ Usage: `/set <key> <value>`");return
    k,val=p[1],p[2];s=cfg()
    if k not in s:tg(f"❌ Unknown key. Valid: `{'`, `'.join(s.keys())}`");return
    try:new=val.lower() in ('true','1','yes','on') if isinstance(s[k],bool) else float(val) if isinstance(s[k],float) else int(val)
    except:tg(f"❌ Invalid value for {k}");return
    s[k]=new;save(CFG,s);tg(f"✅ `{k}` = `{new}`")

def setc(k,v,m):s=cfg();s[k]=v;save(CFG,s);tg(m)
MSG={"ai_info":"🧠 *AI ANALYST*\nTo ask the AI a question, type:\n`/ai <your question>`\n\nExample:\n`/ai what is the market sentiment right now?`","buy_info":"🛒 *BUY COMMAND*\nUsage: `/buy COIN AMOUNT`\nExample: `/buy SOL 5.75`","sell_info":"💸 *SELL COMMAND*\nUsage: `/sell COIN AMOUNT`\nExample: `/sell SOL 5.75`\n\nType `ALL` instead of an amount to sell the entire position.","/help":"🤖 *CMDS*\n`/menu` `/ai [q]` `/sync` `/audit`\n`/gainers` `/losers` `/summary`\n`/balance` `/status` `/pnl`\n`/buy COIN AMT` `/sell COIN AMT|ALL`\n`/set key value` `/pause` `/resume` `/panic`"}

def handle(t,age):
    l=t.lower()
    if l.startswith("/ai "):ask_ai(t[4:].strip())
    elif l=="/ai":ask_ai("Give me a market overview.")
    elif l in MSG:tg(MSG[l])
    elif l in ("/menu","📊 menu"):menu()
    elif l in ("/panic","🚨 panic"):panic_prompt()
    elif l=="panic_cancel":tg("👍 Panic cancelled.")
    elif l=="panic_confirm":panic_sell() if age<=900 else tg("⌛ That confirmation expired. Send /panic again if you still want to sell everything.")
    elif l=="/sync":cmd_sync()
    elif l=="/audit":
        r=audit_system()
        if r and r.startswith("✅"):tg(r)
    elif l=="trail_on":setc('trail_pct',8.0,"📈 Trailing Stop-Loss turned ON (8%)");menu()
    elif l=="trail_off":setc('trail_pct',0.0,"📉 Trailing Stop-Loss turned OFF");menu()
    elif l=="/balance":tg(f"💰 *BALANCE*\n{get_balance_text()}")
    elif l=="/summary":tg(get_summary_text())
    elif l=="/status":
        db=load(DB)
        if not db:tg("🛑 No active trades.")
        else:
            lines=[]
            for sym,d in db.items():
                p=live(sym)
                if p:
                    change=(p-d['entry_price'])/d['entry_price']*100
                    lines.append(f"{sym.split('-')[0]} | Entry: {fp(d['entry_price'])} | Now: {fp(p)} | PnL: {change:+.1f}%")
            tg("🟢 *ACTIVE POSITIONS*\n" + ("\n".join(lines) if lines else "No live prices available."))
    elif l=="/pnl":
        L=load(LF);tp=L.get('total_realized_usdt',0.0);n=L.get('trade_count',0)
        tg(f"📊 *P&L SUMMARY*\nTotal realized PnL: ${tp:.2f}\nCompleted trades: {n}")
    elif l=="/gainers":tg(gl()[0])
    elif l=="/losers":tg(gl()[1])
    elif l.startswith("/buy"):cmd_buy(t)
    elif l.startswith("/sell"):cmd_sell(t)
    elif l.startswith("/set"):cmd_set(t)
    elif l=="/pause":setc('auto_buy_enabled',False,"⏸ Auto-buy paused.")
    elif l in ("/resume","▶️ resume"):setc('auto_buy_enabled',True,"▶️ Auto-buy resumed.")

def tg_check():
    st=load(ST);lid=st.get("last_update_id",0)
    try:
        res=requests.get(f"{API}/getUpdates?offset={lid+1}&timeout=0",timeout=10).json()
        if not (res.get("ok") and res.get("result")):return
        top=max(u["update_id"] for u in res["result"]);st["last_update_id"]=top;save(ST,st)
        try:requests.get(f"{API}/getUpdates?offset={top+1}&limit=1&timeout=0",timeout=10)  # ack on Telegram's side BEFORE acting: overlapping/stale runs can never replay these commands
        except Exception:pass
        for u in res["result"]:
            txt=cid="";age=0
            if "callback_query" in u:
                cb=u["callback_query"];txt=cb.get("data","");m=cb.get("message") or {}
                try:requests.post(f"{API}/answerCallbackQuery",json={"callback_query_id":cb["id"]},timeout=10)
                except Exception:pass
            elif "text" in u.get("message",{}):m=u["message"];txt=m["text"].strip()
            if not txt:continue
            cid=str(m.get("chat",{}).get("id",""));age=time.time()-m.get("date",time.time())
            if cid!=CHAT:print(f"Ignored update from unauthorized chat {cid}");continue
            if age>1800:continue
            try:handle(txt,age)
            except Exception as e:print(f"HANDLER ERR {txt[:20]}: {e}");tg(f"⚠️ Command failed: `{e}`")
    except Exception as e:print(f"TG Err:{e}")

def acquire_lock():
    if fcntl is None:return True
    f=open(LOCK,"w")
    try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);return f
    except OSError:return None

def git_sync():
    ident="trading-bot";mail="bot@users.noreply.github.com"
    env={**os.environ,"GIT_AUTHOR_NAME":ident,"GIT_AUTHOR_EMAIL":mail,"GIT_COMMITTER_NAME":ident,"GIT_COMMITTER_EMAIL":mail}  # CI runners have no git identity: without this every commit/rebase fails
    run=lambda c:subprocess.run(c,capture_output=True,text=True,timeout=60,env=env)
    def fail(m):print(m);cd("gitfail",3600) or tg(f"⚠️ *STATE NOT SAVED:* `{m[:150]}`\nBot memory may reset on the next run.")
    try:
        run(["git","add","--"]+[f for f in FILES if os.path.exists(f)])
        if run(["git","diff","--cached","--quiet"]).returncode!=0:
            r=run(["git","commit","-m","Auto"])
            if r.returncode!=0:fail(f"git commit failed: {r.stderr[-150:]}");return
        r=run(["git","pull","--rebase","--autostash"])
        if r.returncode!=0:run(["git","rebase","--abort"]);fail(f"git pull failed: {r.stderr[-150:]}");return
        r=run(["git","push"])
        if r.returncode!=0:fail(f"git push failed: {r.stderr[-150:]}");return
        bad=[t for t in run(["git","ls-files"]).stdout.split() if re.search(r'(^|/)(\.env|.*\.pem|.*\.key|.*secret.*)',t,re.I)]
        if bad and not cd("gitwarn",86400):tg(f"🚨 *SECURITY:* secret-looking files are tracked in git: `{', '.join(bad[:5])}`. Remove them (git rm --cached) and rotate your keys.")
    except Exception as e:print(f"git sync: {e}")

if __name__=="__main__":
    lk=acquire_lock()
    if lk is None:print("Another run is in progress; exiting.");sys.exit(0)
    try:_C.clear();audit_system();tg_check();manage();git_sync();check_daily_summary();check_hourly_balance();scan();exec_q()
    except Exception as e:
        try:tg(f"🚨 *CRASH:* `{e}`\n`{traceback.format_exc()[-200:]}`")
        except:pass
    git_sync()
