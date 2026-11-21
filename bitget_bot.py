import requests,json,os,time,ccxt,subprocess,traceback,re,datetime,sys
try:import fcntl
except ImportError:fcntl=None
TK=os.environ.get('TG_TOKEN');CHAT=str(os.environ.get('TG_CHAT_ID',''));API=f"https://api.telegram.org/bot{TK}"
DB,ST,CFG,QF,LF,CD,SC,HIST,LOCK,SH,OUT,AL,ATHATL_CACHE="positions.json","tg_state.json","settings.json","queue.json","profit_ledger.json",".cmd_cooldown.json","scan_state.json","trade_history.json",".bot.lock","scan_history.json","telegram_output.json","alerts.json","ath_atl_cache.json"
FILES=[DB,ST,CFG,QF,LF,CD,SC,HIST,SH,OUT,AL,ATHATL_CACHE]
STABLE=['USDC','USDT','DAI','FDUSD','TUSD','USDD','USDE'];LEV=['3L','3S','5L','5S','10L','10S','2L','2S'];BL=['RPANW','CT','ONE']
ex=ccxt.bitget({'apiKey':os.environ.get('BITGET_API_KEY'),'secret':os.environ.get('BITGET_API_SECRET'),'password':os.environ.get('BITGET_API_PASSPHRASE'),'enableRateLimit':True});ex.options['createMarketBuyOrderRequiresPrice']=False
GROQ_API_KEY=os.environ.get('GROQ_API_KEY','').strip()
GROQ_MODEL=os.environ.get('GROQ_MODEL','openai/gpt-oss-120b').strip() or 'openai/gpt-oss-120b'
_C={};_BAL={"t":0,"m":""};LAYOUT_ITEMS=[("tools","🧰 Tools","m:tools"),("analyse","🏆 Analyse","/analyse"),("bot","🤖 Bot","/bot"),("stats","📈 Stats","/stats"),("an","🔎 Analyse Coin","m:an"),("set","⚙️ Settings","m:set"),("hold","💼 Holdings","m:r:holdings"),("ai","🧠 Ask AI","m:ai:1"),("clear","🧹 Clear Chat","/clear"),("panic","🚨 PANIC SELL","/panic")]
LAYOUT_DEFAULT=["tools","analyse"];LAYOUT_MAX=5
def layout_bottom():
 try:b=load(ST,{}).get("layout_bottom")
 except Exception:b=None
 if not isinstance(b,list):b=list(LAYOUT_DEFAULT)
 ok={i[0] for i in LAYOUT_ITEMS}
 return [k for k in b if k in ok][:LAYOUT_MAX]
def lbl(k):
 try:n=(load(ST,{}).get("layout_names") or {}).get(k)
 except Exception:n=None
 return n or ({"menu":"📋 Menu"}.get(k) or next((l for i,l,_ in LAYOUT_ITEMS if i==k),k))
def kb_markup():
 keys=[lbl("menu")]+[lbl(k) for k in layout_bottom()]
 return {"keyboard":[keys[i:i+3] for i in range(0,len(keys),3)],"resize_keyboard":True,"is_persistent":True}
def bottom_map():
 m={"📋 menu":"m:menu","📊 menu":"m:menu","🏆 analyse (top 3)":"/analyse"}
 for k,l,d in LAYOUT_ITEMS:m[l.lower()]=d
 m["📋 menu"]="m:menu"
 for k,l,d in LAYOUT_ITEMS:m[lbl(k).lower()]=d
 m[lbl("menu").lower()]="m:menu"
 return m
def rename_apply(t):
 st=load(ST,{});pd=st.get("rename_pending") or {};k=pd.get("key")
 st.pop("rename_pending",None);save(ST,st)
 if not k or time.time()-float(pd.get("t",0))>600:return
 name=re.sub(r'[*_`\[\]]','',t).strip();low=name.lower()
 if low=="cancel":_UI["lay_note"]="\nRename cancelled.";ui_cb("m:lay");return
 names=st.get("layout_names") or {}
 if low=="reset":
  names.pop(k,None);_UI["lay_note"]="\n✅ Name restored."
 else:
  taken={lbl(i).lower() for i in [x[0] for x in LAYOUT_ITEMS]+["menu"] if i!=k}
  if not (1<=len(name)<=20):_UI["lay_note"]="\n⚠️ Name must be 1-20 characters. Nothing changed.";ui_cb("m:lay");return
  if low in taken:_UI["lay_note"]="\n⚠️ That name is already used by another button. Nothing changed.";ui_cb("m:lay");return
  names[k]=name;_UI["lay_note"]="\n✅ Renamed."
 st=load(ST,{});st["layout_names"]=names;save(ST,st)
 ui_cb("m:lay")
S=lambda s:s.replace("-","/");TR=lambda t:{'trigger':True} if t else {};B=lambda k,c:ex.fetch_balance()[k].get(c,0)
skip=lambda b:b in STABLE or b in BL or ((b.startswith('X') or b.startswith('R')) and len(b)<=6) or any(b.upper().endswith(x) for x in LEV)
NP=lambda e,sl,pct=None:{"entry_price":e,"safe_zone":e,"stop_loss":sl,"peak_price":e,"last_price":e,"opened_at":time.time(),**({"sl_pct":pct} if pct else {})}
STOP_OK="🛡️ Exchange protective sell: Verified\n📈 Bot-side trailing: Active"
STOP_NO="⚠️ Exchange stop NOT verified.\nBot-side monitoring is active only while the bot is running; protection is not guaranteed."
_NGN={"t":0,"r":0}
def ngn_rate():
 if time.time()-_NGN["t"]<600 and _NGN["r"]:return _NGN["r"]
 try:
  r=float((requests.get("https://open.er-api.com/v6/latest/USD",timeout=8).json().get("rates") or {}).get("NGN") or 0)
  if r>0:_NGN["t"],_NGN["r"]=time.time(),r
 except Exception as e:print(f"NGN RATE ERR: {e}")
 return _NGN["r"]
def wallet_value(totals):
 v=0.0
 for c,q in (totals or {}).items():
  try:q=float(q or 0)
  except (TypeError,ValueError):continue
  if q<=0:continue
  if c in STABLE:v+=q;continue
  p=live(f"{c}-USDT")
  if p:v+=q*p
 return v
def balance_text(v):
 rate=ngn_rate();return f"USDT{v:.2f}"+(f"/₦{v*rate:,.0f}" if rate else "")
def compact_balance():
 if time.time()-_BAL["t"]<15:return _BAL["m"]
 try:
  txt=balance_text(wallet_value(ex.fetch_balance().get('total') or {}))
  _BAL["t"],_BAL["m"]=time.time(),txt;return txt
 except:return _BAL["m"]
def _mid(k):
 try:return load(ST,{}).get(k)
 except Exception:return None
def _set_mid(k,v):
 try:
  st=load(ST,{})
  if v:st[k]=v
  else:st.pop(k,None)
  save(ST,st)
 except Exception as e:print(f"DISPLAY STATE ERR: {e}")
def _del_msg(mid):
 if not mid:return
 try:requests.post(API+"/deleteMessage",json={"chat_id":CHAT,"message_id":mid},timeout=10)
 except Exception:pass
def _edit(mid,text,mode,rm=None):
 text=(text or "")[:4000]
 try:
  p={"chat_id":CHAT,"message_id":mid,"text":text}
  if mode:p["parse_mode"]=mode
  if rm:p["reply_markup"]=rm
  r=requests.post(API+"/editMessageText",json=p,timeout=10)
  if r.ok:return True
  body=r.json() if r.headers.get("content-type","").startswith("application/json") else {}
  d=str(body.get("description","")).lower()
  if "not modified" in d:return True
  if "parse" in d or "entities" in d:
   p.pop("parse_mode",None);p["text"]=re.sub(r'<[^>]+>','',text) if mode=="HTML" else text
   r=requests.post(API+"/editMessageText",json=p,timeout=10)
   return bool(r.ok)
 except Exception:pass
 return False
def _create(text,mode,rm,silent=False):
 text=(text or "")[:4000]
 try:
  p={"chat_id":CHAT,"text":text}
  if rm:p["reply_markup"]=rm
  if silent:p["disable_notification"]=True
  if mode:p["parse_mode"]=mode
  r=requests.post(API+"/sendMessage",json=p,timeout=10)
  if not r.ok:
   p.pop("parse_mode",None);p["text"]=re.sub(r'<[^>]+>','',text) if mode=="HTML" else text
   r=requests.post(API+"/sendMessage",json=p,timeout=10)
  if r.ok:
   mid=((r.json() or {}).get("result") or {}).get("message_id")
   if mid:
    out=load(OUT,[]);out.append({"message_id":mid,"sent_at":time.time()});save(OUT,out)
    return mid
 except Exception as e:print("TG EXCEPTION:",e)
 return None
def _make_out(m,mode):
 _del_msg(_mid("out_mid"));_del_msg(_mid("panel_mid"))
 _set_mid("panel_mid",None);_set_mid("out_mid",None)
 mid=_create(m,mode,None)
 if mid:
  st=load(ST,{});st["out_mid"]=mid;st["out_text"]=(m or "")[:3800];st["out_mode"]=mode or "Markdown";save(ST,st)
 return mid
def new_display():
 for k in ("out_mid","panel_mid"):
  _del_msg(_mid(k));_set_mid(k,None)
 _UI["mid"]=None
def _panel(text,rows,touch=True):
 if touch:_UI["panel_used"]=True
 rm={"inline_keyboard":rows}
 if not _mid("out_mid"):_make_out("📟 *OUTPUT*\nResults appear here.","Markdown")
 pm=_mid("panel_mid")
 if pm and _edit(pm,text,"Markdown",rm):_keep_panel(text,rows);return
 if pm:_del_msg(pm);_set_mid("panel_mid",None)
 nm=_create(text,"Markdown",rm,silent=True)
 if nm:
  st=load(ST,{});st["panel_mid"]=nm;st.pop("panel_hidden",None);st["panel_rows"]=rows;st["panel_text"]=text;save(ST,st)
def _hide_panel(extra=None):
 pm=_mid("panel_mid")
 for x in {pm,extra}:
  if x:_del_msg(x)
 _set_mid("panel_mid",None);_set_mid("panel_hidden",True)
def _keep_panel(text,rows):
 try:
  st=load(ST,{})
  if st.get("panel_rows")!=rows or st.get("panel_text")!=text:st["panel_rows"]=rows;st["panel_text"]=text;save(ST,st)
 except Exception:pass
def _carrier(ph):
 rm=kb_markup()
 if ph:rm["input_field_placeholder"]=ph
 old=_mid("kb_mid")
 nm=_create("💰 "+(ph or "Balance"),None,rm,silent=True)
 if nm:
  st=load(ST,{});st["kb_mid"]=nm;st["kb_ph"]=ph;st["kb_keys"]=str(rm["keyboard"]);save(ST,st)
  if old and old!=nm:_del_msg(old)
def _kb_refresh():
 st=load(ST,{});keys=str(kb_markup()["keyboard"]);ph=(compact_balance() or "")[:64]
 if not st.get("kb_mid") or st.get("kb_keys")!=keys or (ph and ph!=st.get("kb_ph")):_carrier(ph)
def _send(m,mode,btn=None):
 try:
  acc=_UI.get("acc")
  if acc is not None:
   pm,pt=acc if acc else (None,"")
   if pt and len(pt)>=120 and pm==mode and len(pt)+len(m)+2<=4000:m=pt+"\n\n"+m
   _UI["acc"]=(mode,m)
  out=_mid("out_mid")
  if not (out and _edit(out,m,mode or "Markdown",None)):_make_out(m,mode)
  else:
   try:
    st=load(ST,{});st["out_text"]=(m or "")[:3800];st["out_mode"]=mode or "Markdown";save(ST,st)
   except Exception:pass
  if btn:_panel("👇 *Actions*",btn.get("inline_keyboard") if isinstance(btn,dict) else btn)
  elif not _mid("panel_mid") and not _mid("panel_hidden"):
   pu=_UI.get("panel_used");menu();_UI["panel_used"]=pu
  _kb_refresh()
 except Exception as e:print("TG EXCEPTION:",e)
def cleanup_telegram_output():
 now=time.time();out=load(OUT,[]);keep=[]
 _st0=load(ST,{});persistent_mid=[_st0.get("out_mid"),_st0.get("panel_mid"),_st0.get("kb_mid")]
 for item in out:
  if item.get("message_id") in persistent_mid:
   keep.append(item);continue
  if now-float(item.get("sent_at",now))<float(cfg().get('cleanup_hours',24.0) or 24.0)*3600:keep.append(item);continue
  try:
   r=requests.post(API+"/deleteMessage",json={"chat_id":CHAT,"message_id":item.get("message_id")},timeout=10)
   if not r.ok:
    body=r.json() if r.headers.get("content-type","").startswith("application/json") else {}
    if body.get("error_code",0)>=500 or "too many requests" in str(body.get("description","")).lower():keep.append(item)
  except:keep.append(item)
 save(OUT,keep)
tg=lambda m:_send(m,"Markdown");tg_ai=lambda m:_send(m,"HTML")
def dust_candidates():
 get_list=getattr(ex,"privateConvertGetV2ConvertBgbConvertCoinList",None)
 if not callable(get_list):return None,"Your installed CCXT version does not expose Bitget's BGB-convert endpoints. Update CCXT (pip install -U ccxt) and try again."
 listed=get_list()
 if str((listed or {}).get("code"))!="00000":return None,f"Bitget eligibility check failed: {(listed or {}).get('msg','unknown API response')}"
 data=(listed or {}).get("data") or {}
 items=(data.get("coinList") or []) if isinstance(data,dict) else []
 tracked={k.split("-")[0].upper() for k in load(DB)}
 out=[]
 for x in items:
  c=str(x.get("coin","")).upper()
  try:a=float(x.get("available") or 0)
  except (TypeError,ValueError):a=0.0
  if not c or a<=0 or c in STABLE or c=="BGB" or c in BL or c in tracked:continue
  px=live(f"{c}-USDT");out.append({"coin":c,"amount":a,"value":(a*px) if px else None})
 return out,""
def cmd_dust(txt=""):
 confirm=txt.strip().lower().endswith("confirm")
 try:cands,err=dust_candidates()
 except Exception as e:tg(f"⚠️ Dust check failed, nothing changed: {str(e)[:200]}");return
 if cands is None:tg(f"⚠️ {err}");return
 if not cands:tg("🧹 *DUST*\nNo eligible small balances to convert right now.");return
 lines=[f"{x['coin']}: {x['amount']:.8g}"+(f" (≈${x['value']:.4f})" if x['value'] is not None else " (no USDT price)") for x in cands]
 if not confirm:
  tot=sum(x['value'] or 0 for x in cands)
  _send(f"🧹 *DUST → BGB*\n{len(cands)} eligible coin(s)"+(f", about ${tot:.2f} priced" if tot>0 else "")+":\n"+"\n".join(lines[:40])+("\n…" if len(lines)>40 else "")+"\n\nNothing is sold at market. Bitget converts these small balances to BGB. Coins in your active trades are never included.","Markdown",{"inline_keyboard":[[{"text":"✅ Convert all to BGB","callback_data":"/dust confirm"},{"text":"❌ Cancel","callback_data":"/dust cancel"}]]});return
 if cd("dust:confirm",30):tg("⏳ Dust conversion already processing...");return
 done=[];failed=[];unknown=False
 try:
  convert=getattr(ex,"privateConvertPostV2ConvertBgbConvert",None)
  if not callable(convert):tg("⚠️ CCXT does not expose the BGB conversion call. Nothing changed.");return
  coins=[x["coin"] for x in cands]
  for i in range(0,len(coins),20):
   batch=coins[i:i+20]
   try:
    res=convert({"coinList":batch})
    if str((res or {}).get("code"))!="00000":failed.append(f"{', '.join(batch)}: {(res or {}).get('msg','rejected')}");continue
    rd=(res or {}).get("data") or {}
    orders=(rd.get("orderList") or []) if isinstance(rd,dict) else []
    got={str(o.get("coin","")).upper() for o in orders}
    done+= [c for c in batch if c in got];failed+=[f"{c}: no order returned" for c in batch if c not in got]
   except Exception as e:
    unknown=True;failed.append(f"{', '.join(batch)}: outcome UNKNOWN ({str(e)[:120]})")
 except Exception as e:failed.append(str(e)[:200])
 msg="🧹 *DUST → BGB*\n"+(f"✅ Conversion accepted for {len(done)} coin(s): {', '.join(done)}\n" if done else "")+("⚠️ "+"\n⚠️ ".join(failed)+"\n" if failed else "")
 if unknown:msg+="Do not retry until you check Bitget's conversion history and balances.\n"
 tg(msg+"Final status is in Bitget conversion history.")
def cmd_clear(auto=False):
 out=load(OUT,[]);n=0;keep=[];t0=time.time()
 for i,item in enumerate(out):
  if time.time()-t0>90:keep.extend(out[i:]);break
  try:
   r=requests.post(API+"/deleteMessage",json={"chat_id":CHAT,"message_id":item.get("message_id")},timeout=10)
   if r.ok:n+=1
   else:
    body=r.json() if r.headers.get("content-type","").startswith("application/json") else {}
    if body.get("error_code",0)>=500 or "too many requests" in str(body.get("description","")).lower():keep.append(item)
  except Exception:keep.append(item)
 save(OUT,keep)
 if not keep:_set_mid("out_mid",None);_set_mid("panel_mid",None);_set_mid("kb_mid",None)
 tg((f"🧹 Daily auto-clear: removed {n} message(s)." if auto else f"🧹 Cleared {n} bot message(s).")+(f" {len(keep)} left; send /clear again." if keep else ""))
def auto_clear_daily():
 h=float(cfg().get('auto_clear_hours',24.0) or 0)
 if h<=0:return
 st=load(SC);now=time.time();a=st.get('clear_anchor')
 if not a:st['clear_anchor']=now;save(SC,st);return
 if now-a>=h*3600:
  st['clear_anchor']=a+int((now-a)//(h*3600))*h*3600;save(SC,st);cmd_clear(auto=True)
def menu():
 b=lambda t,c:{"text":t,"callback_data":c}
 bot_keys=layout_bottom()
 btns=[b(lbl(k),d) for k,l,d in LAYOUT_ITEMS if k not in bot_keys]
 ui_send("🕹️ *MENU*\nSelect an option below.",[btns[i:i+2] for i in range(0,len(btns),2)])
def panic_prompt():_send("🚨 *CONFIRM PANIC*\nThis sells ALL positions at market and pauses auto-buy.\nConfirm within 15 minutes.","Markdown",{"inline_keyboard":[[{"text":"✅ YES, SELL EVERYTHING","callback_data":"panic_confirm"},{"text":"❌ Cancel","callback_data":"panic_cancel"}]]})
def load(f,d=None):
 d=[] if f==HIST else ({} if d is None else d)
 try:
  if os.path.exists(f):
   with open(f) as h:
    data=json.load(h)
    if f==HIST and not isinstance(data,list):return d
    return data
 except Exception as e:print(f"LOAD ERR {f}: {e}")
 return d
def save(f,d):
 try:
  with open(f+".tmp","w") as h:json.dump(d,h,indent=4)
  os.replace(f+".tmp",f)
 except Exception as e:print(f"SAVE ERR {f}: {e}")
def cfg():
 d={"rsi_min":42,"rsi_max":48,"stop_loss_pct":5.0,"auto_buy_enabled":True,"auto_buy_amount_usdt":10.0,"max_tokens_before_consolidation":3,"max_hold_hours":48,"trail_pct":8.0,"crash_guard_pct":10.0,"rsi_exit":75,"btc_rsi_min":50,"risk_per_trade_pct":1.0,"max_daily_loss_pct":3.0,"max_portfolio_exposure_pct":50.0,"max_position_pct":15.0,"max_daily_trades":5,"max_spread_pct":0.5,"auto_mode_v1":False,"rebuy_cooldown_hours":6.0,"atr_stop_enabled":True,"atr_stop_mult":2.0,"atr_stop_min_pct":3.0,"atr_stop_max_pct":10.0,"market_filter_enabled":True,"btc_trend_hours":50,"min_breadth_pct":30.0,"offline_alert_hours":3.0,"daily_summary_hour_utc":20,"max_daily_drawdown_pct":5.0,"partial_tp_pct":5.0,"partial_tp_fraction":0.5,"dynamic_trail":True,"sell_if_stop_fails":True,"scan_prefilter":True,"cleanup_hours":24.0,"scan_interval_minutes":15.0,"auto_clear_hours":24.0,"live_mode_seconds":270,"mtf_confirm":True,"btc_shock_pct":3.0,"max_open_risk_pct":3.0,"loss_streak_trigger":2,"loss_streak_size_factor":0.5,"scan_parallel":True}
 s=load(CFG,d)
 for k,v in d.items():s.setdefault(k,v)
 if not s.get('auto_mode_v1'):s['auto_buy_enabled']=True;s['auto_mode_v1']=True;save(CFG,s)
 return s
def in_cd(k,s=5):return time.time()-load(CD,{}).get(k,0)<s
def mk_cd(k):d=load(CD,{});d[k]=time.time();save(CD,d)
def cd(k,s=5):
 if in_cd(k,s):return True
 mk_cd(k);return False
def atr_pct(sym):
 try:
  o=ex.fetch_ohlcv(S(sym),'1h',limit=30)
  if len(o)<16:return None
  k=o[:-1];h=[float(x[2]) for x in k];lo=[float(x[3]) for x in k];c=[float(x[4]) for x in k]
  trs=[max(h[i]-lo[i],abs(h[i]-c[i-1]),abs(lo[i]-c[i-1])) for i in range(1,len(c))]
  a=sum(trs[-14:])/14
  return a/c[-1]*100 if c[-1]>0 else None
 except:return None
def sl_pct(sym,d=None):
 s=cfg();base=float(s['stop_loss_pct'])
 if d is not None:return float(d.get('sl_pct') or base)
 if not s.get('atr_stop_enabled',True):return base
 a=atr_pct(sym)
 if a is None:return base
 lo=float(s.get('atr_stop_min_pct',3.0));hi=float(s.get('atr_stop_max_pct',10.0))
 return round(min(max(a*float(s.get('atr_stop_mult',2.0)),lo),hi),2)
def spread_pct(sym):
 try:
  t=ex.fetch_ticker(S(sym));b=t.get('bid');a=t.get('ask')
  if b and a and a>=b:return (a-b)/((a+b)/2)*100
 except:pass
 return None
_MKT={"t":0,"r":""}
def market_filter(s):
 if not s.get('market_filter_enabled',True):return ""
 if time.time()-_MKT["t"]<300:return _MKT["r"]
 reason=""
 try:
  n=max(10,int(s.get('btc_trend_hours',50)))
  o=ex.fetch_ohlcv("BTC/USDT",'1h',limit=n+10)
  c=[float(k[4]) for k in o[:-1]]
  if len(c)>=n and c[-1]<sum(c[-n:])/n:reason=f"BTC is below its {n}h average"
  if not reason:
   ch=[t.get('percentage') for k,t in ex.fetch_tickers().items() if k.endswith('/USDT') and (t.get('quoteVolume') or 0)>=1000000 and t.get('percentage') is not None]
   if len(ch)>=30:
    up=sum(1 for x in ch if x>0)/len(ch)*100
    if up<float(s.get('min_breadth_pct',30.0)):reason=f"only {up:.0f}% of coins are up today"
 except Exception as e:print("MARKET FILTER ERR:",e);reason=""
 _MKT["t"],_MKT["r"]=time.time(),reason;return reason
def setup_score(setup,vol,r,ma5,ma10,bvol=None):
 sc=({"BREAKOUT":3,"TREND":2,"OVERSOLD":1}.get(setup,0))*100+min(vol/1e6,100)*0.2
 if ma10:sc+=max(0.0,min(20.0,(ma5-ma10)/ma10*1000))
 sc+=max(0.0,min(10.0,(30-r)/3)) if setup=="OVERSOLD" else max(0.0,10-abs(r-55)/2)
 if bvol:sc+=min(float(bvol),5.0)*4
 return round(sc,2)
def cmd_stats():
 H=load(HIST)
 if not isinstance(H,list) or not H:tg("📈 *TRADE STATS*\nNo closed trades yet.");return
 pn=[float(t.get('pnl',0)) for t in H];w=sum(1 for x in pn if x>0);lo=sum(1 for x in pn if x<0);g={}
 for t in H:g.setdefault(str(t.get('strategy','OLDER')).upper(),[]).append(float(t.get('pnl',0)))
 rows=[f"{k}: {len(v)} trades | win {sum(1 for x in v if x>0)/len(v)*100:.0f}% | avg ${sum(v)/len(v):+.2f} | total ${sum(v):+.2f}" for k,v in sorted(g.items(),key=lambda x:-sum(x[1]))]
 tg("\n".join(["📈 *TRADE STATS*",f"Closed trades: {len(pn)}",f"Win rate: {w/len(pn)*100:.0f}% ({w} wins, {lo} losses)",f"Average per trade: ${sum(pn)/len(pn):+.2f}",f"Net profit: ${sum(pn):+.2f}",f"Best: ${max(pn):+.2f} | Worst: ${min(pn):+.2f}","","*By strategy*"]+rows))
def daily_summary_text():
 now=time.time();H=load(HIST);day=[t for t in H if now-float(t.get('time',0))<86400] if isinstance(H,list) else []
 nm=lambda t:str(t.get('sym','?')).replace('-USDT','');lines=["🗓 *DAILY SUMMARY*"]
 if day:
  pn=[float(t.get('pnl',0)) for t in day];w=sum(1 for x in pn if x>0);b=max(day,key=lambda t:float(t.get('pnl',0)));wo=min(day,key=lambda t:float(t.get('pnl',0)))
  lines+=[f"Trades closed (24h): {len(pn)}",f"Win rate: {w/len(pn)*100:.0f}% ({w}/{len(pn)})",f"Net profit: ${sum(pn):+.2f}",f"Best: {nm(b)} ${float(b.get('pnl',0)):+.2f}",f"Worst: {nm(wo)} ${float(wo.get('pnl',0)):+.2f}"]
 else:lines.append("No trades closed in the last 24 hours.")
 L=load(LF);lines.append(f"Open positions: {len(load(DB))}");lines.append(f"All-time realized: ${float(L.get('total_realized_usdt',0.0)):+.2f} ({int(L.get('trade_count',0))} trades)")
 bal=compact_balance()
 if bal:lines.append("💼 "+bal)
 return "\n".join(lines)
def check_daily_summary():
 st=load(SC);now=datetime.datetime.now(datetime.timezone.utc);today=now.strftime("%Y-%m-%d")
 if now.hour<int(cfg().get('daily_summary_hour_utc',20)) or st.get('last_daily')==today:return
 tg(daily_summary_text());st['last_daily']=today;save(SC,st)
def heartbeat():
 st=load(SC);now=time.time();last=float(st.get('last_run',0) or 0);st['last_run']=now;save(SC,st)
 lim=float(cfg().get('offline_alert_hours',3.0))
 if last and lim>0 and (now-last)/3600>=lim:tg(f"⚠️ *BOT WAS OFFLINE* for {(now-last)/3600:.1f}h.\nExchange stops stayed active; bot-side checks (trailing, crash guard, time stop) were paused.")
def ping_watchdog():
 u=os.environ.get('HEALTHCHECK_URL')
 if u:
  try:requests.get(u,timeout=10)
  except Exception as e:print("WATCHDOG PING ERR:",e)
def _alerts():
 a=load(AL,[])
 return a if isinstance(a,list) else []
def cmd_alert(txt):
 p=txt.split();cl=lambda x:x.upper().replace("-USDT","").replace("/USDT","")
 if len(p)==3 and p[1].lower() in ("del","delete","remove"):
  c=cl(p[2]);a=_alerts();n=[x for x in a if x.get('coin')!=c];save(AL,n);tg(f"🗑 Removed {len(a)-len(n)} alert(s) for {c}.");return
 if len(p)!=3:tg("🔔 Usage: `/alert COIN PRICE`\n`/alert del COIN`\n`/alerts`");return
 c=cl(p[1])
 try:target=float(p[2])
 except ValueError:tg("❌ Invalid price.");return
 if target<=0:tg("❌ Price must be positive.");return
 price=live(f"{c}-USDT")
 if not price:tg(f"❌ No price for {c}.");return
 if target==price:tg(f"ℹ️ {c} is already at {fp(price)}.");return
 d="above" if target>price else "below";a=_alerts();a.append({"coin":c,"price":target,"dir":d,"created":time.time()});save(AL,a)
 tg(f"🔔 *ALERT SET:* {c} {d} {fp(target)} (now {fp(price)}).")
def cmd_cancel_alerts():
 a=_alerts();save(AL,[]);tg(f"🗑 Cancelled all price alerts ({len(a)})." if a else "🔔 No price alerts to cancel.")
def cmd_alerts():
 a=_alerts()
 if not a:tg("🔔 No price alerts set.\nUse `/alert COIN PRICE`.");return
 _send("🔔 *PRICE ALERTS*\n"+"\n".join(f"{x.get('coin')} {x.get('dir')} {fp(x.get('price'))}" for x in a)+"\n\nCancel all: /cancel\\_alerts","Markdown",{"inline_keyboard":[[{"text":"🗑 Cancel all alerts","callback_data":"/cancel_alerts"}]]})
def check_alerts():
 a=_alerts()
 if not a:return
 keep=[];hit=False
 for x in a:
  p=live(f"{x.get('coin')}-USDT")
  if p and ((x.get('dir')=="above" and p>=x['price']) or (x.get('dir')=="below" and p<=x['price'])):
   tg(f"🔔 *ALERT:* {x['coin']} is {fp(p)} ({x['dir']} your {fp(x['price'])} level).");hit=True
  else:keep.append(x)
 if hit:save(AL,keep)
def _rsi(c,n=14):
 if len(c)<n+1:return None
 d=[c[i]-c[i-1] for i in range(1,len(c))];ag=sum(max(0,x) for x in d[:n])/n;al=sum(max(0,-x) for x in d[:n])/n
 for x in d[n:]:ag=(ag*(n-1)+max(0,x))/n;al=(al*(n-1)+max(0,-x))/n
 return 50.0 if al==0 and ag==0 else 100.0 if al==0 else round(100-100/(1+ag/al),2)
def _calc(sym):
 try:
  o=ex.fetch_ohlcv(S(sym),'1h',limit=100)
  if len(o)<50:return (None,)*5
  c=[float(k[4]) for k in o[:-1]]
  return float(o[-1][4]),c[-1],_rsi(c),round(sum(c[-5:])/5,6),round(sum(c[-10:])/10,6)
 except:return (None,)*5
_PF={}
def data(sym):
 h=_C.get(sym)
 if h and time.time()-h[0]<20:return h[1]
 r=_calc(sym);_C[sym]=(time.time(),r);return r
def breakout_confirmation(sym):
 try:
  candles=ex.fetch_ohlcv(S(sym),'1h',limit=25)
  if len(candles)<23:return False,None,None
  closed=candles[:-1];latest=closed[-1];prior=closed[-21:-1]
  if len(prior)!=20:return False,None,None
  resistance=max(float(c[2]) for c in prior)
  close=float(latest[4]);last_volume=float(latest[5] or 0);avg_volume=sum(float(c[5] or 0) for c in prior)/20
  multiple=(last_volume/avg_volume) if avg_volume>0 else 0.0
  return close>resistance and multiple>=1.5,multiple,resistance
 except:return False,None,None
def live(sym):
 try:return ex.fetch_ticker(S(sym))['last']
 except:return None
def open_sells(sym):
 res=[];ok=True
 for t in (False,True):
  try:res+=[(o,t) for o in ex.fetch_open_orders(S(sym),params=TR(t)) if o.get('side')=='sell']
  except:ok=False
 return res,ok
def cancel_sells(sym):
 orders,ok=open_sells(sym)
 if not ok:return 1
 failed=0
 for o,t in orders:
  try:ex.cancel_order(o['id'],S(sym),TR(t))
  except:failed+=1
 time.sleep(0.3);remaining,verified=open_sells(sym)
 if not verified or remaining:failed=max(1,failed)
 return failed
def free_qty(sym,d):
 try:b=B('free',sym.split('-')[0]);return min(d,b) if b>0 else d
 except:return d
def m_buy(cs,usdt):
 sym=cs.replace("/","-");p=live(sym)
 if not p:raise Exception(f"No price for {cs}")
 try:o=ex.create_order(cs,'market','buy',usdt,None,{'createMarketBuyOrderRequiresPrice':False})
 except Exception as e1:
  try:o=ex.create_order(cs,'market','buy',float(ex.amount_to_precision(cs,usdt/p)),p)
  except Exception as e2:raise Exception(f"{e1} | {e2}")
 oid=o.get('id')
 if oid:
  for _ in range(8):
   f=o.get('filled') or 0
   if f>0 and (o.get('average') or o.get('price')):break
   time.sleep(1)
   try:o=ex.fetch_order(oid,cs)
   except:continue
   if o.get('status') in ('canceled','rejected','expired') and not (o.get('filled') or 0):raise Exception(f"Buy {o.get('status')}; no fill")
 f=float(o.get('filled') or 0);a=float(o.get('average') or o.get('price') or 0)
 if f<=0 or a<=0:raise Exception("Exchange did not confirm fill.")
 return f,a
def hard_sl(sym,qty,pr):
 try:
  cs=S(sym)
  if cancel_sells(sym):return False
  time.sleep(0.5);qty=float(ex.amount_to_precision(cs,qty));pr=float(ex.price_to_precision(cs,pr))
  if qty<=0 or qty*pr<1.0:return False
  created=ex.create_order(cs,'market','sell',qty,None,{'triggerPrice':pr})
  oid=(created or {}).get('id');time.sleep(0.5);orders,ok=open_sells(sym)
  if ok and any((o.get('id')==oid if oid else False) for o,_ in orders):return True
  return False
 except Exception as e:tg(f"⚠️ *SL NOT VERIFIED* {sym}: `{e}`");return False
def place_trailing_sl(sym,qty):
 pos=load(DB).get(sym,{});ent=pos.get('entry_price') or live(sym)
 if not ent:return False
 px=round(ent*(1-sl_pct(sym,pos)/100),6);ok=hard_sl(sym,qty,px)
 if ok:
  db=load(DB)
  if sym in db:db[sym]['xsl']=px;save(DB,db)
 return ok
def ratchet(sym,d,lvl,lv):
 if lvl<=d.get('xsl',0)*1.01 or lvl>=lv*0.995:return
 q=B('total',sym.split('-')[0])
 if hard_sl(sym,q,lvl):d['xsl']=lvl
 elif place_trailing_sl(sym,q):d['xsl']=round(d['entry_price']*(1-sl_pct(sym,d)/100),6)
def rec(sym,pnl,why):
 L=load(LF);L['total_realized_usdt']=L.get('total_realized_usdt',0.0)+pnl;L['trade_count']=L.get('trade_count',0)+1;save(LF,L)
 H=load(HIST)
 if not isinstance(H,list):H=[]
 strat=load(DB).get(sym,{}).get('strategy','OTHER')
 H.append({"sym":sym,"pnl":pnl,"time":time.time(),"reason":why,"strategy":strat});save(HIST,H)
 if pnl<0 and not str(why).startswith(("MANUAL","PANIC")):mk_cd(f"stopout_{sym}")
def cleanup_spot_dust_after_sale(sym):
 coin=sym.split("-")[0].upper()
 conversion_attempted=False
 try:
  if coin in STABLE or coin=="BGB":
   return f"🧹 BGB dust conversion skipped for {coin} (not a convertible source asset)."
  time.sleep(0.8)
  bal=ex.fetch_balance()
  totals=bal.get("total") or {};free_bal=bal.get("free") or {}
  total=float(totals.get(coin) or 0.0)
  free=float(free_bal.get(coin) or 0.0)
  if total<=0:
   return f"🧹 Dust check: {coin} balance is zero."
  price=live(sym)
  if not price or price<=0:
   return f"🧹 Dust retained: {total:.8g} {coin}; live USDT price unavailable."
  value=total*price
  if value>=10.0:
   return f"ℹ️ Residual retained: {total:.8g} {coin} (≈${value:.4f}); Bitget BGB conversion is only for balances below 10 USDT."
  if free<=0:
   return f"🧹 Dust retained: {total:.8g} {coin} (≈${value:.4f}); no free spot balance."
  get_list=getattr(ex,"privateConvertGetV2ConvertBgbConvertCoinList",None)
  convert=getattr(ex,"privateConvertPostV2ConvertBgbConvert",None)
  if not callable(get_list) or not callable(convert):
   return (f"⚠️ {coin} dust retained (≈${value:.4f}). Your installed CCXT version does not expose "
                    "Bitget's BGB-convert endpoints; update CCXT before enabling conversion.")
  listed=get_list()
  if str((listed or {}).get("code"))!="00000":
   return f"⚠️ Bitget eligibility check failed for {coin}; dust left untouched: {(listed or {}).get('msg','unknown API response')}"
  data=(listed or {}).get("data") or {}
  items=data.get("coinList") or [] if isinstance(data,dict) else []
  item=next((x for x in items if str(x.get("coin","")).upper()==coin),None)
  if not item:
   return f"🧹 {coin} dust retained (≈${value:.4f}); Bitget does not list it as eligible for BGB conversion."
  eligible=float(item.get("available") or 0.0)
  if eligible<=0:
   return f"🧹 {coin} dust retained; Bitget reports no available amount to convert."
  eligible_value=eligible*price
  if eligible_value>=10.0:
   return f"🧹 {coin} dust retained (≈${eligible_value:.4f}); Bitget's eligible amount is not below 10 USDT."
  if eligible>free*1.000001:
   return f"⚠️ {coin} dust retained; Bitget's eligible amount ({eligible:.8g}) does not match the current free balance ({free:.8g})."
  conversion_attempted=True
  result=convert({"coinList":[coin]})
  if str((result or {}).get("code"))!="00000":
   return f"⚠️ Bitget rejected {coin}→BGB conversion; dust left untouched: {(result or {}).get('msg','unknown API response')}"
  rdata=(result or {}).get("data") or {}
  orders=rdata.get("orderList") or [] if isinstance(rdata,dict) else []
  order=next((x for x in orders if str(x.get("coin","")).upper()==coin),None)
  if not order:
   return f"⚠️ Bitget accepted the conversion request but returned no order for {coin}; check Bitget conversion history."
  oid=str(order.get("orderId") or "")
  history=getattr(ex,"privateConvertGetV2ConvertBgbConvertRecords",None)
  if callable(history) and oid:
   for _ in range(4):
    try:
     hr=history({"orderId":oid})
     hd=(hr or {}).get("data") or []
     records=hd if isinstance(hd,list) else [hd]
     recd=next((x for x in records if str(x.get("orderId",oid))==oid),None)
     if recd:
      status=str(recd.get("status","")).lower()
      if status in ("success","successful","succeed"):
       from_amt=recd.get("fromAmount",eligible)
       to_amt=recd.get("toAmount")
       fee_detail=recd.get("feeDetail") or []
       fee_txt=""
       if fee_detail:
        fee_txt=f" | Fee: {fee_detail[0].get('fee','?')} {fee_detail[0].get('feeCoin','BGB')}"
       return f"✅ 🧹 BGB dust converted: {from_amt} {coin} → {to_amt or 'BGB amount in history'} BGB{fee_txt}. Order: {oid}."
      if status in ("failed","fail","rejected","cancelled","canceled"):
       return f"⚠️ Bitget conversion {status} for {coin}; check order {oid}."
    except Exception as e:
     print(f"BGB CONVERT HISTORY CHECK {coin}: {e}")
    time.sleep(0.8)
  return f"🟡 Bitget accepted {coin}→BGB dust conversion. Order: {oid or 'not supplied'}. Final status can be checked in Bitget conversion history."
 except Exception as e:
  if conversion_attempted:
   return f"⚠️ BGB conversion outcome is UNKNOWN for {coin}; do not retry until Bitget conversion history and balances are checked. Reason: {str(e)[:180]}"
  return f"⚠️ BGB dust conversion skipped for {coin}; balance left untouched. Reason: {str(e)[:220]}"
def sell(sym,pct,reason):
 try:
  cs=S(sym);coin=sym.split("-")[0]
  if cancel_sells(sym):tg(f"⚠️ Sale of {sym} stopped: orders could not be safely cancelled.");return False
  time.sleep(0.5);fb=B('free',coin)
  if fb<=0:tg(f"ℹ️ {sym} has 0 free balance ({reason}).");return False
  p=live(sym)
  if not p:return False
  q=fb*pct
  if q*p<1.02 and pct<1.0:q=fb;reason+=" (dust→full)"
  q=float(ex.amount_to_precision(cs,q))
  if q<=0:return True
  o=ex.create_market_sell_order(cs,q);oid=(o or {}).get('id');filled=float((o or {}).get('filled') or 0);px=float((o or {}).get('average') or (o or {}).get('price') or p)
  if oid and (filled<=0 or not px):
   for _ in range(8):
    time.sleep(0.8)
    try:o=ex.fetch_order(oid,cs);filled=float(o.get('filled') or 0);px=float(o.get('average') or o.get('price') or p)
    except Exception:continue
    if filled>0:break
  if filled<=0:tg(f"⚠️ *SELL NOT VERIFIED* {sym}: order `{oid}` returned no confirmed fill. Reconcile exchange balance before retrying.");return False
  db=load(DB)
  if sym in db:
   pnl=(px-db[sym]["entry_price"])*filled;rec(sym,pnl,reason)
   tg(f"✅ *SOLD ({reason})*\n🪙 {filled:.8g} {sym}\n{'🟢' if pnl>=0 else '🔴'} PnL: ${pnl:.2f}")
  else:tg(f"✅ *SOLD ({reason})*\n🪙 {filled:.8g} {sym}\nCost basis not tracked; realized PnL not calculated.")
  if pct>=0.999:
   dust_result=cleanup_spot_dust_after_sale(sym)
   tg(dust_result)
  return True
 except Exception as e:tg(f"❌ *SELL FAILED* {sym}:`{e}`");return False
def parse(txt,usage,allow_all=False):
 p=txt.split()
 if len(p)!=3:tg(usage);return None
 c=p[1].upper().replace("-USDT","").replace("/USDT","");a=p[2].upper()
 if allow_all and a=="ALL":return c,None
 try:a=float(a)
 except:tg("❌ Invalid amount.");return None
 if a<=0:tg("❌ Amount must be positive.");return None
 return c,a
def cmd_buy(txt):
 r=parse(txt,"❌ Usage: `/buy COIN USDT_AMOUNT` or `/buy COIN ALL`",True)
 if not r:return
 c,amt=r;sym=f"{c}-USDT"
 if cd(f"buy:{sym}",5):tg(f"⏳ Processing /buy {c}...");return
 db=load(DB);old=db.get(sym);ub=B('free','USDT')
 if amt is None:
  fee_reserve=0.02
  spendable=max(0.0,ub-fee_reserve)
  amt=int(spendable*100)/100
  if amt<1.0:
   tg(f"❌ *BUY FAILED:* Only ${ub:.2f} free USDT. `/buy COIN ALL` keeps ${fee_reserve:.2f} aside; at least $1.02 free USDT is needed for a $1.00 order.");return
 if ub<amt+(0.02 if txt.strip().upper().endswith(' ALL') else 0.0):
  tg(f"❌ *BUY FAILED:* You have ${ub:.2f} free USDT; the order plus the $0.02 fee reserve would exceed it.");return
 try:
  ob=B('total',c) if old else 0.0;f,a=m_buy(f"{c}/USDT",amt);sl=sl_pct(sym,old) if old else sl_pct(sym)
  if old:
   e0=old['entry_price'];avg=round((ob*e0+f*a)/(ob+f),6) if ob>0 else round(a,6)
   old.update(entry_price=avg,safe_zone=avg,stop_loss=round(avg*(1-sl/100),6),last_price=a,peak_price=max(old.get('peak_price',avg),a));db[sym]=old;save(DB,db);ok=place_trailing_sl(sym,free_qty(sym,ob+f))
   tg(f"➕ *DCA ADDED:* {c} @ {a:.6f}\nSpent: ${amt:.2f}\nNew avg entry: {avg:.6f}\n"+(STOP_OK if ok else STOP_NO))
  else:
   db[sym]=NP(round(a,6),round(a*(1-sl/100),6),sl);db[sym]['strategy']="MANUAL";save(DB,db);ok=place_trailing_sl(sym,free_qty(sym,f))
   tg(f"✅ *BOUGHT:* {c} @ {a:.6f}\nSpent: ${amt:.2f}\n"+(STOP_OK if ok else STOP_NO))
 except Exception as e:tg(f"❌ *BUY FAILED:* {e}")
def cmd_sell(txt):
 r=parse(txt,"❌ Usage: `/sell COIN USDT_AMOUNT` (or `ALL`)",True)
 if not r:return
 c,amt=r;sym=f"{c}-USDT";cs=f"{c}/USDT"
 if cd(f"sell:{sym}",5):tg(f"⏳ Processing /sell {c}...");return
 db=load(DB)
 if sym not in db:tg(f"❌ No active bot trade for {c}. Type `/sync` if manual.");return
 p=live(sym)
 if not p:tg(f"❌ No price for {c}.");return
 try:
  if cancel_sells(sym):tg(f"⚠️ Sale of {sym} stopped: orders could not be safely cancelled.");return
  time.sleep(0.5);fb=B('free',c)
  if fb<=0:tg(f"❌ You have 0 {c} available.");return
  q,a_s=(fb,"ALL") if amt is None or amt/p>=fb else (amt/p,f"${amt:.2f}")
  q=float(ex.amount_to_precision(cs,q))
  if q<=0:tg("❌ Amount too small.");return
  full_exit=q>=fb*0.999
  o=ex.create_market_sell_order(cs,q);oid=(o or {}).get('id')
  filled=float((o or {}).get('filled') or 0.0);px=float((o or {}).get('average') or (o or {}).get('price') or p)
  if oid and (filled<=0 or not px):
   for _ in range(8):
    time.sleep(0.8)
    try:
     o=ex.fetch_order(oid,cs);filled=float(o.get('filled') or 0.0);px=float(o.get('average') or o.get('price') or p)
    except Exception:continue
    if filled>0 and px>0:break
  if filled<=0 or px<=0:
   tg(f"⚠️ *SELL NOT VERIFIED* {sym}: order `{oid}` has no confirmed fill. Reconcile the Bitget balance before retrying.");return
  pnl=(px-db[sym]["entry_price"])*filled;rec(sym,pnl,f"MANUAL SELL {a_s}")
  time.sleep(1);rem=B('total',c)
  fully_filled=full_exit and filled>=q*0.999
  if rem<=0 or rem*p<1.02:
   db.pop(sym,None);save(DB,db)
   tg(f"✅ *SOLD:* {filled:.8g} {c} @ {px:.6f}\nPnL: ${pnl:.2f}\nPosition closed.")
  else:
   place_trailing_sl(sym,rem);save(DB,db)
   tg(f"✅ *SOLD:* {filled:.8g} {c} @ {px:.6f}\nPnL: ${pnl:.2f}\nRemaining: {rem:.8g} {c} (stop re-armed)")
  if fully_filled:
   tg(cleanup_spot_dust_after_sale(sym))
 except Exception as e:tg(f"❌ *SELL FAILED:* {e}")
def cmd_cancel_all():
 tg("🔄 Cancelling all open orders on Bitget...")
 try:
  count=0
  for t in (False, True):
   try:
    for o in ex.fetch_open_orders(params=TR(t)):
     try:ex.cancel_order(o['id'], o['symbol'], TR(t));count += 1
     except:pass
   except:pass
  save(QF, {})
  tg(f"✅ *CANCELLED:* {count} open orders on Bitget.\nRun `/audit` to re-arm stops.")
 except Exception as e:tg(f"❌ Failed to cancel: {e}")
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
 tg(f"✅ *SYNCED:* Added {len(added)} coins:\n`{chr(10).join(added)}`" if added else "✅ No new manual coins found to sync.")
def panic_sell():
 db=load(DB)
 if not db:
  try:u=B('free','USDT')
  except Exception:u=0
  tg(f"🚨 *PANIC:* No open positions. Balance: ${u:.2f} USDT.");return
 tg("🚨 *PANIC COMMAND INITIATED!* Selling all positions...");failed=[]
 for sym in list(db):
  if sell(sym,1.0,"PANIC"):db.pop(sym,None)
  else:failed.append(sym)
 save(DB,db);save(QF,{});s=cfg();s['auto_buy_enabled']=False;save(CFG,s)
 tg(f"⚠️ *PANIC INCOMPLETE.* Still held: `{', '.join(failed)}`\nAuto-buy PAUSED." if failed else "🛑 *PANIC COMPLETE.* All positions sold. Auto-buy PAUSED.")
def get_bitget_ath_atl(sym):
 cache=load(ATHATL_CACHE,{})
 now=time.time()
 item=cache.get(sym,{}) if isinstance(cache,dict) else {}
 if item and now-float(item.get("checked_at",0) or 0)<86400:
  return item.get("ath"),item.get("atl"),item.get("status","cached")
 try:
  markets=ex.load_markets()
  market=markets.get(S(sym)) or markets.get(sym)
  if not market or not market.get("spot",False):
   return None,None,"not a Bitget spot market"
  info=market.get("info") or {}
  start_ms=0
  for key in ("listTime","listingTime","onlineTime","openTime","ctime"):
   value=info.get(key)
   try:
    n=int(float(value))
    if n>0:
     start_ms=n if n>100000000000 else n*1000
     break
   except (TypeError,ValueError):
    continue
  if not start_ms:start_ms=1514764800000
  end_ms=int(now*1000)
  window_ms=89*24*60*60*1000
  ath=None;atl=None;cursor=start_ms;calls=0
  api_symbol=str(market.get("id") or sym.replace("/",""))
  while cursor<end_ms and calls<100:
   window_end=min(cursor+window_ms,end_ms)
   response=requests.get("https://api.bitget.com/api/v2/spot/market/history-candles",params={
                "symbol":api_symbol,"granularity":"1week","startTime":str(cursor),
                "endTime":str(window_end),"limit":"200"
            },timeout=12)
   response.raise_for_status();payload=response.json()
   if payload.get("code")!="00000":
    raise RuntimeError(str(payload.get("msg") or payload.get("code") or "Bitget candle API error"))
   rows=payload.get("data") or []
   for row in rows:
    try:
     high=float(row[2]);low=float(row[3])
     if high>0:ath=high if ath is None else max(ath,high)
     if low>0:atl=low if atl is None else min(atl,low)
    except (TypeError,ValueError,IndexError):continue
   cursor=window_end+1;calls+=1
   if calls%4==0:time.sleep(0.12)
  if calls>=100 and cursor<end_ms:
   raise RuntimeError("historical scan limit reached; ATH/ATL not certified")
  if ath is None or atl is None:
   return None,None,"no historical candles returned"
  cache[sym]={"ath":ath,"atl":atl,"checked_at":now,"status":"complete"}
  save(ATHATL_CACHE,cache)
  return ath,atl,"complete"
 except Exception as e:
  print(f"ATH/ATL ERR {sym}: {e}")
  if item and item.get("ath") and item.get("atl"):
   return item.get("ath"),item.get("atl"),"cached; refresh failed"
  return None,None,"unavailable"
def get_holdings_text():
 try:balance=ex.fetch_balance();totals=balance.get('total') or {};free=balance.get('free') or {}
 except Exception as e:return f"❌ Could not fetch exchange holdings: {e}"
 usdt_total=float(totals.get('USDT',0.0));usdt_free=float(free.get('USDT',0.0));total_coins_value=0.0;count=0;unpriced=[];coin_lines=[]
 for coin,qty in sorted(totals.items()):
  try: qty=float(qty or 0)
  except (TypeError,ValueError): continue
  if qty<=0 or coin in STABLE or coin in BL: continue
  sym=f"{coin}-USDT";price=live(sym)
  if not price:unpriced.append(f"{coin}: {qty:.8g}")
  if not price or qty*price<0.10: continue
  total_coins_value += qty*price
  db=load(DB);entry_data=db.get(sym,{});entry=entry_data.get('entry_price')
  _,_,rsi,ma5,ma10=data(sym)
  sentiment="⚪ Unavailable"
  if rsi is not None and ma5 is not None and ma10 is not None:
   if price>ma5>ma10 and rsi>=50:sentiment="🟢 Bullish"
   elif price<ma5<ma10 and rsi<50:sentiment="🔴 Bearish"
   else:sentiment="🟡 Neutral / Mixed"
  pnl_line="Entry / PnL: Not tracked by bot"
  if entry:
   pnl=(price-entry)/entry*100;pnl_usdt=(price-entry)*qty
   pnl_line=f"Entry: {fp(entry)} USDT | PnL: {pnl:+.2f}% (${pnl_usdt:+.2f})"
  tp=cfg().get('trail_pct',0) or 0
  ath,atl,ath_status=get_bitget_ath_atl(sym)
  ath_line=f"ATH (Bitget lifetime): {fp(ath)} USDT" if ath else "ATH (Bitget lifetime): Unavailable"
  atl_line=f"ATL (Bitget lifetime): {fp(atl)} USDT" if atl else "ATL (Bitget lifetime): Unavailable"
  coin_lines.extend([f"🪙 *{coin}*",f"Quantity: {qty:.8g}",f"Current price: {fp(price)} USDT",f"Market value: ${qty*price:.2f}",ath_line,atl_line,pnl_line,f"RSI (1h): {rsi if rsi is not None else 'N/A'}",f"MA5 (1h): {fp(ma5) if ma5 is not None else 'N/A'}",f"MA10 (1h): {fp(ma10) if ma10 is not None else 'N/A'}",f"Market sentiment: {sentiment}",f"Bot trailing stop: {'ON ('+str(tp)+'% below peak)' if tp>0 else 'OFF'}",f"Peak price: {fp(entry_data.get('peak_price')) if entry_data.get('peak_price') else 'N/A'}",f"Exchange stop level: {fp(entry_data.get('xsl')) if entry_data.get('xsl') else 'Not verified'}"])
  coin_lines.append("");count+=1
 total_portfolio_value=wallet_value(totals)
 _BAL["t"],_BAL["m"]=time.time(),balance_text(total_portfolio_value)
 bgb_total=float(totals.get('BGB',0.0) or 0.0)
 bgb_free=float(free.get('BGB',0.0) or 0.0)
 bgb_price=live('BGB-USDT') if bgb_total>0 else None
 bgb_value=bgb_total*bgb_price if bgb_price else None
 bgb_report=(f"🟡 *BGB Wallet Balance (not auto-converted)*\nTotal: {bgb_total:.8g} BGB | Free: {bgb_free:.8g} BGB"
                +(f" | Value: ${bgb_value:.4f} USDT" if bgb_value is not None else " | USDT value: unavailable"))
 _r=ngn_rate();lines=[f"💼 *Total Portfolio Value:* ${total_portfolio_value:.2f} USDT"+(f" | ₦{total_portfolio_value*_r:,.0f}" if _r else ""),"",f"💵 *Available USDT Cash*",f"Total: ${usdt_total:.2f} | Free to Trade: ${usdt_free:.2f}",bgb_report,"━━━━━━━━━━━━━━━━━━━━"]
 if unpriced:
  note="\n⚠️ Held but with no USDT price: "+", ".join(unpriced[:3])
  if len(unpriced)>3: note+=f" and {len(unpriced)-3} more..."
  coin_lines.append(note)
 footer=f"\n\n📊 *LIVE SPOT WALLET BALANCE:* {compact_balance()}"
 if count==0:return "\n".join(lines) + "\n\n💼 *Coin Holdings*\nNo eligible USDT-priced assets."+footer
 lines.append(f"\n💼 *Coin Holdings (Value: ${total_coins_value:.2f})*\n")
 return "\n".join(lines) + "\n".join(coin_lines)+footer
def check_hourly_balance():
 st=load(SC);now=time.time()
 if now-st.get('last_bal_alert',0)>=3600:tg(f"💼 *HOURLY HOLDINGS*\n{get_holdings_text()}");st['last_bal_alert']=now;save(SC,st)
def last_trade_line():
 H=load(HIST)
 if not H:return "\nNo closed trade recorded."
 t=H[-1];ago=(time.time()-float(t.get('time',0)))/3600
 return f"\nLast: {str(t.get('sym','?')).replace('-USDT','')} — {t.get('reason','?')} | PnL ${float(t.get('pnl',0)):+.2f} | {ago:.1f}h ago"
def audit_system():
 issues=[];db=load(DB);q=load(QF)
 try:bal=ex.fetch_balance()['total']
 except Exception as e:tg(f"⚠️ *AUDIT:* Failed to fetch data: `{e}`");return "⚠️ Audit failed."
 for sym in list(db):
  amt=bal.get(sym.split('-')[0],0);px=live(sym)
  if amt<=0 or (px and amt*px<1.02):db.pop(sym,None);issues.append(f"🧹 Removed ghost: {sym}");continue
  orders,ok=open_sells(sym)
  if not ok:continue
  if len(orders)!=1 and not in_cd(f"auditsl_{sym}",1800):
   mk_cd(f"auditsl_{sym}");issues.append(f"🚨 {sym}: {len(orders)} sell orders. Re-arming...");save(DB,db);place_trailing_sl(sym,amt)
 for c,a in bal.items():
  if c in STABLE or a<=0:continue
  sym=f"{c}-USDT"
  if sym not in db:
   p=live(sym)
   if p and a*p>1.02:
    db[sym]=NP(p,round(p*(1-cfg()['stop_loss_pct']/100),6));save(DB,db);ok=place_trailing_sl(sym,a);issues.append(f"🔄 Auto-synced: {sym}")
 qc=False
 for sym in list(q):
  if sym in db or time.time()-q[sym].get('timestamp',0)>86400:q.pop(sym,None);qc=True
 if issues or qc:
  save(DB,db)
  if qc:save(QF,q)
  if issues:m="🛠️ *AUTO-AUDIT COMPLETE*\n"+"\n".join(issues);tg(m);return m
 return "✅ *AUDIT COMPLETE:* No issues found."
def rank_setups(exclude=()):
 s=cfg()
 try:tk=ex.fetch_tickers();mk=ex.load_markets()
 except Exception as e:print(f"RANK ERR: {e}");return None
 v=[{'sym':k.replace("/","-"),'vol':t['quoteVolume'] or 0,'chg':t['percentage'] or 0} for k,t in tk.items() if k.endswith('/USDT') and not skip(k.split('/')[0]) and not (k in mk and not mk[k].get('spot',False))]
 v.sort(key=lambda x:x['vol'],reverse=True);cands=[]
 for rank,t in enumerate(v[:500],start=1):
  if rank<=15 or t['sym'] in exclude:continue
  i=rank-16;thr=(1000000 if i<=84 else 3000000 if i<=183 else 4000000 if i<=383 else 5000000)
  if t['vol']<thr:continue
  if s.get('scan_prefilter',True) and not (-30.0<float(t['chg'] or 0)<-8.0 or 0.0<float(t['chg'] or 0)<30.0):continue
  sym=t['sym'];chg=float(t['chg'] or 0);p,pr,r,ma5,ma10=data(sym)
  if not p or r is None or ma5 is None or ma10 is None:continue
  setup=None;bv=None
  if 1.0<chg<30.0 and s['rsi_min']<=r<=s['rsi_max'] and p>ma5>ma10:setup="TREND"
  elif -30.0<chg<-8.0 and r<30 and p>pr:setup="OVERSOLD"
  elif 0.0<chg<25.0 and 50.0<=r<=70.0 and p>ma5>ma10:
   ok,bv,bl=breakout_confirmation(sym)
   if ok:setup="BREAKOUT"
  why=[];pts=0
  if setup:
   pts+={"BREAKOUT":30,"TREND":20,"OVERSOLD":10}[setup]
   why.append({"BREAKOUT":f"Confirmed breakout: price cleared the recent 20h high on {bv or 0:.2f}x average volume","TREND":f"Trend setup: price {fp(p)} is above MA5 {fp(ma5)} which is above MA10 {fp(ma10)}, with RSI {r:.1f} inside your {s['rsi_min']}-{s['rsi_max']} entry zone","OVERSOLD":f"Oversold bounce: RSI {r:.1f} is below 30 and price is back above the previous close"}[setup])
  elif p>ma5>ma10:why.append("Short-term trend is up (price > MA5 > MA10)")
  else:why.append(f"Trend not confirmed yet (price {fp(p)}, MA5 {fp(ma5)}, MA10 {fp(ma10)})")
  if p>ma5>ma10:pts+=5
  if 45<=r<=65:pts+=3;why.append(f"RSI {r:.1f} is healthy (room to run before overbought)")
  elif r>=75:pts-=5;why.append(f"RSI {r:.1f} is overbought (risk of pullback)")
  if 1<chg<15:pts+=2;why.append(f"24h change {chg:+.2f}% shows steady, not extreme, momentum")
  pts+=min(t['vol']/1e8,3);why.append(f"24h volume ${t['vol']/1e6:.1f}M (liquid enough: above the ${thr/1e6:.0f}M minimum)")
  tier=setup or ("WATCHLIST (no full scanner setup)" if p>ma5>ma10 and r<75 else "WEAK (best available, not a full setup)")
  cands.append({"sym":sym,"setup":tier,"full":bool(setup),"pts":pts+(0 if setup or p>ma5>ma10 else -10),"why":why,"p":p,"rsi":r,"chg":chg})
 cands.sort(key=lambda x:x["pts"],reverse=True);return cands
def analyse_top3():
 tg("🏆 Scanning the market for the 3 best setups... this can take a minute.")
 s=cfg();cands=rank_setups()
 if cands is None:tg("⚠️ Could not load Bitget data.");return
 top=cands[:3]
 if not top:tg("🏆 *TOP 3 SETUPS*\nCould not read market data for any coin right now. Try again shortly.");return
 sl=s['stop_loss_pct'];out=["🏆 *TOP 3 SETUPS RIGHT NOW*","_Read-only scan. Nothing was bought._",""]
 try:
  mf=market_filter(s)
  if mf:out+=[f"⚠️ Market filter active: {mf}. Auto-buy is blocked; a manual buy is your own call.",""]
 except Exception:pass
 lim=float(s.get('max_spread_pct',0.5));rbh=float(s.get('rebuy_cooldown_hours',6.0))
 for n,c in enumerate(top,1):
  lab=c["setup"]
  out+=[f"*{n}. {c['sym']}* — {lab}",f"Price: {fp(c['p'])} USDT | 24h: {c['chg']:+.2f}% | RSI: {c['rsi']:.1f}",f"Suggested stop: {fp(c['p']*(1-sl/100))} ({sl}% below price)","Why:"]+["• "+w for w in c["why"]]
  sp=spread_pct(c['sym'])
  out.append("• Spread: "+(f"{sp:.2f}% ("+("OK for auto-buy" if sp<=lim else f"wider than your {lim:g}% limit, auto-buy would skip it")+")" if sp is not None else "unavailable"))
  if rbh>0 and in_cd(f"stopout_{c['sym']}",rbh*3600):out.append(f"• Re-buy cooldown active after a recent loss on this coin ({rbh:g}h)")
  out.append("")
 if not any(c["full"] for c in top):out.append("No coin passes all scanner rules right now, so these are the strongest available. Be cautious.\n")
 out.append("Buy manually with `/buy COIN amount` if you agree.")
 tg("\n".join(out))
def hold_advice(sym,price,entry,rsi,ma5,ma10,s):
 pnl=(price-entry)/entry*100 if entry else None;sl=float(s.get('stop_loss_pct',5));rx=float(s.get('rsi_exit',75) or 0)
 down=rsi is not None and ma5 is not None and ma10 is not None and price<ma5<ma10 and rsi<50
 up=ma5 is not None and ma10 is not None and price>ma5>ma10
 if rsi is not None and rx and rsi>=rx:return ("💰 TAKE PROFIT" if pnl is None or pnl>0 else "🟠 OVERBOUGHT - consider selling"),"RSI "+f"{rsi:.0f}"+" is overbought; a pullback is likely"
 if pnl is not None and pnl<=-sl:return "🔴 SELL (cut loss)",f"Down {pnl:.1f}%, past your {sl}% stop-loss level"
 if down and pnl is not None and pnl<0:return "🔴 SELL (cut loss)",f"Losing {pnl:.1f}% and the trend is down (price < MA5 < MA10, RSI {rsi:.0f}); loss is likely to grow"
 if down:return "🟠 WEAK - consider selling","Trend is down (price < MA5 < MA10)"+(f", RSI {rsi:.0f}" if rsi is not None else "")
 if up:return "🟢 HOLD","Trend is up (price > MA5 > MA10)"+(f", RSI {rsi:.0f}" if rsi is not None else "")
 return "🟡 HOLD / WATCH","Trend is mixed; keep the stop-loss active"
def long_term_view(sym):
 try:
  c=ex.fetch_ohlcv(S(sym),'1d',limit=100);cl=[float(x[4]) for x in c]
  if len(cl)<50:return "⚪ Long-term: not enough history (new coin, high risk)"
  ma50=sum(cl[-50:])/50;ch=(cl[-1]/cl[0]-1)*100;vol=float(ex.fetch_ticker(S(sym)).get('quoteVolume') or 0)
  if vol>=20e6 and cl[-1]>=ma50 and ch>-20:return f"✅ Long-term: reasonable to hold (above 50-day average, {len(cl)}-day change {ch:+.0f}%, liquid)"
  if cl[-1]<ma50 and (ch<-30 or vol<2e6):return f"❌ Long-term: not suited (below 50-day average, {len(cl)}-day change {ch:+.0f}%"+(", thin volume)" if vol<2e6 else ")")
  return f"⚠️ Long-term: speculative ({len(cl)}-day change {ch:+.0f}%, {'above' if cl[-1]>=ma50 else 'below'} 50-day average)"
 except Exception as e:
  print(f"LONG TERM ERR {sym}: {e}");return "⚪ Long-term: unavailable"
def cmd_status():
 s=cfg();now=time.time();db=load(DB)
 try:bal=ex.fetch_balance();totals=bal.get('total') or {};free=bal.get('free') or {}
 except Exception as e:tg(f"❌ Could not reach Bitget: {e}");return
 usdt=float(totals.get('USDT',0) or 0);usdt_free=float(free.get('USDT',0) or 0)
 held=[];coin_val=0.0
 for coin,qty in sorted(totals.items()):
  try:qty=float(qty or 0)
  except (TypeError,ValueError):continue
  if qty<=0 or coin in STABLE or coin in BL:continue
  sym=f"{coin}-USDT";px=live(sym)
  if px and qty*px>=0.10:held.append((coin,sym,qty,px));coin_val+=qty*px
 total=wallet_value(totals);r=ngn_rate()
 ok,why=risk_controls();br=data("BTC-USDT")[2];bm=s.get('btc_rsi_min',0)
 btc="OK" if bm<=0 or (br is not None and br>=bm) else f"BLOCKING buys (BTC RSI {br} < {bm})"
 ls=load(SC).get('last',0);ago=f"{(now-ls)/60:.0f} min ago" if ls else "never"
 q=load(QF)
 try:_,ddp=day_drawdown()
 except Exception:ddp=0.0
 try:mf=market_filter(s) or "OK (no market-wide block)"
 except Exception:mf="unavailable"
 expo=coin_val/total*100 if total>0 else 0.0
 cdl=[k[8:].replace("-USDT","") for k,v in load(CD,{}).items() if k.startswith("stopout_") and now-v<float(s.get('rebuy_cooldown_hours',6.0))*3600]
 sysmsg=["🤖 *BOT*","Exchange connection: ✅ OK",f"Portfolio: ${total:.2f} USDT"+(f" | ₦{total*r:,.0f}" if r else ""),f"USDT cash: ${usdt:.2f} (free ${usdt_free:.2f})",f"Auto-buy: {'ON' if s.get('auto_buy_enabled') else 'PAUSED'} (${s.get('auto_buy_amount_usdt')} per trade)",f"BTC safety filter: {btc}",f"Market filter: {mf}",f"Risk controls: {'✅ trading allowed' if ok else '⛔ '+why}",f"Today vs start of day: {ddp:+.2f}% (auto-buy pauses at -{s.get('max_daily_drawdown_pct')}%)",f"Money in coins: {expo:.0f}% of portfolio (cap {s.get('max_portfolio_exposure_pct')}%)",f"Re-buy cooldown active: {', '.join(cdl) if cdl else 'none'}",f"Last market scan: {ago} (runs every {float(s.get('scan_interval_minutes',15)):g} min) | Queued coins: {len(q)}",f"Bot-tracked positions: {len(db)} | Holdings priced: {len(held)}",last_trade_line().strip(),"",
      "🧭 *STRATEGY*",f"Entries: TREND (RSI {s.get('rsi_min')}-{s.get('rsi_max')}, price > MA5 > MA10), OVERSOLD bounce (RSI < 30), BREAKOUT (RSI 50-70 + volume breakout)",f"Protection: stop-loss {s.get('stop_loss_pct')}% | trailing stop {s.get('trail_pct')}% below peak | crash guard {s.get('crash_guard_pct')}%",f"Exit: RSI >= {s.get('rsi_exit')} or max hold {s.get('max_hold_hours')}h",f"Profit taking: sell {float(s.get('partial_tp_fraction',0.5))*100:.0f}% at +{s.get('partial_tp_pct')}% then stop moves to break-even" if float(s.get('partial_tp_pct',0) or 0)>0 else "Profit taking: partial sell OFF",f"Dynamic trailing: {'ON (wider in strong trends, tighter when momentum fades)' if s.get('dynamic_trail',True) else 'OFF'}",f"Entry safety: spread limit {s.get('max_spread_pct')}% | stop fails -> {'sell at market' if s.get('sell_if_stop_fails',True) else 'alert only'} | scan pre-filter {'ON' if s.get('scan_prefilter',True) else 'OFF'}",(f"Near-instant mode: ON (stays awake {float(s.get('live_mode_seconds',270)):g}s per run)" if float(s.get('live_mode_seconds',270) or 0)>0 else "Near-instant mode: OFF"),f"Auto-clear chat: every {float(s.get('auto_clear_hours',24)):g}h" if float(s.get('auto_clear_hours',24) or 0)>0 else "Auto-clear chat: OFF",f"Limits: {s.get('max_daily_trades')} trades/day, {s.get('max_daily_loss_pct')}% daily loss, {s.get('max_position_pct')}% max per coin",f"Extra safeguards: 4h trend check {'ON' if s.get('mtf_confirm',True) else 'OFF'} | BTC shock pause {float(s.get('btc_shock_pct',3.0) or 0):g}%/h | combined open risk cap {float(s.get('max_open_risk_pct',3.0) or 0):g}% | smaller buys after {int(s.get('loss_streak_trigger',2) or 0)} losses in a row | parallel scan {'ON' if s.get('scan_parallel',True) else 'OFF'}"]
 tg("\n".join(sysmsg))
 if not held:tg("🛑 *HOLDINGS ADVICE*\nNo priced coin holdings right now.");return
 out=["💼 *HOLDINGS ADVICE*","_Technical view only, not financial advice._",""];sells=0
 for coin,sym,qty,px in held:
  d=db.get(sym,{});entry=d.get('entry_price');_,_,rsi,ma5,ma10=data(sym)
  v,why2=hold_advice(sym,px,entry,rsi,ma5,ma10,s)
  if any(k in v for k in ("SELL","TAKE","WEAK","OVERBOUGHT")):sells+=1
  pnl=f" | PnL {(px-entry)/entry*100:+.1f}% (${(px-entry)*qty:+.2f})" if entry else " | PnL not tracked"
  out+=[f"🪙 *{coin}* — ${qty*px:.2f}{pnl}",f"Advice: {v}",f"Why: {why2}",long_term_view(sym),""]
 tg("\n".join(out))
 if sells:
  cands=rank_setups(exclude={sym for _,sym,_,_ in held})
  if cands:
   alt=["🔁 *IF YOU SELL - TOP 3 ALTERNATIVES*",""]
   for n,c in enumerate(cands[:3],1):alt+=[f"*{n}. {c['sym']}* - {c['setup']}",f"Price {fp(c['p'])} | 24h {c['chg']:+.2f}% | RSI {c['rsi']:.1f}","Why: "+c['why'][0],""]
   alt.append("Full reasons: /analyse")
   tg("\n".join(alt))
 else:tg("✅ No sell advised right now. For possible new buys use /analyse.")
def analyse_coin(txt):
 parts=txt.strip().split()
 if len(parts)!=2:tg("📊 Usage: `/analyse COIN`");return
 coin=parts[1].upper().replace('/USDT','').replace('-USDT','').replace('USDT','').strip('-/')
 if not coin or not re.fullmatch(r'[A-Z0-9]+',coin):tg("❌ Invalid coin.");return
 sym=f"{coin}-USDT";cs=S(sym)
 try:
  markets=ex.load_markets()
  if cs not in markets or not markets[cs].get('spot',False):tg(f"❌ {sym} not a spot market.");return
  candles=ex.fetch_ohlcv(cs,'1h',limit=100)
  if len(candles)<60:tg(f"⚠️ Not enough history for {sym}.");return
  closed=candles[:-1];closes=[float(x[4]) for x in closed];highs=[float(x[2]) for x in closed];lows=[float(x[3]) for x in closed];vols=[float(x[5] or 0) for x in closed]
  price=float(ex.fetch_ticker(cs).get('last') or candles[-1][4]);ticker=ex.fetch_ticker(cs);rsi=_rsi(closes)
  def ema(v,p):
   if len(v)<p:return None
   a=2/(p+1);e=sum(v[:p])/p
   for z in v[p:]:e=a*z+(1-a)*e
   return e
  ma5=sum(closes[-5:])/5;ma10=sum(closes[-10:])/10;ma20=sum(closes[-20:])/20
  macd_series=[]
  for i in range(26,len(closes)+1):
   a=ema(closes[:i],12);b=ema(closes[:i],26)
   if a is not None and b is not None:macd_series.append(a-b)
  macd=macd_series[-1] if macd_series else None;signal=ema(macd_series,9) if len(macd_series)>=9 else None;hist=(macd-signal) if macd is not None and signal is not None else None
  trs=[max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1])) for i in range(1,len(closed))]
  atr=sum(trs[-14:])/14 if len(trs)>=14 else None;avgvol=sum(vols[-21:-1])/20 if len(vols)>=21 else None;lastvol=vols[-1] if vols else 0
  change=ticker.get('percentage');bid=ticker.get('bid');ask=ticker.get('ask');spread=((ask-bid)/((ask+bid)/2)*100) if bid and ask and ask>=bid else None
  checks=[price>ma5>ma10>ma20,rsi is not None and 50<=rsi<=70,macd is not None and signal is not None and macd>signal,avgvol is not None and lastvol>=avgvol];score=sum(bool(x) for x in checks)
  verdict='WAIT / WEAK SETUP' if price<ma20 or (rsi is not None and rsi>=75) or (hist is not None and hist<0) else 'TECHNICAL SETUP LOOKS POSITIVE' if score>=3 and spread is not None and spread<=cfg().get('max_spread_pct',0.5) else 'WAIT FOR STRONGER CONFIRMATION'
  def fmt(v):return fp(v) if isinstance(v,(int,float)) else 'N/A'
  lines=[f"📊 *ANALYSIS: {sym}*",f"Live price: {fmt(price)} USDT",f"24h change: {change:+.2f}%" if isinstance(change,(int,float)) else "24h change: N/A",f"RSI(14): {fmt(rsi)} — "+("Overbought" if rsi is not None and rsi>=70 else "Oversold" if rsi is not None and rsi<=30 else "Neutral" if rsi is not None else "N/A"),f"MA5: {fmt(ma5)} | MA10: {fmt(ma10)} | MA20: {fmt(ma20)}",f"MACD: {fmt(macd)} | Signal: {fmt(signal)} | Histogram: {fmt(hist)}",f"ATR(14): {fmt(atr)} ({(atr/price*100):.2f}% of price)" if atr is not None and price else "ATR(14): N/A",f"Last hour vol vs 20h avg: {(lastvol/avgvol):.2f}x" if avgvol else "Volume: N/A",f"Bid/ask spread: {spread:.3f}%" if spread is not None else "Bid/ask spread: N/A",f"Technical checks: {score}/4",f"Assessment: *{verdict}*"]
  tg("\n".join(lines))
 except Exception as e:tg(f"❌ Could not analyse {sym}: {e}")
def ask_ai(p):
 api_key=os.environ.get('GROQ_API_KEY','').strip() or GROQ_API_KEY
 model=os.environ.get('GROQ_MODEL','').strip() or GROQ_MODEL or 'openai/gpt-oss-120b'
 if not api_key:
  tg("⚠️ Groq AI is not configured. Create a free API key at https://console.groq.com/keys, add it as GROQ_API_KEY in your bot host's Secrets/Environment Variables, then restart the bot. GROQ_MODEL is optional (default: openai/gpt-oss-120b). The free tier has usage limits.");return
 try:
  db=load(DB);q=load(QF);s=cfg();bal=B('free','USDT');br=data("BTC-USDT")[2]
  pos=[f"  {sym}: entry={d.get('entry_price')}, pnl={((live(sym) or d.get('entry_price',0))-d.get('entry_price',1))/d.get('entry_price',1)*100:+.1f}%" for sym,d in list(db.items())[:8] if d.get('entry_price')] or ["  (none)"]
  ctx=f"Free USDT: {bal:.2f}|AutoBuy: ${s['auto_buy_amount_usdt']}|BTC RSI: {br}|Positions({len(db)}):\n"+"\n".join(pos)+f"\nQueue: {len(q)}|RSI {s['rsi_min']}-{s['rsi_max']}, SL {s['stop_loss_pct']}%\n\n"
  prompt="You are an expert crypto market analyst advising a spot trading bot user. Use plain text, concise, acknowledge uncertainty. You are advisory only: never claim to execute trades or override deterministic bot risk controls. Context:\n"+ctx+"User question: "+str(p)
  r=requests.post("https://api.groq.com/openai/v1/chat/completions",headers={"Authorization":"Bearer "+api_key,"Content-Type":"application/json"},json={"model":model,"messages":[{"role":"user","content":prompt}],"temperature":0.4,"max_tokens":1200},timeout=45)
  if not r.ok:
   try: detail=(r.json().get("error",{}).get("message") or r.text or "")[:350]
   except Exception: detail=(r.text or "")[:350]
   if r.status_code==401: raise RuntimeError("Groq rejected the API key. Check GROQ_API_KEY in your host secrets.")
   if r.status_code==429: raise RuntimeError("Groq free-tier rate limit reached. Wait for the limit to reset and try again: "+detail)
   if r.status_code==404: raise RuntimeError(f"Groq model '{model}' is unavailable. Check GROQ_MODEL against Groq's current model list.")
   raise RuntimeError(f"Groq HTTP {r.status_code}: {detail}")
  data_json=r.json();txt=data_json.get("choices", [{}])[0].get("message",{}).get("content")
  if not txt:raise RuntimeError("Groq returned an empty response")
  _send(f"🧠 AI ({model} via Groq):\n\n{txt[:3800]}",None)
 except Exception as e:tg(f"❌ AI Error: {str(e)[:500]}")
def manage():
 db=load(DB);s=cfg();tp=s.get('trail_pct',0) or 0
 for sym,d in list(db.items()):
  d.setdefault('opened_at',time.time());dt=data(sym);r=dt[2];lv=live(sym)
  if not lv:continue
  e=d["entry_price"];inc=(lv-e)/e*100;d['peak_price']=max(d.get('peak_price',e),lv);lp=d.get('last_price',0);d['seen']=time.time()
  if lp>0:
   drop=(lv-lp)/lp*100
   if drop<=-s.get('crash_guard_pct',18) and sell(sym,1.0,f"CRASH GUARD ({drop:.1f}%)"):db.pop(sym,None);save(DB,db);continue
  d['last_price']=lv
  if inc>=5.0 and d['stop_loss']<e:d['stop_loss']=e
  sp=sl_pct(sym,d);c=[(round(e*(1-sp/100),6),f"SL HIT ({sp}%)"),(d['stop_loss'],"STOP LOSS")]
  tpe=tp
  if tp>0 and s.get('dynamic_trail',True) and dt[3] and dt[4]:
   if r and r>=60 and lv>dt[3]>dt[4]:tpe=tp*1.25
   elif lv<dt[3] and inc>0:tpe=tp*0.75
  if tp>0:c.append((d['peak_price']*(1-tpe/100),f"TRAILING STOP ({tpe:.1f}%)"))
  lvl,why=max(c,key=lambda x:x[0])
  if lv<=lvl:
   if sell(sym,1.0,why):db.pop(sym,None);save(DB,db)
   continue
  mh=s.get('max_hold_hours',48)
  if (time.time()-d['opened_at'])/3600>mh and inc<=1.0:
   if sell(sym,1.0,f"TIME STOP {mh}h ({inc:+.1f}%)"):db.pop(sym,None);save(DB,db)
   continue
  if r and s['rsi_exit']>0 and r>=s['rsi_exit'] and sell(sym,1.0,f"RSI {s['rsi_exit']}"):db.pop(sym,None);save(DB,db);continue
  ptp=float(s.get('partial_tp_pct',0) or 0);pf=float(s.get('partial_tp_fraction',0.5) or 0)
  try:partial_profit(sym,d,db,s,lv,inc,ptp,pf)
  except Exception as e:print(f"PARTIAL PROFIT ERR {sym}: {e}")
  ratchet(sym,d,lvl,lv);db[sym]=d
 save(DB,db)
def partial_profit(sym,d,db,s,lv,inc,ptp,pf):
 if not (ptp>0 and 0<pf<1 and not d.get('partial_done') and inc>=ptp):return
 coin=sym.split('-')[0];qh=B('total',coin)
 if qh*lv*pf>=5.0 and qh*lv*(1-pf)>=5.0:
  d['partial_done']=True;db[sym]=d;save(DB,db)
  if sell(sym,pf,f"PARTIAL PROFIT +{inc:.1f}%"):
   time.sleep(1);rem=B('total',coin);d.pop('xsl',None)
   if rem*lv>=1.02:
    place_trailing_sl(sym,rem);d['xsl']=load(DB).get(sym,{}).get('xsl',0)
    tg(f"💰 *PARTIAL PROFIT:* sold {pf*100:.0f}% of {coin} at +{inc:.1f}%. Remaining {rem:.6g} {coin} keeps a protective stop; trailing continues.")
 else:d['partial_done']=True
def btc_blocked(s):
 m=s.get('btc_rsi_min',0)
 if m<=0:return False
 br=data("BTC-USDT")[2]
 if br is not None and br>=m:return False
 cd("btcblock",21600) or tg(f"⏸ *BTC FILTER:* BTC RSI is {br} (needs ≥ {m}).")
 return True
def equity_now():
 tot=ex.fetch_balance().get('total') or {};eq=float(tot.get('USDT',0) or 0)
 for coin,qty in tot.items():
  if coin in STABLE or coin=='USDT' or float(qty or 0)<=0:continue
  px=live(f"{coin}-USDT")
  if px:eq+=float(qty)*px
 return eq
def day_drawdown(equity=None):
 eq=equity if equity is not None else equity_now()
 st=load(SC);today=time.strftime('%Y-%m-%d',time.gmtime());dd=st.get('dd') or {}
 if dd.get('date')!=today or not dd.get('equity'):dd={'date':today,'equity':eq};st['dd']=dd;save(SC,st)
 start=float(dd['equity']);return start,((eq-start)/start*100 if start>0 else 0.0)
def track_day_equity():
 try:day_drawdown()
 except Exception as e:print(f"DAY EQUITY ERR: {e}")
def risk_controls():
 s=cfg();now=time.time();day=[t for t in load(HIST) if now-float(t.get('time',0))<86400];realized=sum(float(t.get('pnl',0)) for t in day)
 try:
  bal=ex.fetch_balance();equity=float((bal.get('total') or {}).get('USDT',0))
  for coin,qty in (bal.get('total') or {}).items():
   if coin in STABLE or coin=='USDT' or float(qty or 0)<=0:continue
   px=live(f"{coin}-USDT")
   if px:equity+=float(qty)*px
 except Exception as e:return False,f"Cannot verify equity: {e}"
 if equity<=0:return False,"Equity unavailable"
 ddl=float(s.get('max_daily_drawdown_pct',0) or 0)
 if ddl>0:
  try:
   _,ddp=day_drawdown(equity)
   if ddp<=-ddl:return False,f"Daily drawdown {ddp:.1f}% (limit -{ddl:g}%)"
  except Exception as e:print(f"DRAWDOWN ERR: {e}")
 ntr=len([t for t in day if not str(t.get('reason','')).startswith('PARTIAL')])
 if ntr>=int(s.get('max_daily_trades',5)):return False,f"Daily limit ({ntr})"
 limit=equity*float(s.get('max_daily_loss_pct',3.0))/100
 if realized<=-limit:return False,f"Loss limit (${realized:.2f} / -${limit:.2f})"
 return True,""
def risk_sized_budget(sym,price,stop,available_usdt):
 s=cfg()
 try:
  bal=ex.fetch_balance();tot=bal.get('total') or {};equity=float(tot.get('USDT',0));exposure=0.0
  for coin,qty in tot.items():
   if coin in STABLE or coin=='USDT' or float(qty or 0)<=0:continue
   px=live(f"{coin}-USDT")
   if px:exposure+=float(qty)*px
  equity+=exposure
  if equity<=0:return 0.0
  distance=max(0.005,(price-stop)/price);risk_cash=equity*float(s.get('risk_per_trade_pct',1.0))/100
  by_risk=risk_cash/distance;max_position=equity*float(s.get('max_position_pct',15.0))/100;max_exposure=equity*float(s.get('max_portfolio_exposure_pct',50.0))/100
  remaining=max(0.0,max_exposure-exposure)
  return max(0.0,min(float(available_usdt),by_risk,max_position,remaining,float(s.get('auto_buy_amount_usdt',10.0))))
 except:return 0.0
def buy_rejection(sym,reason):
 key=f"buy_reject_notice:{sym}:{reason[:60]}"
 if not in_cd(key,900):tg(f"⛔ *AUTO-BUY REJECTED — {sym}*\nReason: {reason}");mk_cd(key)
def protect_retry(sym,f):
 time.sleep(2)
 try:return place_trailing_sl(sym,free_qty(sym,f))
 except Exception:return False
def unprotected_exit(sym,db):
 tg(f"🚨 *{sym} has NO exchange stop after a retry.* Selling at market to protect your funds.")
 if sell(sym,1.0,"UNPROTECTED (stop failed)"):db.pop(sym,None);save(DB,db)
def trend_4h_ok(sym,s):
 if not s.get('mtf_confirm',True):return True
 try:
  o=ex.fetch_ohlcv(S(sym),'4h',limit=30)
  c=[float(k[4]) for k in o[:-1]]
  if len(c)<12:return True
  return float(o[-1][4])>=sum(c[-10:])/10
 except Exception:return True
_SHOCK={"t":0,"r":""}
def btc_shock(s):
 lim=float(s.get('btc_shock_pct',3.0) or 0)
 if lim<=0:return ""
 if time.time()-_SHOCK["t"]<300:return _SHOCK["r"]
 r=""
 try:
  o=ex.fetch_ohlcv("BTC/USDT",'1h',limit=3);a=float(o[-2][4]);b=float(o[-1][4]);ch=(b-a)/a*100 if a>0 else 0
  if ch<=-lim:r=f"BTC fell {ch:.1f}% within the hour"
 except Exception:r=""
 _SHOCK["t"],_SHOCK["r"]=time.time(),r;return r
def open_risk_pct():
 try:
  db=load(DB);eq=equity_now()
  if not eq or eq<=0:return 0.0
  tot=0.0
  for sym,d in db.items():
   px=live(sym);q=B('total',sym.split('-')[0])
   if not px or q<=0:continue
   e=float(d.get('entry_price') or px);stop=max(float(d.get('stop_loss') or 0),e*(1-sl_pct(sym,d)/100))
   tot+=max(0.0,px-stop)*q
  return tot/eq*100
 except Exception:return 0.0
def loss_streak_adjust(sp,s):
 f=float(s.get('loss_streak_size_factor',0.5) or 0);n=int(s.get('loss_streak_trigger',2) or 0)
 if n<=0 or not (0<f<1):return sp
 h=[t for t in load(HIST) if isinstance(t,dict) and not str(t.get('reason','')).startswith(('PARTIAL','MANUAL','PANIC'))][-n:]
 return sp*f if len(h)>=n and all(float(t.get('pnl',0))<0 for t in h) else sp
def exec_q():
 q=load(QF);db=load(DB);s=cfg();mx=s["max_tokens_before_consolidation"]
 if not q:return
 if not s["auto_buy_enabled"]:
  for sym in q:buy_rejection(sym,"Auto-buy paused.")
  return
 if btc_blocked(s):
  for sym in q:buy_rejection(sym,"BTC filter active.")
  return
 mf=market_filter(s)
 if mf:
  for sym in q:buy_rejection(sym,"Market filter active.")
  if not cd("mktblock",21600):tg(f"⏸ *MARKET FILTER:* {mf}.")
  return
 shk=btc_shock(s)
 if shk:
  for sym in q:buy_rejection(sym,"BTC shock.")
  if not cd("btcshock",3600):tg(f"⏸ *BTC SHOCK:* {shk}. New buys wait until it settles.")
  return
 allowed,reason=risk_controls()
 if not allowed:
  for sym in q:buy_rejection(sym,f"Risk: {reason}")
  if not in_cd('risk_pause_notice',3600):tg(f"🛑 *RISK BREAKER:* {reason}. Paused.")
  return
 try:
  ub=B('free','USDT')
  for sym,qd in sorted(q.items(),key=lambda x:x[1].get('score',0),reverse=True):
   if in_cd(f"fail_{sym}",300):buy_rejection(sym,"Previous fail.");continue
   if sym in db:buy_rejection(sym,"Position exists.");q.pop(sym,None);save(QF,q);continue
   if not live(sym):buy_rejection(sym,"No price.");continue
   hrs=float(s.get('rebuy_cooldown_hours',6.0))
   if hrs>0 and in_cd(f"stopout_{sym}",hrs*3600):buy_rejection(sym,f"Re-buy cooldown ({hrs:g}h after a loss).");q.pop(sym,None);save(QF,q);continue
   sprd=spread_pct(sym);lim=float(s.get('max_spread_pct',0.5))
   if sprd is None or sprd>lim:buy_rejection(sym,f"Spread too wide or unavailable (limit {lim:g}%).");continue
   spend=ub
   if len(db)>=mx:buy_rejection(sym,f"Max positions ({mx}).");continue
   if spend<1.00:buy_rejection(sym,f"Insufficient USDT (${spend:.2f}).");q.pop(sym,None);save(QF,q);continue
   cap=float(s.get('max_open_risk_pct',3.0) or 0)
   if cap>0 and db:
    orp=open_risk_pct()
    if orp+float(s.get('risk_per_trade_pct',1.0))>cap:buy_rejection(sym,f"Combined open risk {orp:.1f}% (limit {cap:g}%).");continue
   pct=sl_pct(sym);sp=risk_sized_budget(sym,live(sym),qd.get('sl') or live(sym)*(1-pct/100),spend);sp=loss_streak_adjust(sp,s)
   if sp<1.02:buy_rejection(sym,f"Size too small (${sp:.2f}).");continue
   try:f,a=m_buy(S(sym),sp)
   except Exception as e:buy_rejection(sym,f"Order failed: {e}");mk_cd(f"fail_{sym}");continue
   mxs=round(a*(1-pct/100),6);sl=qd.get('sl') or mxs
   if sl>=a or sl>mxs:sl=mxs
   db[sym]=NP(round(a,6),sl,pct);db[sym]['strategy']=qd.get('type','OTHER');save(DB,db);ok=place_trailing_sl(sym,free_qty(sym,f)) or protect_retry(sym,f);tg(f"🛒 *EXECUTED!*\n{sym}|💵{sp:.2f}@{a:.6f}\n"+(STOP_OK if ok else STOP_NO))
   if not ok and s.get('sell_if_stop_fails',True):unprotected_exit(sym,db)
   q.pop(sym,None);save(QF,q);ub=B('free','USDT');time.sleep(2);break
 except Exception as e:tg(f"⚠️ *QUEUE FAILED:* `{e}`")
def scan_history_state():
 h=load(SH,{})
 if not isinstance(h,dict):h={}
 now=time.time();start=float(h.get("window_start",0) or 0)
 if not start or now-start>=86400:h={"window_start":now,"items":[]};save(SH,h)
 h.setdefault("items",[]);return h
def scan_history_rsi(sym):
 try:
  cached=data(sym)[2]
  if isinstance(cached,(int,float)):return cached
 except:pass
 try:
  candles=ex.fetch_ohlcv(S(sym),"1h",limit=30)
  if len(candles)<16:return None
  closes=[float(c[4]) for c in candles[:-1]];return _rsi(closes)
 except:return None
def cmd_scan_history(txt="/scan_history"):
 import re, shlex
 h=scan_history_state();items=h.get("items",[])
 try: parts=shlex.split(txt)[1:]
 except: parts=txt.split()[1:]
 filters=[];page=1;limit=20;bad=[]
 for token in parts:
  t=token.strip();low=t.lower()
  if low.startswith("page="):
   try: page=max(1,int(t.split("=",1)[1]))
   except ValueError: bad.append(t)
  elif low.startswith("limit="):
   try: limit=max(1,min(50,int(t.split("=",1)[1])))
   except ValueError: bad.append(t)
  elif re.match(r"^(rsi|chg|change|rank|vol)(<=|>=|=|<|>)-?\d+(?:\.\d+)?$",low):
   m=re.match(r"^(rsi|chg|change|rank|vol)(<=|>=|=|<|>)(-?\d+(?:\.\d+)?)$",low)
   key={"change":"chg","vol":"vol"}.get(m.group(1),m.group(1));filters.append((key,m.group(2),float(m.group(3))))
  elif low.startswith("coin="): filters.append(("coin","contains",low.split("=",1)[1]))
  elif low.startswith("decision="): filters.append(("decision","contains",low.split("=",1)[1]))
  elif low.startswith("reason="): filters.append(("reason","contains",low.split("=",1)[1]))
  elif low in ("buy","queued","setup","not_queued","skip","rejected"):
   val={"buy":"setup","queued":"queued","not_queued":"not queued","rejected":"not queued"}.get(low,low);filters.append(("decision","contains",val))
  elif low in ("trend","oversold"): filters.append(("reason","contains",low))
  elif low.startswith("/scan_history"): continue
  else: filters.append(("coin","contains",low))
 if bad:tg("❌ Invalid filter: "+", ".join(bad));return
 def matches(x):
  for key,op,val in filters:
   if key in ("coin","decision","reason"):
    actual=(str(x.get("sym", "")) if key=="coin" else str(x.get(key,""))).lower()
    if val not in actual:return False
   else:
    actual=x.get(key)
    if not isinstance(actual,(int,float)):return False
    if not {"<":actual<val,">":actual>val,"<=":actual<=val,">=":actual>=val,"=":actual==val}[op]:return False
  return True
 found=sorted((x for x in items if matches(x)), key=lambda x: (int(x.get("rank", 10**9) or 10**9), -float(x.get("time", 0) or 0)))
 total=max(1,(len(found)+limit-1)//limit)
 if page>total:tg(f"Only {total} page(s) match.");return
 chunk=found[(page-1)*limit:page*limit];changed=False
 for x in chunk:
  if not isinstance(x.get("rsi"),(int,float)):
   _rsi_value=scan_history_rsi(str(x.get("sym","")))
   if isinstance(_rsi_value,(int,float)):x["rsi"]=_rsi_value;changed=True
 if changed:save(SH,h)
 age=max(0,time.time()-h.get("window_start",time.time()));rows=[]
 for x in chunk:
  r=x.get("rsi");rsi=f"{r:.1f}" if isinstance(r,(int,float)) else "N/A"
  sym=str(x.get("sym","?")).replace("-USDT","")[:12];raw_decision=str(x.get("decision","SKIP")).upper()
  if "NOT QUEUED" in raw_decision or "REJECT" in raw_decision: decision="REJECTED"
  elif "QUEUED" in raw_decision: decision="QUEUED"
  elif "BUY" in raw_decision or "BOUGHT" in raw_decision: decision="BUY"
  elif "SELL" in raw_decision or "SOLD" in raw_decision: decision="SOLD"
  else: decision="SKIP"
  rr=x.get("rr");rr_text=f"1:{rr:.1f}" if isinstance(rr,(int,float)) and rr>0 else "-"
  rows.append(f"{sym:<12} {rsi:>5} {rr_text:>6} {decision}")
 table="COIN         RSI    R:R  DECISION\n"+"-"*39+"\n"+"\n".join(rows)
 query=" ".join(parts) if parts else "all coins"
 msg=f"🔎 *SCAN HISTORY — QUERY*\nMatched {len(found)} of {len(items)} records | {query}\nPage {page}/{total} | Window {age/3600:.1f}h\n\n```{table}```"
 nav=[]
 if page>1:nav.append({"text":"⬅️ Previous","callback_data":f"scan_history:{page-1}:{' '.join(parts)}"})
 if page<total:nav.append({"text":"Next ➡️","callback_data":f"scan_history:{page+1}:{' '.join(parts)}"})
 keyboard=[]
 if nav:keyboard.append(nav)
 keyboard.append([{"text":"🕹️ Main Menu","callback_data":"/menu"}])
 _send(msg,"Markdown",{"inline_keyboard":keyboard})
def scan(force=False):
 st=load(SC);now=time.time()
 iv=max(1.0,float(cfg().get('scan_interval_minutes',15) or 15))
 if not force and now-st.get('last',0)<max(60,iv*60-240):return
 db=load(DB);q=load(QF);s=cfg();auto_enabled=bool(s.get("auto_buy_enabled"));btc_is_blocked=btc_blocked(s) if auto_enabled else False
 mf=market_filter(s) if auto_enabled and not btc_is_blocked else ""
 if mf and not cd("mktblock",21600):tg(f"⏸ *MARKET FILTER:* {mf}.")
 rbh=float(s.get('rebuy_cooldown_hours',6.0))
 buying_allowed=auto_enabled and not btc_is_blocked and not mf;block_reason=("automatic buying is paused" if not auto_enabled else "BTC safety filter blocked buying" if btc_is_blocked else f"market filter: {mf}" if mf else "")
 try:tk=ex.fetch_tickers();mk=ex.load_markets()
 except Exception as e:print(f"SCAN ERROR: {e}");tg(f"⚠️ *SCAN FAILED:* {e}");return
 v=[{'sym':k.replace("/","-"),'vol':t['quoteVolume'] or 0,'chg':t['percentage'] or 0,'last':t.get('last')} for k,t in tk.items() if k.endswith('/USDT') and not skip(k.split('/')[0]) and not (k in mk and not mk[k].get('spot',False))]
 v.sort(key=lambda x:x['vol'],reverse=True);ct=int(time.time());qu=False
 for sym,d in list(q.items()):
  if ct-d.get('timestamp',0)>86400:q.pop(sym,None);qu=True
 if not buying_allowed and q:q.clear();qu=True
 sh=scan_history_state();records=[];matched=0;queued=0
 _PF.clear()
 if s.get('scan_parallel',True):
  try:
   cand=[]
   for rank,t in enumerate(v[:500],start=1):
    if rank<=15:continue
    i=rank-16;th=(1000000 if i<=84 else 3000000 if i<=183 else 4000000 if i<=383 else 5000000);chg=float(t['chg'] or 0)
    if float(t['vol'] or 0)<th:continue
    if s.get('scan_prefilter',True) and not (-30.0<chg<-8.0 or 0.0<chg<30.0):continue
    cand.append(t['sym'])
   from concurrent.futures import ThreadPoolExecutor
   with ThreadPoolExecutor(max_workers=4) as pool:
    for sy,res in zip(cand,pool.map(_calc,cand)):_PF[sy]=res
  except Exception as e:print(f"PREFETCH ERR: {e}");_PF.clear()
 for rank,t in enumerate(v[:500],start=1):
  sym=t['sym'];vol=float(t['vol'] or 0);chg=float(t['chg'] or 0)
  recd={"time":ct,"rank":rank,"sym":sym,"vol":vol,"chg":chg,"price":t.get("last"),"rsi":None,"rr":None,"ma5":None,"ma10":None,"decision":"SKIP","reason":"Top 15 excluded" if rank<=15 else "Not evaluated"}
  if rank>15:
   i=rank-16;threshold=(1000000 if i<=84 else 3000000 if i<=183 else 4000000 if i<=383 else 5000000);recd["min_volume"]=threshold
   if vol<threshold:recd["reason"]=f"Vol below ${threshold/1e6:.0f}M"
   elif s.get('scan_prefilter',True) and not (-30.0<chg<-8.0 or 0.0<chg<30.0):recd["reason"]="24H change outside every setup range (skipped before candle fetch)"
   else:
    p,pr,r,ma5,ma10=_PF.get(sym) or data(sym);recd.update(price=p,rsi=r,ma5=ma5,ma10=ma10)
    if not p or r is None:recd["reason"]="No candle data"
    else:
     setup=sl=None;breakout_volume=None
     if 1.0<chg<30.0 and s['rsi_min']<=r<=s['rsi_max'] and p>ma5>ma10:setup="TREND";sl=round(ma5*(1-s['stop_loss_pct']/100),6)
     elif -30.0<chg<-8.0 and r<30 and p>pr:setup="OVERSOLD";sl=round(p*(1-s['stop_loss_pct']/100),6)
     elif 0.0<chg<25.0 and 50.0<=r<=70.0 and p>ma5>ma10:
      confirmed,breakout_volume,_=breakout_confirmation(sym)
      if confirmed:setup="BREAKOUT";sl=round(p*(1-s['stop_loss_pct']/100),6)
     if setup in ("TREND","BREAKOUT") and not trend_4h_ok(sym,s):
      recd["reason"]=f"{setup} matched but 4h trend is down";setup=None
     if setup:
      recd["strategy"]=setup
      if setup=="BREAKOUT":recd["reason"]=f"BREAKOUT vol {breakout_volume:.2f}x"
      matched+=1;rr=None;recd["rr"]=None;score=setup_score(setup,vol,r,ma5,ma10,breakout_volume)
      if not buying_allowed:recd["decision"]="SETUP — NOT QUEUED";recd["reason"]=f"{setup} matched, {block_reason}"
      elif rbh>0 and in_cd(f"stopout_{sym}",rbh*3600):recd["decision"]="SETUP — NOT QUEUED";recd["reason"]=f"{setup} matched, re-buy cooldown"
      else:
       recd["decision"]="SETUP — QUEUED";recd["reason"]=setup+" matched; queued"
       if sym not in q or q[sym].get('score',0)<score:
        if sym not in q:tg(f"📥 *QUEUED:* {sym}\nScore:{score:.1f}")
        q[sym]={"timestamp":ct,"type":setup,"sl":sl,"score":score,"price":p,"chg":chg};qu=True
       queued+=1
     elif "4h trend is down" not in str(recd.get("reason","")):
      reasons=[]
      if not (1.0<chg<30.0):reasons.append("Trend range")
      if not (s['rsi_min']<=r<=s['rsi_max']):reasons.append(f"RSI {s['rsi_min']}-{s['rsi_max']}")
      if not (p>ma5>ma10):reasons.append("MA trend")
      if not (-30.0<chg<-8.0 and r<30 and p>pr):reasons.append("Oversold recovery")
      if not (0.0<chg<25.0 and 50.0<=r<=70.0 and p>ma5>ma10):reasons.append("Breakout trend")
      else:reasons.append("No breakout")
      recd["reason"]="; ".join(reasons) or "No match"
  records.append(recd)
 full=force or now-st.get('last_full',0)>=3300
 sh["items"].extend(records if full else [x for x in records if str(x.get('decision','')).startswith('SETUP')]);sh["items"]=sh["items"][-15000:];save(SH,sh)
 if qu or not buying_allowed:save(QF,q)
 st['last']=time.time()
 if full:st['last_full']=st['last']
 save(SC,st)
 if matched:
  msg=f"🔍 *SCAN COMPLETE*\nChecked {len(records)} markets.\nMatched: {matched} | Queued: {queued}." + ("\n" + block_reason if not buying_allowed else "")
  try:
   rows=[x for x in records if str(x.get('decision','')).startswith('SETUP')]
   lines=["","✅ *MATCHED COINS*"]
   for n,x in enumerate(rows,1):
    lines.append(f"{n}. *{x['sym']}* — {str(x.get('reason','')).replace('_',' ')}\n   Price {fp(x.get('price'))} | 24h {float(x.get('chg') or 0):+.2f}% | RSI {x['rsi'] if x.get('rsi') is None else round(x['rsi'],1)} | {'✅ queued' if x.get('decision')=='SETUP — QUEUED' else '⏸ not queued'}")
   body=lines[:2];used=len(msg)+len("\n".join(body))
   for i,ln in enumerate(lines[2:]):
    if used+len(ln)+1>3800:body.append(f"…and {len(lines)-2-i} more (see /scan\\_history)");break
    body.append(ln);used+=len(ln)+1
   msg+="\n"+"\n".join(body)
  except Exception as e:print(f"MATCH LIST ERR: {e}")
  tg(msg)
 elif full:tg(f"🔍 *SCAN COMPLETE*\nChecked {len(records)} markets.\nNo coin matched any setup.")
def fp(p):
 if p is None:return "N/A"
 return f"{p:.2f}" if p>=100 else f"{p:.4f}" if p>=1 else f"{p:.6f}" if p>=0.001 else f"{p:.8f}"
def get_market_row(sym,chg=None):
 if chg is None:
  try:chg=ex.fetch_ticker(S(sym))['percentage'] or 0
  except:chg=0
 r=data(sym)[2];rsi=f"{r:.1f}" if r is not None else "-"
 return {'coin':sym.split('-')[0],'price':fp(live(sym)),'chg':float(chg or 0),'rsi':rsi}
def gl(limit=10,which=None):
 try:tk=ex.fetch_tickers()
 except:return "❌ Unable to fetch market data."
 v=sorted([{'sym':k.replace("/","-"),'chg':t['percentage'] or 0} for k,t in tk.items() if k.endswith('/USDT') and not skip(k.split('/')[0]) and (t['quoteVolume'] or 0)>=1000000],key=lambda x:x['chg'],reverse=True)
 gainers=[x for x in v if x['chg']>0][:limit];losers=[x for x in reversed(v) if x['chg']<0][:limit]
 def blk(title,rows):
  out=[title]
  for i,x in enumerate(rows,1):out.append(f"{i:>2}. {x['sym'].split('-')[0][:8]:<8} {x['chg']:>+7.2f}%")
  if not rows:out.append("none")
  return "\n".join(out)
 if which=="g":return f"📈 *TOP {limit} GAINERS (24H)*\n\n```\n{blk('📈 GAINERS',gainers)}```\n_Minimum 24H volume: $1M._"
 if which=="l":return f"📉 *TOP {limit} LOSERS (24H)*\n\n```\n{blk('📉 LOSERS',losers)}```\n_Minimum 24H volume: $1M._"
 return f"📊 *TOP {limit} MOVERS (24H)*\n\n```\n{blk('📈 GAINERS',gainers)}```\n```\n{blk('📉 LOSERS',losers)}```\n_Minimum 24H volume: $1M._"
def cmd_set(txt):
 p=txt.split()
 if len(p)!=3:tg("❌ Usage: `/set <key> <value>`");return
 k,val=p[1],p[2];s=cfg()
 if k not in s:tg(f"❌ Unknown key. Valid: `{'`, `'.join(s.keys())}`");return
 try:new=val.lower() in ('true','1','yes','on') if isinstance(s[k],bool) else float(val) if isinstance(s[k],float) else int(val)
 except:tg(f"❌ Invalid value for {k}");return
 limits={"risk_per_trade_pct":(0.1,2.0),"max_daily_loss_pct":(0.5,10.0),"max_portfolio_exposure_pct":(5.0,100.0),"max_position_pct":(1.0,50.0),"max_daily_trades":(1,50),"max_spread_pct":(0.05,3.0),"trail_pct":(0.0,30.0),"scan_interval_minutes":(5.0,240.0),"auto_clear_hours":(0.0,168.0),"live_mode_seconds":(0,330),"stop_loss_pct":(0.5,20.0),"rsi_min":(1.0,99.0),"rsi_max":(1.0,99.0),"rsi_exit":(0.0,99.0),"auto_buy_amount_usdt":(1.0,100000.0),"max_tokens_before_consolidation":(1,30),"max_hold_hours":(1,720),"crash_guard_pct":(1.0,50.0),"btc_rsi_min":(0.0,99.0),"rebuy_cooldown_hours":(0.0,168.0),"atr_stop_mult":(0.5,6.0),"atr_stop_min_pct":(1.0,10.0),"atr_stop_max_pct":(2.0,25.0),"btc_trend_hours":(10,200),"min_breadth_pct":(0.0,100.0),"offline_alert_hours":(0.0,72.0),"daily_summary_hour_utc":(0,23)}
 if k in limits and not (limits[k][0] <= new <= limits[k][1]):tg(f"❌ `{k}` must be between {limits[k][0]} and {limits[k][1]}.");return
 if k=="rsi_min" and new>=s.get("rsi_max",99):tg("❌ rsi_min must be lower than rsi_max.");return
 if k=="rsi_max" and new<=s.get("rsi_min",1):tg("❌ rsi_max must be higher than rsi_min.");return
 s[k]=new;save(CFG,s);tg(f"✅ `{k}` = `{new}`")
def setc(k,v,m):s=cfg();s[k]=v;save(CFG,s);tg(m)
MSG={"ai_info":"🧠 *AI ANALYST*\nTo ask the AI a question, type:\n`/ai <your question>`","/help":"🤖 *CMDS*\n`/menu` `/ai [q]` `/analyse` (top 3) `/analyse COIN` `/audit` `/cancel`\n`/bot` `/holdings` `/stats`\n`/clear` `/dust` `/alert COIN PRICE` `/alerts` `/cancel\\_alerts`\n`/scan_history [coin/filter]`\n`/set key value` `/pause` `/resume` `/panic`"}
_UI={"mid":None,"edit_mode":False,"panel_used":False}
_B=lambda t,c:{"text":t,"callback_data":c}
_MENU_BTN=[_B("⬅️ Menu","m:menu")]
RUN={"scan":"/scan","sync":"/sync","holdings":"/holdings","analyse":"/analyse","alerts":"/alerts","cancelalerts":"/cancel_alerts","dust":"/dust","bot":"/bot","audit":"/audit","stats":"/stats","clear":"/clear","pause":"/pause","resume":"/resume","cancel":"/cancel","pricealert":"/alerts"}
AI_Q={"1":"Give me a market overview.","2":"Review my current holdings and say which to hold or sell.","3":"Which single coin looks best to buy right now and why?","4":"What is the biggest risk in my portfolio right now?"}
def ui_send(text,rows):_panel(text,rows)
def _grid(btns,n=3):return [btns[i:i+n] for i in range(0,len(btns),n)]
def ui_coins(kind):
 out=[];seen=set()
 def add(c):
  c=str(c).split("-")[0].upper()
  if c and c not in seen and c not in STABLE and c not in BL:seen.add(c);out.append(c)
 for k in load(DB):add(k)
 if kind=="sell":return out[:18]
 for k in load(QF):add(k)
 try:
  for x in reversed(((load(SH,{}) or {}).get("items") or [])[-3000:]):
   if str(x.get("decision","")).startswith("SETUP"):add(x.get("sym",""))
   if len(out)>=18:break
 except Exception:pass
 try:
  for c,q in (ex.fetch_balance().get('total') or {}).items():
   if float(q or 0)>0 and c not in STABLE:
    p=live(f"{c}-USDT")
    if p and float(q)*p>=0.10:add(c)
 except Exception:pass
 return out[:18]
def ui_settings_page(pg=0):
 keys=[k for k in cfg() if k!="auto_mode_v1"];per=12;n=(len(keys)+per-1)//per;pg=max(0,min(pg,n-1));cur=cfg()
 btns=[_B(f"{k} = {cur[k]}"[:30],f"m:k:{k}") for k in keys[pg*per:(pg+1)*per]]
 nav=[]
 if pg>0:nav.append(_B("◀️ Prev",f"m:sp:{pg-1}"))
 if pg<n-1:nav.append(_B("Next ▶️",f"m:sp:{pg+1}"))
 ui_send(f"⚙️ *SETTINGS* (page {pg+1}/{n})\nTap a setting to change it.",_grid(btns,2)+([nav] if nav else [])+[_MENU_BTN])
def ui_setting(k):
 cur=cfg()
 if k not in cur:ui_send("❌ Unknown setting.",[_MENU_BTN]);return
 v=cur[k]
 if isinstance(v,bool):row=[_B("🔄 Switch ON/OFF",f"m:kv:{k}:t")]
 else:row=[_B("➖ Decrease",f"m:kv:{k}:-"),_B("➕ Increase",f"m:kv:{k}:+")]
 ui_send(f"⚙️ *{str(k).replace('_',' ')}*\nCurrent value: `{v}`",[row,[_B("⬅️ Settings","m:sp:0")]])
def ui_cb(t):
 p=t.split(":");a=p[1] if len(p)>1 else "menu"
 try:
  if a=="menu":menu()
  elif a=="clear":cmd_clear()
  elif a=="r" and len(p)>2 and p[2] in RUN:
   _UI["edit_mode"]=True
   try:handle(RUN[p[2]],0)
   finally:_UI["edit_mode"]=False
  elif a=="buy":
   cs=ui_coins("buy")
   ui_send("🛒 *BUY* — choose a coin:",_grid([_B(c,f"m:b:{c}") for c in cs],3)+[[_B("🏆 Top 3 ideas","m:r:analyse")],_MENU_BTN])
  elif a=="b":
   c=p[2];free=B('free','USDT');amts=[x for x in (2,5,10,15) if x<=free]
   ui_send(f"🛒 *BUY {c}*\nFree USDT: ${free:.2f}\nChoose how much:",[[_B(f"${x}",f"m:bc:{c}:{x}") for x in amts]+[_B("ALL",f"m:bc:{c}:ALL")],[_B("⬅️ Back","m:buy")]])
  elif a=="bc":
   c,x=p[2],p[3];px=live(f"{c}-USDT")
   ui_send(f"⚠️ *CONFIRM BUY*\n{c} for {'ALL free USDT' if x=='ALL' else '$'+x} at about {fp(px)} USDT.",[[_B("✅ Confirm",f"m:bx:{c}:{x}"),_B("❌ Cancel","m:menu")]])
  elif a=="bx":handle(f"/buy {p[2]} {p[3]}",0)
  elif a=="sell":
   cs=ui_coins("sell")
   if not cs:ui_send("💸 *SELL*\nNo bot-tracked positions. If you bought manually, tap Sync first.",[[_B("🔄 Sync manual coins","m:r:sync")],_MENU_BTN])
   else:ui_send("💸 *SELL* — choose a position:",_grid([_B(c,f"m:s:{c}") for c in cs],3)+[_MENU_BTN])
  elif a=="s":
   c=p[2];fb=B('free',c);px=live(f"{c}-USDT") or 0
   ui_send(f"💸 *SELL {c}*\nYou hold {fb:.8g} (about ${fb*px:.2f}).\nHow much?",[[_B(f"{x}%",f"m:sc:{c}:{x}") for x in (25,50,75,100)],[_B("⬅️ Back","m:sell")]])
  elif a=="sc":
   c,x=p[2],int(p[3]);fb=B('free',c);px=live(f"{c}-USDT") or 0
   ui_send(f"⚠️ *CONFIRM SELL*\n{x}% of {c} (about ${fb*px*x/100:.2f}) at market.",[[_B("✅ Confirm",f"m:sx:{c}:{x}"),_B("❌ Cancel","m:menu")]])
  elif a=="sx":
   c,x=p[2],int(p[3])
   if x>=100:handle(f"/sell {c} ALL",0)
   else:
    fb=B('free',c);px=live(f"{c}-USDT") or 0;handle(f"/sell {c} {fb*px*x/100:.2f}",0)
  elif a=="an":
   cs=ui_coins("analyse")
   ui_send("🔎 *ANALYSE A COIN* — choose:",[[_B("🏆 Top 3 now","m:r:analyse")]]+_grid([_B(c,f"m:a:{c}") for c in cs],3)+[_MENU_BTN])
  elif a=="a":handle(f"/analyse {p[2]}",0)
  elif a=="al":
   cs=ui_coins("alert");al=[x.get("coin") for x in _alerts()]
   rows=[[_B("📋 My alerts","m:r:alerts"),_B("🗑 Cancel all","m:r:cancelalerts")]]
   rows+=_grid([_B(f"🗑 {c}",f"m:ad:{c}") for c in dict.fromkeys(al)],3)
   ui_send("🔔 *PRICE ALERT* — choose a coin to set an alert on:",rows+_grid([_B(c,f"m:a2:{c}") for c in cs],3)+[_MENU_BTN])
  elif a=="a2":
   c=p[2];px=live(f"{c}-USDT")
   ui_send(f"🔔 *ALERT {c}*\nNow {fp(px)} USDT. Alert me when price is:",[[_B(f"▲ +{x}%",f"m:ax:{c}:{x}") for x in (2,5,10)],[_B(f"▼ -{x}%",f"m:ax:{c}:-{x}") for x in (2,5,10)],[_B("⬅️ Back","m:al")]])
  elif a=="ax":
   c,x=p[2],float(p[3]);px=live(f"{c}-USDT")
   if not px:tg(f"❌ No price for {c}.")
   else:handle(f"/alert {c} {px*(1+x/100):.8g}",0)
  elif a=="ad":handle(f"/alert del {p[2]}",0)
  elif a=="set":ui_settings_page(0)
  elif a=="sp":ui_settings_page(int(p[2]))
  elif a=="k":ui_setting(p[2])
  elif a=="kv":
   k,d=p[2],p[3];v=cfg().get(k)
   if isinstance(v,bool):new=not v
   elif isinstance(v,int):new=v+(1 if d=="+" else -1)
   else:
    step=max(abs(v)*0.1,0.1);new=round(v+(step if d=="+" else -step),4)
   handle(f"/set {k} {new}",0);ui_setting(k)
  elif a=="lay" and len(p)>2 and p[2]=="rn":
   ks=["menu"]+[x[0] for x in LAYOUT_ITEMS]
   ui_send("✏️ *RENAME*\nChoose the button to rename.",[[_B("✏️ "+lbl(k),f"m:lay:rk:{k}") for k in ks[i:i+2]] for i in range(0,len(ks),2)]+[[_B("↩️ Restore all names","m:lay:rnreset")],[_B("⬅️ Layout","m:lay")]])
  elif a=="lay" and len(p)>3 and p[2]=="rk":
   st=load(ST,{});st["rename_pending"]={"key":p[3],"t":time.time()};save(ST,st)
   ui_send("✏️ *RENAME*\nSend the new name as a message (1-20 characters, emoji allowed).\nSend `reset` to restore the original name or `cancel` to stop.",[[_B("⬅️ Cancel","m:lay")]])
  elif a=="lay" and len(p)>2 and p[2]=="rnreset":
   st=load(ST,{});st.pop("layout_names",None);st.pop("rename_pending",None);save(ST,st);_UI["lay_note"]="\n✅ All names restored.";ui_cb("m:lay")
  elif a=="lay":
   b=layout_bottom();note=""
   if len(p)>2 and p[2]=="reset":b=list(LAYOUT_DEFAULT);note="\n✅ Layout reset."
   elif len(p)>3 and p[2]=="t":
    k=p[3]
    if k in b:b.remove(k)
    elif len(b)>=LAYOUT_MAX:note=f"\n⚠️ Bottom is full ({LAYOUT_MAX+1} keys with Menu). Move one back to the list first."
    else:b.append(k)
   try:
    st=load(ST,{});st["layout_bottom"]=b;save(ST,st)
   except Exception as e:print(f"LAYOUT SAVE ERR: {e}")
   _kb_refresh()
   note+=_UI.pop("lay_note","")
   rows=[[_B(("⌨️ " if k in b else "📋 ")+lbl(k),f"m:lay:t:{k}") for k,l,_ in LAYOUT_ITEMS[i:i+2]] for i in range(0,len(LAYOUT_ITEMS),2)]
   ui_send(f"🧩 *LAYOUT*\nTap a button to move it.\n⌨️ = bottom key (below the text box)\n📋 = Menu list\nBottom keys: {len(b)+1}/{LAYOUT_MAX+1} (Menu always stays){note}",rows+[[_B("✏️ Rename buttons","m:lay:rn"),_B("↩️ Reset layout","m:lay:reset")],[_B("⬅️ Tools","m:tools")]])
  elif a=="tools":
   ui_send("🧰 *TOOLS*\nChoose a tool. Existing commands and functions remain available.",[
              [_B("🛠️ Audit","m:r:audit"),_B("🧾 Scan History","m:scan_hist")],
              [_B("❌ Cancel Orders","m:r:cancel"),_B("🔔 Alerts","m:r:alerts")],
              [_B("🧹 Dust → BGB","m:r:dust"),_B("🛒 Buy","m:buy")],
              [_B("💸 Sell","m:sell"),_B("🔔 Price Alert","m:al")],
              [_B("📈 Gainers","m:r:gain"),_B("📉 Losers","m:r:lose")],
              [_B("📊 Dashboard","m:dash"),_B("🧩 Layout","m:lay")],
              [_B("⬅️ Main Menu","m:menu")]
            ])
  elif a=="dash":
   ui_send("📊 *DASHBOARD*\nChoose a dashboard screen.",[
              [_B("📊 Market","m:r:market"),_B("📈 Performance","m:r:stats")],
              [_B("🧾 Trade History","m:r:history"),_B("🛡️ Risk & Bot","m:r:bot")],
              [_B("⬅️ Tools","m:tools"),_B("🏠 Main Menu","m:menu")]
            ])
  elif a=="scan_hist":
   cmd_scan_history("/scan_history")
  elif a=="r" and len(p)>2 and p[2] in ("gain","lose"):
   tg(gl(10,p[2][0]))
  elif a=="r" and len(p)>2 and p[2]=="market":
   ui_send(gl(10),[[_B("⬅️ Dashboard","m:menu"),_B("🔄 Refresh","m:r:market")]])
  elif a=="r" and len(p)>2 and p[2]=="history":
   H=load(HIST);lines=["🧾 *TRADE HISTORY*"]
   for x in H[-12:]:lines.append(f"{str(x.get('sym','?')).replace('-USDT','')} | {x.get('reason','?')} | PnL ${float(x.get('pnl',0)):+.2f}")
   ui_send("\n".join(lines),[[_B("⬅️ Dashboard","m:menu")]])
  elif a=="r" and len(p)>2 and p[2]=="cancel":
   cmd_cancel_all()
  elif a=="ai" and len(p)>2 and p[2] in AI_Q:ask_ai(AI_Q[p[2]])
 except Exception as e:print(f"UI ERR {t}: {e}");tg(f"⚠️ Button failed, nothing changed: `{str(e)[:150]}`")
def handle(t,age):
 if t.startswith("m:"):ui_cb(t);return
 if not t.startswith(("/","scan_history")):
  try:
   _pd=load(ST,{}).get("rename_pending")
   if _pd:rename_apply(t);return
  except Exception as e:print(f"RENAME ERR: {e}")
 l=t.lower()
 if l.startswith("/analyse "):analyse_coin(t)
 elif l in ("/analyse","🏆 analyse","🏆 analyse (top 3)"):analyse_top3()
 elif l.startswith("/ai "):ask_ai(t[4:].strip())
 elif l=="/ai":ask_ai("Give me a market overview.")
 elif l in MSG:tg(MSG[l])
 elif l in ("/tools","🧰 tools"):
  ui_cb("m:tools")
 elif l in ("/menu","📊 menu","📋 menu","/start"):
  menu()
 elif l in ("/clear","/wipe","🧹 clear chat"):cmd_clear()
 elif l=="/dust cancel":tg("❌ Dust conversion cancelled.")
 elif l in ("/dust","/dust confirm"):cmd_dust(l)
 elif l=="/stats":cmd_stats()
 elif l=="/alerts":cmd_alerts()
 elif l in ("/cancel_alerts","/cancelalerts","/alerts_cancel"):cmd_cancel_alerts()
 elif l.startswith("/alert"):cmd_alert(t)
 elif l=="/scan":tg("🔍 Scanning the market now...");scan(True)
 elif l=="scan_history":cmd_scan_history("/scan_history")
 elif l.startswith("scan_history:"):
  try:
   payload=t.split(":",1)[1];pg,sep,qry=payload.partition(":")
   cmd_scan_history("/scan_history page="+str(max(1,int(pg)))+(" "+qry if sep and qry else ""))
  except (ValueError,IndexError):cmd_scan_history("/scan_history")
 elif l in ("/panic","🚨 panic"):panic_prompt()
 elif l=="panic_cancel":tg("👍 Panic cancelled.")
 elif l=="panic_confirm":panic_sell() if age<=900 else tg("⌛ That confirmation expired.")
 elif l=="/sync":cmd_sync()
 elif l in ("/cancel","❌ cancel"):cmd_cancel_all()
 elif l=="/audit":
  r=audit_system()
  if r and r.startswith("✅"):tg(r)
 elif l=="trail_on":setc('trail_pct',8.0,"📈 Trailing Stop-Loss ON (8%)");menu()
 elif l=="trail_off":setc('trail_pct',0.0,"📉 Trailing Stop-Loss OFF");menu()
 elif l in ("/holdings","/portfolio","💼 holdings","💰 balance","/balance"):tg(get_holdings_text())
 elif l=="/scan_history" or l.startswith("/scan_history "):cmd_scan_history(t)
 elif l in ("/status","/bot","🤖 bot"):cmd_status()
 elif l.startswith("/buy"):cmd_buy(t)
 elif l.startswith("/sell"):cmd_sell(t)
 elif l.startswith("/set"):cmd_set(t)
 elif l=="/pause":setc('auto_buy_enabled',False,"⏸ Auto-buy paused.")
 elif l in ("/resume","▶️ resume"):setc('auto_buy_enabled',True,"▶️ Auto-buy resumed.")
 elif l in ("🧠 ask ai", "ai_info"):_send(MSG["ai_info"]+"\n\nOr tap a question:","Markdown",{"inline_keyboard":[[_B("📈 Market overview","m:ai:1"),_B("💼 Review my holdings","m:ai:2")],[_B("🏆 Best coin to buy","m:ai:3"),_B("⚠️ Biggest risk","m:ai:4")]]})
_TG_N={"n":0}
def tg_check(poll=0):
 st=load(ST);lid=st.get("last_update_id",0)
 try:
  res=requests.get(f"{API}/getUpdates?offset={lid+1}&timeout={int(poll)}",timeout=10+int(poll)).json()
  if not (res.get("ok") and res.get("result")):return
  top=max(u["update_id"] for u in res["result"]);st["last_update_id"]=top;save(ST,st)
  try:requests.get(f"{API}/getUpdates?offset={top+1}&limit=1&timeout=0",timeout=10)
  except:pass
  for u in res["result"]:
   txt=cid="";age=0
   if "callback_query" in u:
    cb=u["callback_query"];txt=cb.get("data","");m=cb.get("message") or {}
    try:requests.post(f"{API}/answerCallbackQuery",json={"callback_query_id":cb["id"]},timeout=10)
    except:pass
   elif "text" in u.get("message",{}):m=u["message"];txt=m["text"].strip()
   if not txt:continue
   cid=str(m.get("chat",{}).get("id",""));age=time.time()-m.get("date",time.time())
   if cid!=CHAT:continue
   tapped=False
   if "callback_query" not in u and txt.lower() in bottom_map():_del_msg(m.get("message_id"));tapped=True;txt=bottom_map()[txt.lower()]
   elif "callback_query" not in u and m.get("message_id"):
    try:o=load(OUT,[]);o.append({"message_id":m["message_id"],"sent_at":time.time()});save(OUT,o)
    except Exception:pass
   if age>1800:continue
   _UI["mid"]=m.get("message_id") if "callback_query" in u else None
   if "callback_query" in u and (txt.startswith("m:") or txt.startswith("scan_history:")) and not _mid("panel_mid"):
    _set_mid("panel_mid",m.get("message_id"))
   _TG_N["n"]+=1
   if ("callback_query" in u or tapped) and (txt.startswith("m:") or txt.startswith("scan_history:")):_UI["edit_mode"]=True
   _UI["panel_used"]=False;label="";_UI["acc"]=() if (("callback_query" in u) or tapped) else None
   if "callback_query" in u:
    try:
     for _r in ((m.get("reply_markup") or {}).get("inline_keyboard") or []):
      for _b in _r:
       if _b.get("callback_data")==txt:label=str(_b.get("text","")).replace("✅ ","",1)
    except Exception:label=""
   try:handle(txt,age)
   except Exception as e:print(f"HANDLER ERR: {e}");tg(f"⚠️ Command failed: `{e}`")
   finally:_UI["edit_mode"]=False;_UI["acc"]=None
   if ("callback_query" in u or tapped) and not _UI.get("panel_used"):
    try:
     _hide_panel(m.get("message_id"))
    except Exception as e:print(f"PANEL ERR: {e}")
 except Exception as e:print(f"TG Err:{e}")
def acquire_lock():
 if fcntl is None:return True
 f=open(LOCK,"w")
 try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);return f
 except OSError:return None
def git_sync():
 ident="trading-bot";mail="bot@users.noreply.github.com"
 env={**os.environ,"GIT_AUTHOR_NAME":ident,"GIT_AUTHOR_EMAIL":mail,"GIT_COMMITTER_NAME":ident,"GIT_COMMITTER_EMAIL":mail,"GIT_TERMINAL_PROMPT":"0"}
 def run(args):return subprocess.run(args,capture_output=True,text=True,timeout=60,env=env)
 def notify_fail(message):
  print(message)
  if not cd("gitfail",900):tg("⚠️ *STATE NOT SAVED:* "+message[:220])
 def abort_rebase():
  p=run(["git","rev-parse","--verify","-q","REBASE_HEAD"])
  if p.returncode==0:
   a=run(["git","rebase","--abort"])
   if a.returncode!=0:raise RuntimeError("Could not abort rebase: "+a.stderr[-250:])
 try:
  abort_rebase();existing=[f for f in FILES if os.path.exists(f)]
  if existing:
   a=run(["git","add","--"]+existing)
   if a.returncode!=0:notify_fail("git add failed: "+a.stderr[-250:]);return
   staged=run(["git","diff","--cached","--quiet"])
   if staged.returncode==1:
    c=run(["git","commit","-m","Persist bot state"])
    if c.returncode!=0:notify_fail("git commit failed: "+c.stderr[-250:]);return
   elif staged.returncode not in (0,):notify_fail("could not inspect staged: "+staged.stderr[-250:]);return
  p=run(["git","pull","--rebase","--autostash"])
  if p.returncode!=0:
   abort=run(["git","rebase","--abort"])
   if abort.returncode!=0:notify_fail("rebase failed and could not abort: "+abort.stderr[-250:]);return
   m=run(["git","pull","--no-rebase","--no-edit","-s","recursive","-X","ours"])
   if m.returncode!=0:run(["git","merge","--abort"]);notify_fail("rebase conflicted; merge failed: "+m.stderr[-250:]);return
   tg("ℹ️ Git sync used conflict recovery.")
  push=run(["git","push"])
  if push.returncode!=0:notify_fail("git push failed: "+push.stderr[-250:]);return
 except Exception as e:notify_fail("git sync exception: "+str(e))
def run_step(fn):
 try:fn()
 except Exception as e:
  nm=getattr(fn,"__name__","step");print(f"STEP {nm} FAILED: {e}\n{traceback.format_exc()[-400:]}")
  try:
   if not in_cd(f"stepfail_{nm}",1800):mk_cd(f"stepfail_{nm}");tg(f"⚠️ *{nm} failed; the other steps (including the scan) continue:* `{e}`")
  except Exception:pass
def live_loop(t0):
 window=float(cfg().get('live_mode_seconds',270) or 0)
 if window<=0:return
 import signal
 def _stop(sig,frm):
  try:git_sync()
  except Exception:pass
  os._exit(0)
 for sg in (signal.SIGTERM,signal.SIGINT):
  try:signal.signal(sg,_stop)
  except Exception:pass
 deadline=t0+window;last_light=last_heavy=last_sync=time.time();pending=False
 while deadline-time.time()>8:
  it=time.time();_C.clear();n0=_TG_N["n"]
  try:tg_check(poll=int(min(20,max(1,deadline-time.time()-6))))
  except Exception as e:print(f"LIVE POLL ERR: {e}")
  now=time.time()
  if now-last_light>=60:
   for fn in (manage,check_alerts,scan,exec_q):
    if fn is scan and deadline-time.time()<180:continue
    run_step(fn)
   last_light=time.time()
  if now-last_heavy>=300:
   for fn in (auto_clear_daily,check_hourly_balance,check_daily_summary):run_step(fn)
   last_heavy=time.time()
  if _TG_N["n"]>n0:pending=True
  if pending and time.time()-last_sync>=15:run_step(git_sync);last_sync=time.time();pending=False
  if time.time()-it<1:time.sleep(1)
if __name__=="__main__":
 lk=acquire_lock()
 if lk is None:print("Another run is in progress; exiting.");sys.exit(0)
 T0=time.time()
 try:
  _st=load(ST,{})
  if not _st.get("display_reset_v4"):
   for _k in ("display_message_id","out_mid","panel_mid","kb_mid"):
    _del_msg(_st.get(_k));_st.pop(_k,None)
   for _k in ("panel_kb","kb_map","panel_ph","panel_text","kb_ph"):_st.pop(_k,None)
   _st["display_reset_v4"]=True;save(ST,_st)
 except Exception as e:print(f"DISPLAY RESET ERR: {e}")
 try:
  for fn in (heartbeat,_C.clear,cleanup_telegram_output,_kb_refresh,auto_clear_daily,scan_history_state,audit_system,tg_check,manage,track_day_equity,git_sync,check_hourly_balance,check_alerts,check_daily_summary,scan,exec_q):run_step(fn)
  live_loop(T0)
  run_step(_kb_refresh)
 except Exception as e:
  try:tg(f"🚨 *CRASH:* `{e}`\n`{traceback.format_exc()[-200:]}`")
  except:pass
 git_sync();ping_watchdog()
