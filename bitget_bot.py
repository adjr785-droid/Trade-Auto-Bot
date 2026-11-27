# ============================== FILE: main.py ==============================
"""Single-command launcher for the modular Bitget bot."""
from bitget_bot.bootstrap import run

if __name__ == "__main__":
    run()

# ============================== FILE: bitget_bot/bootstrap.py ==============================
"""Load the legacy-compatible components in their original source order.

The components intentionally share one execution namespace. This preserves the bot's
existing global state, CCXT client, SQLite helpers, Telegram callback wrapper order,
and late-bound function references during this staged modular migration.
"""
from pathlib import Path

COMPONENTS = [
    "01_bootstrap_config.py",
    "02_alerts_and_indicators.py",
    "03_execution_and_orders.py",
    "04_portfolio_and_coin_analysis.py",
    "05_ai_advisor.py",
    "06_risk_management.py",
    "07_scan_history_and_database.py",
    "08_scanner.py",
    "09_telegram_interface.py",
    "10_process_runtime.py",
]

def run():
    root = Path(__file__).resolve().parent / "components"
    namespace = {"__name__": "__main__", "__file__": str(root / COMPONENTS[0])}
    for filename in COMPONENTS:
        path = root / filename
        namespace["__file__"] = str(path)
        source = path.read_text(encoding="utf-8")
        exec(compile(source, str(path), "exec"), namespace, namespace)

# ============================== FILE: bitget_bot/__init__.py ==============================
"""Modular Bitget trading bot package."""

# ============================== FILE: bitget_bot/components/01_bootstrap_config.py ==============================
# Imports, exchange setup, configuration, Telegram presentation helpers
import requests,json,os,time,ccxt,subprocess,traceback,re,datetime,sys
try:import fcntl
except ImportError:fcntl=None
TK=os.environ.get('TG_TOKEN');CHAT=str(os.environ.get('TG_CHAT_ID',''));API=f"https://api.telegram.org/bot{TK}"
DB,ST,CFG,QF,LF,CD,SC,HIST,LOCK,SH,OUT,AL,ATHATL_CACHE="positions.json","tg_state.json","settings.json","queue.json","profit_ledger.json",".cmd_cooldown.json","scan_state.json","trade_history.json",".bot.lock","scan_history.json","telegram_output.json","alerts.json","ath_atl_cache.json"
COINDB="coins.db";FAV="favourites.json";NEWLIST="new_listing_state.json";GUARDIAN="bot_guardian.json";AI_RECENT="ai_recent.json";GCHARTER="core_charter.json";GAUDIT="guardian_audit.json";JOURNAL="trade_transaction_journal.json";BREAKER="trade_circuit_breaker.json";AI_HEALTH="ai_health.json"
FILES=[COINDB,FAV,NEWLIST,DB,ST,CFG,QF,LF,CD,SC,HIST,SH,OUT,AL,ATHATL_CACHE,JOURNAL,BREAKER,GUARDIAN,GCHARTER,GAUDIT,AI_HEALTH,AI_RECENT]
STABLE=['USDC','USDT','DAI','FDUSD','TUSD','USDD','USDE'];LEV=['3L','3S','5L','5S','10L','10S','2L','2S'];BL=['RPANW','CT','ONE']
ex=ccxt.bitget({'apiKey':os.environ.get('BITGET_API_KEY'),'secret':os.environ.get('BITGET_API_SECRET'),'password':os.environ.get('BITGET_API_PASSPHRASE'),'enableRateLimit':True});ex.options['createMarketBuyOrderRequiresPrice']=False
_RL={"ccxt":[],"groq":[]}
def rl_note(k):
 # Rate-limit monitor: remember each rate-limit hit (last hour) for the System report and for self-slowing.
 now=time.time();_RL[k]=[x for x in _RL.get(k,[]) if now-x<3600]+[now]
def rl_count(k,sec=3600):return sum(1 for x in _RL.get(k,[]) if time.time()-x<sec)
def rl_workers(w):
 # 3 or more exchange rate-limit hits in 10 minutes: halve the parallel scan workers until it calms down
 w=max(1,int(w or 8));return max(2,w//2) if rl_count("ccxt",600)>=3 else w
_RL_EXC=tuple(getattr(ccxt,n) for n in ('RateLimitExceeded','DDoSProtection') if hasattr(ccxt,n))
_rl_orig_request=getattr(ex,'request',None)
def _rl_request(*a,**k):
 try:return _rl_orig_request(*a,**k)
 except _RL_EXC:
  rl_note("ccxt");raise
if _rl_orig_request:ex.request=_rl_request
GROQ_API_KEY=os.environ.get('GROQ_API_KEY','').strip()
GROQ_MODEL=os.environ.get('GROQ_MODEL','openai/gpt-oss-120b').strip() or 'openai/gpt-oss-120b'
_AI_CACHE={}
_AI_LAST={}

def ai_emergency_state():
 hs=load(AI_HEALTH,{})
 return hs if isinstance(hs,dict) else {}

def ai_emergency_active():
 return bool(ai_emergency_state().get("emergency_mode"))

def activate_ai_emergency(reason):
 """Pause automated buying on AI health failure; preserve manual trade controls and protective exits."""
 hs=ai_emergency_state();already=bool(hs.get("emergency_mode"))
 _lost=int(hs.get("consecutive_successes",0)) if already else 0
 hs.update({"emergency_mode":True,"trading_mode":"AUTO_BUY_PAUSED_MANUAL_OPEN","emergency_started":hs.get("emergency_started") or time.time(),
  "emergency_reason":str(reason)[:240],"consecutive_successes":0,"resume_requires_user_confirmation":False})
 save(AI_HEALTH,hs)
 if already and _lost>0 and not str(reason).startswith("Groq health self-test"):tg(f"⚠️ *AUDIT — AI RECOVERY RESET*\nFailed at stage {_lost}/3 ({str(reason)[:100]}). Restarting from 0/3.")
 if not already:
  tg("🚨 *AI FAILURE*\nAI notes and reviews are paused. Buying, selling and all stops keep running on the rules. The audit is recovering the AI (3 checks).\nReason: "+str(reason)[:160])

def ai_sell_reason_allowed(reason):
 """Allow only the bot's predefined, deterministic exit conditions in emergency mode."""
 r=str(reason or "").strip().upper()
 prefixes=("SL HIT", "STOP LOSS", "TRAILING STOP", "RSI ", "CRASH GUARD",
  "PARTIAL PROFIT", "UNPROTECTED", "PANIC")
 return any(r.startswith(x) for x in prefixes)

def ai_emergency_resume(auto=False):
 hs=ai_emergency_state()
 if not hs.get("emergency_mode"):
  tg("ℹ️ AI emergency mode is not active.");return
 if hs.get("status")!="healthy" or int(hs.get("consecutive_successes",0))<3:
  tg("⛔ Resume denied. AI must pass 3 consecutive live health checks first. Current successful checks: "+str(hs.get("consecutive_successes",0))+".");return
 hs.update({"emergency_mode":False,"trading_mode":"NORMAL","emergency_ended":time.time(),"resume_requires_user_confirmation":False})
 save(AI_HEALTH,hs)
 tg("✅ *AUDIT — AI RECOVERY VERIFIED*\n3 checks passed. AI is back to normal"+("." if auto else " (you confirmed)."))
_C={};_BAL={"t":0,"m":""};LAYOUT_ITEMS=[("tools","🧰 Tools","m:tools"),("analyse","🏆 Analyse","/analyse"),("bot","🖥 System","/bot"),("stats","📈 Stats","/stats"),("an","🔎 Analyse Coin","m:an"),("set","⚙️ Settings","m:set"),("hold","💼 Holdings","m:r:holdings"),("hist","📜 Trade History","m:r:history"),("ai","🧠 Ask AI","m:ai:1"),("clear","🧹 Clear Chat","/clear"),("panic","🚨 PANIC SELL","/panic")]
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
_FREE={"t":0,"v":None}
def usdt_line():
 # "USDT33.75": free USDT, shown at the top of every Analyse page (cached 15s)
 if time.time()-_FREE["t"]<15 and _FREE["v"] is not None:return f"USDT{_FREE['v']:.2f}"
 try:
  _FREE["v"]=float((ex.fetch_balance().get("free") or {}).get("USDT",0) or 0);_FREE["t"]=time.time()
 except Exception:pass
 return f"USDT{_FREE['v']:.2f}" if _FREE["v"] is not None else ""
def _ub_inject(m):
 # one USDT line per command, only inside Analyse pages
 if not _UI.get("ub") or _UI.get("ub_done") or not isinstance(m,str):return m
 _UI["ub_done"]=True;u=usdt_line()
 return (u+"\n"+m) if u else m
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
 mid=_create(m,mode,None,silent=True)
 if mid:
  st=load(ST,{});st["out_mid"]=mid;st["out_text"]=(m or "")[:3800];st["out_mode"]=mode or "Markdown";save(ST,st)
 return mid
def _panel(text,rows,touch=True):
 text=_ub_inject(text)
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
 if old:_del_msg(old)
 nm=_create("💰 "+(ph or "Balance"),None,rm,silent=True)
 if nm:
  st=load(ST,{});st["kb_mid"]=nm;st["kb_ph"]=ph;st["kb_keys"]=str(rm["keyboard"]);save(ST,st)
def _kb_refresh():
 st=load(ST,{});keys=str(kb_markup()["keyboard"]);ph=(compact_balance() or "")[:64]
 if not st.get("kb_mid") or st.get("kb_keys")!=keys or (ph and ph!=st.get("kb_ph")):_carrier(ph)
IMPORTANT=("*SOLD","*BOUGHT","*BUY FAILED","*SELL FAILED","*SELL NOT VERIFIED","*SL NOT VERIFIED","*PANIC","*CRASH","*STATE NOT SAVED","*PARTIAL PROFIT","*DCA ADDED","*EXECUTED","*ALERT:*","*RISK BREAKER","*QUEUE FAILED","*STOP")
REFRESH=("/bot","/stats","m:r:holdings")
def _mark_pnl(t):
 out=[]
 for ln in t.split("\n"):
  if "PnL" in ln and "🟢" not in ln and "🔴" not in ln:
   m=re.search(r'PnL:?\s*\$?\s*([+-]?\d+(?:\.\d+)?)',ln)
   if m:ln=("🟢 " if float(m.group(1))>=0 else "🔴 ")+ln
  out.append(ln)
 return "\n".join(out)
def _ring(m,mode):
 mid=_create(m,mode,None,silent=False)
 if not mid:_create(m,None,None,silent=False)
def stale_output():
 mins=float(cfg().get('output_stale_minutes',5) or 0)
 st=load(ST,{})
 if mins<=0 or not st.get("out_mid") or not st.get("out_time"):return
 if time.time()-float(st["out_time"])<mins*60:return
 # Expired command output is removed, not left as an obsolete actionable result.
 _del_msg(st.get("out_mid"));_del_msg(st.get("panel_mid"))
 st=load(ST,{})
 for k in ("out_mid","out_text","out_mode","out_time","out_stale","panel_mid","panel_rows","panel_text","pages"):
  st.pop(k,None)
 save(ST,st)

def _dw(t):
 return sum(2 if ord(c)>0x2000 else 1 for c in t)
def _vis(ln,W):
 return max(1,-(-_dw(ln)//W))
def _wrap(text,W):
 out=[]
 for ln in text.split("\n"):
  if _dw(ln)<=W:out.append(ln);continue
  cur="";
  for w in ln.split(" "):
   while _dw(w)>W:
    if cur:out.append(cur);cur=""
    k=0;acc=0
    while k<len(w) and acc+(2 if ord(w[k])>0x2000 else 1)<=W:acc+=2 if ord(w[k])>0x2000 else 1;k+=1
    out.append(w[:k]);w=w[k:]
   if not cur:cur=w
   elif _dw(cur)+1+_dw(w)<=W:cur+=" "+w
   else:out.append(cur);cur=w
  out.append(cur)
 return "\n".join(out)
def _pad(t,W,L):
 v=sum(_vis(x,W) for x in t.split("\n"))
 return t+"\n"+"\n".join(["⠀"]*(L-v)) if v<L else t
def _paginate(text,W,L):
 cap=max(2,L-2);pages=[];cur=[];used=[0];fence=[False]
 def flush():
  if not cur:return
  t="\n".join(cur)
  if fence[0]:t="```\n"+t
  fence[0]=t.count("```")%2==1
  if fence[0]:t+="\n```"
  pages.append(t);cur.clear();used[0]=0
 for ln in text.split("\n"):
  parts=[ln[i:i+W*cap] for i in range(0,len(ln),W*cap)] if _vis(ln,W)>cap else [ln]
  for pt in parts:
   v=_vis(pt,W)
   if cur and used[0]+v>cap:flush()
   cur.append(pt);used[0]+=v
 flush()
 return pages or [""]
def _nav_rows(i,k):
 b=lambda t,c:{"text":t,"callback_data":c}
 return [[b("◀","pg:p"),b(f"{i+1}/{k}","pg:x"),b("▶","pg:n"),b("🏠","m:menu")]]
def _nav_with(nav,flat):
 # Buttons keep the row layout they were built with (several in one line); the refresh button joins the page row.
 rows=[r if isinstance(r,list) else [r] for r in (flat or [])]
 for r in rows:
  r=[x for x in r if x.get("callback_data") not in ("m:menu","/menu")]
  if not r:continue
  if len(r)==1 and r[0].get("callback_data")=="rf:go":nav[0].append(r[0])
  else:nav.append(r)
 return nav
def page_nav(t):
 st=load(ST,{});pg=st.get("pages") or {};ps=pg.get("p") or []
 if len(ps)<2:_hide_panel();return
 i=int(pg.get("i",0));d=t.split(":")[1] if ":" in t else "x"
 i=max(0,i-1) if d=="p" else min(len(ps)-1,i+1) if d=="n" else 0
 st["pages"]["i"]=i
 if pg.get("cmd"):st.setdefault("page_mem",{})[pg["cmd"]]=[i,time.time()]
 save(ST,st)
 out=_mid("out_mid")
 if not (out and _edit(out,ps[i],pg.get("mode") or "Markdown",None)):_make_out(ps[i],pg.get("mode") or "Markdown")
 nav=_nav_with(_nav_rows(i,len(ps)),pg.get("x") or [])
 _panel(f"📄 *Page {i+1}/{len(ps)}*",nav)
def _send(m,mode,btn=None):
 if mode in (None,"Markdown"):m=_ub_inject(m)
 try:
  n=int(cfg().get('page_chars',600) or 0)
  acc=_UI.get("acc")
  if acc is None and mode in (None,"Markdown") and any(k in (m or "") for k in IMPORTANT):_ring(m,mode);return
  if acc is not None:
   pm,pt=acc if acc else (None,"")
   if pt and len(pt)>=120 and pm==mode and len(pt)+len(m)+2<=(12000 if n>0 else 4000):m=pt+"\n\n"+m
   _UI["acc"]=(mode,m)
  if mode in (None,"Markdown"):m=_mark_pnl(m)
  cmd=_UI.get("cmd") if acc is not None else None
  L=int(cfg().get('page_lines',14) or 0);W=max(10,int(cfg().get('page_width',32) or 32))
  ps=[_pad(x,W,L) for x in _paginate(_wrap(m,W),W,L)] if (mode in (None,"Markdown") and n>0 and L>0) else [m]
  had=bool((load(ST,{}).get("pages") or {}).get("p"))
  rf=cmd in REFRESH
  i0=0
  if len(ps)>1 and cmd:
   mem=(load(ST,{}).get("page_mem") or {}).get(cmd)
   if mem and time.time()-float(mem[1])<600 and int(mem[0])<len(ps):i0=int(mem[0])
  show=ps[i0]
  out=_mid("out_mid");had_panel=bool(_mid("panel_mid"))
  fresh=bool(_UI.get("fresh")) and acc is not None;_UI["fresh"]=False
  recreated=False
  if fresh or not (out and _edit(out,show,mode or "Markdown",None)):_make_out(show,mode);recreated=True
  rows=(btn.get("inline_keyboard") if isinstance(btn,dict) else btn) if btn else []
  rb={"text":"🔄","callback_data":"rf:go"}
  extra=([[rb]] if rf else [])+rows
  flat=[[x for x in r if x.get("callback_data") not in ("m:menu","/menu")] for r in extra];flat=[r for r in flat if r]
  try:
   st=load(ST,{});st["out_text"]=(show or "")[:3800];st["out_mode"]=mode or "Markdown";st["out_time"]=time.time();st.pop("out_stale",None)
   if rf:st["last_cmd"]=cmd
   if len(ps)>1:st["pages"]={"p":ps,"i":i0,"mode":mode or "Markdown","x":flat,"cmd":cmd}
   else:st.pop("pages",None)
   save(ST,st)
  except Exception:pass
  if len(ps)>1:
   nav=_nav_with(_nav_rows(i0,len(ps)),flat)
   _panel("📄 *Page %d/%d*"%(i0+1,len(ps)),nav)
  elif rf:_panel("🔄 *Refresh*",[[rb,{"text":"🏠","callback_data":"m:menu"}]+[x for r in rows for x in r if x.get("callback_data")!="m:menu"]])
  elif btn:_panel("👇 *Actions*",rows)
  elif recreated and had_panel:_set_mid("panel_hidden",True)
  elif had and _mid("panel_mid"):_hide_panel()
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
  if now-float(item.get("sent_at",now))<300:keep.append(item);continue
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
 btns=[b(lbl(k),d) for k,l,d in LAYOUT_ITEMS if k not in bot_keys and k!='an']
 ui_send("🕹️ *MENU*\nSelect an option below.",[btns[i:i+2] for i in range(0,len(btns),2)])
def panic_prompt():_send("🚨 *CONFIRM PANIC*\nThis sells ALL positions at market.\nConfirm within 15 minutes.","Markdown",{"inline_keyboard":[[{"text":"✅ YES, SELL EVERYTHING","callback_data":"panic_confirm"},{"text":"❌ Cancel","callback_data":"panic_cancel"}]]})
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
 d={"oversold_enabled":True,"pullback_rsi_min":40,"rising_guard":True,"rsi_min":42,"rsi_max":48,"stop_loss_pct":5.0,"auto_buy_enabled":True,"auto_buy_amount_usdt":10.0,"max_tokens_before_consolidation":3,"trail_pct":8.0,"crash_guard_pct":10.0,"rsi_exit":75,"btc_rsi_min":50,"risk_per_trade_pct":0.5,"max_daily_loss_pct":3.0,"max_portfolio_exposure_pct":50.0,"max_position_pct":15.0,"max_daily_trades":5,"max_spread_pct":0.5,"auto_mode_v1":False,"rebuy_cooldown_hours":6.0,"atr_stop_enabled":True,"atr_stop_mult":2.0,"atr_stop_min_pct":3.0,"atr_stop_max_pct":10.0,"market_filter_enabled":True,"btc_trend_hours":50,"min_breadth_pct":30.0,"weak_market_mode":True,"weak_market_floor_pct":15.0,"weak_market_size_factor":0.5,"h1_all_setups":False,"offline_alert_hours":3.0,"daily_summary_hour_utc":20,"max_daily_drawdown_pct":5.0,"dynamic_trail":True,"sell_if_stop_fails":True,"scan_prefilter":True,"cleanup_hours":24.0,"scan_interval_minutes":15.0,"auto_clear_hours":24.0,"live_mode_seconds":270,"mtf_confirm":True,"btc_shock_pct":3.0,"max_open_risk_pct":3.0,"half_or_all_sizing":True,"half_or_all_threshold_usdt":5.0,"fee_pct_side":0.1,"perf_breaker_trades":10,"perf_breaker_hours":24.0,"loss_streak_trigger":2,"loss_streak_size_factor":0.5,"scan_parallel":True,"page_chars":600,"page_lines":12,"page_width":32,"output_stale_minutes":5,"smart_risk_enabled":True,"max_coin_exposure_pct":20.0,"max_sector_exposure_pct":35.0,"liquidity_depth_usdt":5000.0,"max_book_imbalance_pct":85.0,"regime_risk_factor":0.65,"execution_slippage_pct":0.35,"stop_watchdog_enabled":True,"portfolio_stress_pct":12.0,"signal_expiry_minutes":30,"adaptive_risk_enabled":True,"dynamic_auto_buy_enabled":True,"auto_buy_reserve_pct":5.0,"dynamic_auto_buy_max_pct":20.0,"dynamic_auto_buy_min_usdt":1.02,"dynamic_auto_buy_max_usdt":0.0,"guardian_enabled":True,"guardian_audit_interval_minutes":15,"max_portfolio_correlation":0.85,"momentum_1h_enabled":True,"mom_1h_min_pct":0.5,"mom_1h_max_pct":6.0,"max_24h_pct":100.0,"near_miss_alerts":True,"near_miss_rsi_margin":3.0,"near_miss_1h_margin":0.5,"near_miss_cooldown_minutes":60,"near_miss_max_per_scan":3,"scan_workers":8,"radar_max":30,"radar_rsi_margin":10.0,"manual_candidate_min_score":70.0}
 s=load(CFG,d)
 for k,v in d.items():s.setdefault(k,v)
 if float(s.get("manual_candidate_min_score",70.0) or 70.0)!=70.0:
  s["manual_candidate_min_score"]=70.0;save(CFG,s)  # Fixed 7/10 MANUAL threshold; legacy percentage setting normalized.
 for _k in ('max_hold_hours','momentum_watch_minutes','rotation_retry_hours'):
  if _k in s:s.pop(_k,None);save(CFG,s)
 if not s.get('one_h_v1'):s['h1_all_setups']=True;s['one_h_v1']=True;save(CFG,s)
 if not s.get('risk_v3'):s['risk_per_trade_pct']=0.5;s['risk_v3']=True;save(CFG,s)
 if not s.get('knife_v1'):s['risk_per_trade_pct']=0.5;s['oversold_enabled']=False;s['rising_guard']=True;s['knife_v1']=True;save(CFG,s)
 if not s.get('setups_v1'):s['oversold_enabled']=True;s['setups_v1']=True;save(CFG,s)
 if 'partial_tp_pct' in s or 'partial_tp_fraction' in s:s.pop('partial_tp_pct',None);s.pop('partial_tp_fraction',None);save(CFG,s)
 if not s.get('auto_mode_v1'):s['auto_buy_enabled']=True;s['auto_mode_v1']=True;save(CFG,s)
 if not s.get('fixed5_v1'):s['atr_stop_enabled']=False;s['stop_loss_pct']=5.0;s['fixed5_v1']=True;save(CFG,s)   # fixed 5% stop
 if not s.get('auto_dyn_v1'):s['auto_buy_enabled']=True;s['auto_dyn_v1']=True;save(CFG,s)   # Auto-buy is dynamic: no Settings switch; it buys whenever the set rules are met
 return s

# ============================== FILE: bitget_bot/components/02_alerts_and_indicators.py ==============================
# Cooldowns, market filters, indicators, and signal measurements
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
_MKT={"t":0,"r":"","w":""}
def market_filter(s):
 if not s.get('market_filter_enabled',True):return ""
 if time.time()-_MKT["t"]<300:return _MKT["r"]
 reason="";weak=""
 try:
  n=max(10,int(s.get('btc_trend_hours',50)))
  o=ex.fetch_ohlcv("BTC/USDT",'1h',limit=n+10)
  c=[float(k[4]) for k in o[:-1]]
  if len(c)<n:reason="market data unavailable (fail-safe: buying blocked)"
  elif c[-1]<sum(c[-n:])/n:reason=f"BTC is below its {n}h average"
  if not reason:
   ch=[t.get('percentage') for k,t in ex.fetch_tickers().items() if k.endswith('/USDT') and (t.get('quoteVolume') or 0)>=1000000 and t.get('percentage') is not None]
   if len(ch)>=30:
    up=sum(1 for x in ch if x>0)/len(ch)*100
    if up<float(s.get('min_breadth_pct',30.0)):
     msg=f"only {up:.0f}% of coins are up today"
     if s.get('weak_market_mode',True) and up>=float(s.get('weak_market_floor_pct',15.0)):weak=msg
     else:reason=msg
 except Exception as e:print("MARKET FILTER ERR:",e);reason="market data unavailable (fail-safe: buying blocked)";weak=""
 # a failed data read is retried after 60s instead of being cached for 5 minutes
 _MKT["t"],_MKT["r"],_MKT["w"]=(time.time()-240 if reason.startswith("market data unavailable") else time.time()),reason,weak;return reason
def weak_market(s):
 # weak (not crashing) market: buying stays allowed at reduced size; returns the reason text or ""
 if market_filter(s):return ""
 return _MKT.get("w","") if s.get('market_filter_enabled',True) else ""
def setup_range(sym,chg,s,kind):
 # BREAKOUT / BOUNCE: 24h range by default; the 1H window when h1_all_setups is on
 if mom_on(s) and s.get('h1_all_setups',False):return trend_range(sym,chg,s)
 return (0.0<chg<25.0) if kind=="BREAKOUT" else (-30.0<chg<-8.0)
def setup_score(setup,vol,r,ma5,ma10,bvol=None):
 sc=({"BREAKOUT":3,"TREND":2,"PULLBACK":2,"BOUNCE":1}.get(setup,0))*100+min(vol/1e6,100)*0.2
 if ma10:sc+=max(0.0,min(20.0,(ma5-ma10)/ma10*1000))
 sc+=max(0.0,min(10.0,(30-r)/3)) if setup=="BOUNCE" else max(0.0,10-abs(r-55)/2)
 if bvol:sc+=min(float(bvol),5.0)*4
 return round(sc,2)
def cmd_stats():
 H=load(HIST)
 if not isinstance(H,list) or not H:tg("📈 *TRADE STATS*\nNo closed trades yet.");_stage_results_text();return
 H=[x for x in H if isinstance(x,dict)]
 pn=[net_pnl(x) for x in H];wl=[x for x in pn if x>0];ll=[x for x in pn if x<0];g={}
 for x in H:g.setdefault(str(x.get('strategy','OLDER')).upper(),[]).append(net_pnl(x))
 aw=sum(wl)/len(wl) if wl else 0.0;al=sum(ll)/len(ll) if ll else 0.0
 pf=(sum(wl)/abs(sum(ll))) if ll else None;ex_=sum(pn)/len(pn)
 rows=[f"{k}: {len(v)} trades | win {sum(1 for x in v if x>0)/len(v)*100:.0f}% | avg ${sum(v)/len(v):+.2f} | total ${sum(v):+.2f}" for k,v in sorted(g.items(),key=lambda x:-sum(x[1]))]
 tg("\n".join(["📈 *TRADE STATS* (after estimated fees)",f"Closed trades: {len(pn)}",f"Win rate: {len(wl)/len(pn)*100:.0f}% ({len(wl)} wins, {len(ll)} losses)",f"Average win: ${aw:+.2f} | Average loss: ${al:+.2f}",f"Profit factor: {pf:.2f}" if pf is not None else "Profit factor: no losses yet",f"Expectancy per trade: ${ex_:+.2f}",f"Net profit: ${sum(pn):+.2f}",f"Best: ${max(pn):+.2f} | Worst: ${min(pn):+.2f}","","*By strategy*"]+rows))
 _stage_results_text()
def _stage_results_text():
 try:
  res=coin_stage_results()
  fm=lambda v:"n/a" if v is None else f"{v:+.1f}%"
  L=["🔬 *RULE RESULTS* (what coins did after each stage)"]
  for k,ic,nm in (("WATCH","👁","Watch"),("MANUAL","🟡","Manual"),("AUTO","🤖","Auto")):
   r=res.get(k)
   L.append(f"{ic} {nm}: collecting data" if not r else f"{ic} {nm} ({r['n']}): 4h {fm(r['r4'])} | win {r['win4']:.0f}% | up {fm(r['up'])} / down {fm(r['down'])}")
  L.append("Win = beats round-trip fees at 4h. Needs a few days of scans to mean anything.")
  tg("\n".join(L))
 except Exception as e:print("STAGE RESULTS ERR",str(e)[:100])
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
  if len(o)<3:return (None,)*5
  c=[float(k[4]) for k in o[:-1]]
  try:
   f=max(0.0,min(1.0,(time.time()-float(o[-1][0])/1000.0)/3600.0));pm=c[-2]+(c[-1]-c[-2])*f
   _H1[sym]=(float(o[-1][4])/pm-1.0)*100.0 if pm>0 else None
  except Exception:_H1[sym]=None
  if len(o)<50:
   if _H1.get(sym) is not None:_NEWC[sym]=(float(o[-1][4]),_H1[sym],len(o))
   return (None,)*5
  return float(o[-1][4]),c[-1],_rsi(c),round(sum(c[-5:])/5,6),round(sum(c[-10:])/10,6)
 except:return (None,)*5
_PF={};_H1={};_NM=[];_RADAR=[];_NEWC={}
def tk_spread(t):
 try:
  b=float(t.get('bid') or 0);a=float(t.get('ask') or 0)
  return (a-b)/((a+b)/2)*100 if b>0 and a>0 and a>=b else None
 except Exception:return None
def mom_on(s):return bool(s.get('momentum_1h_enabled',True))
def pre_ok(chg,s):
 # candle-fetch prefilter: with the 1H momentum window the 24h change only needs to stay under a loose ceiling
 if mom_on(s):return True  # 1H is the only momentum test; no 24h limits
 return -30.0<chg<-8.0 or 0.0<chg<30.0
def near_miss(sym,chg,r,p,ma5,ma10,s):
 # TREND setup missed by a small margin (RSI window and/or 1H window); trend structure and 24h ceiling must already pass.
 if not (s.get('near_miss_alerts',True) and mom_on(s)):return None
 h=_H1.get(sym)
 if h is None or not pre_ok(chg,s) or not (p>ma5>ma10):return None
 mr=float(s.get('near_miss_rsi_margin',3.0));mh=float(s.get('near_miss_1h_margin',0.5));gaps=[];why=[]
 lo,hi=float(s['rsi_min']),float(s['rsi_max'])
 if r<lo:g=lo-r;gaps.append(g/mr);why.append(f"RSI {r:.1f} is {g:.1f} below {lo:g}")
 elif r>hi:g=r-hi;gaps.append(g/mr);why.append(f"RSI {r:.1f} is {g:.1f} above {hi:g}")
 a,b=float(s.get('mom_1h_min_pct',0.5)),float(s.get('mom_1h_max_pct',6.0))
 if h<a:g=a-h;gaps.append(g/mh);why.append(f"1H {h:+.2f}% is {g:.2f} below {a:g}%")
 elif h>b:g=h-b;gaps.append(g/mh);why.append(f"1H {h:+.2f}% is {g:.2f} above {b:g}%")
 if not gaps or max(gaps)>1.0:return None
 return sum(gaps),"; ".join(why),h
def trend_range(sym,chg,s):
 if not mom_on(s):return 1.0<chg<30.0
 h=_H1.get(sym)
 return h is not None and pre_ok(chg,s) and float(s.get('mom_1h_min_pct',0.5))<=h<=float(s.get('mom_1h_max_pct',6.0))
def data(sym):
 h=_C.get(sym)
 if h and time.time()-h[0]<20:return h[1]
 r=_calc(sym);_C[sym]=(time.time(),r);return r
_BRK_CACHE={};_BRK_RANGE={};_T4H_CACHE={};_BTC_SCORE_CACHE={"t":0,"ok":None}
def breakout_metrics(sym):
 now=time.time();cached=_BRK_CACHE.get(sym)
 if cached and now-cached[0]<900:return cached[1]
 try:
  candles=ex.fetch_ohlcv(S(sym),'1h',limit=25)
  if not isinstance(candles,list) or len(candles)<23:result=(None,None,None)
  else:
   closed=candles[:-1];latest=closed[-1];prior=closed[-21:-1]
   if len(prior)!=20:result=(None,None,None)
   else:
    resistance=max(float(c[2]) for c in prior);close=float(latest[4])
    _BRK_RANGE[sym]=resistance-min(float(c[3]) for c in prior)
    last_volume=float(latest[5] or 0);avg_volume=sum(float(c[5] or 0) for c in prior)/20
    multiple=last_volume/avg_volume if avg_volume>0 else None
    result=(bool(close>resistance),multiple,resistance)
 except Exception:result=(None,None,None)
 _BRK_CACHE[sym]=(now,result);return result
def breakout_confirmation(sym):
 breakout,multiple,resistance=breakout_metrics(sym)
 return bool(breakout is True and multiple is not None and multiple>=1.5),multiple,resistance
def market_trend_4h(sym):
 now=time.time();cached=_T4H_CACHE.get(sym)
 if cached and now-cached[0]<900:return cached[1]
 result=None
 try:
  o=ex.fetch_ohlcv(S(sym),'4h',limit=30)
  if isinstance(o,list) and len(o)>=13:
   closes=[float(k[4]) for k in o[:-1] if isinstance(k,(list,tuple)) and len(k)>4 and k[4] is not None]
   latest=o[-1]
   if len(closes)>=10 and isinstance(latest,(list,tuple)) and len(latest)>4 and latest[4] is not None:
    vals=closes[-10:];price=float(latest[4])
    if price>0 and all(x>0 for x in vals):result=bool(price>=sum(vals)/10)
 except Exception:result=None
 _T4H_CACHE[sym]=(now,result);return result
def btc_market_safety_check(s):
 now=time.time()
 if now-_BTC_SCORE_CACHE["t"]<300 and _BTC_SCORE_CACHE["ok"] is not None:return _BTC_SCORE_CACHE["ok"]
 ok=None
 try:
  o=ex.fetch_ohlcv("BTC/USDT",'1h',limit=65)
  if isinstance(o,list) and len(o)>=55:
   closes=[float(x[4]) for x in o[:-1] if isinstance(x,(list,tuple)) and len(x)>4 and x[4] is not None]
   if len(closes)>=51 and all(x>0 for x in closes[-51:]):
    rsi=_rsi(closes)
    if rsi is not None:
     trend=closes[-1]>=sum(closes[-50:])/50
     shock_limit=float(s.get("btc_shock_pct",3.0) or 0)
     shock_change=(closes[-1]/closes[-2]-1.0)*100 if len(closes)>=2 else None
     no_shock=(shock_change is not None and (shock_limit<=0 or shock_change>-shock_limit))
     ok=bool(rsi>=float(s.get("btc_rsi_min",50.0)) and trend and no_shock)
 except Exception:ok=None
 _BTC_SCORE_CACHE.update(t=now,ok=ok);return ok

# ============================== FILE: bitget_bot/components/03_execution_and_orders.py ==============================
# Order execution, protective stops, buy/sell commands, dust cleanup
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
def partial_stop_frac(pos):
 """Share of the entry amount sold when the fixed stop hits (default 75%). 1.0 = sell all (small positions, or the partial already happened)."""
 try:
  fr=float(cfg().get('stop_exit_fraction',0.75) or 1.0)
  eq=float(pos.get('entry_qty') or 0);eu=float(pos.get('entry_usdt') or 0)
  if fr>=1.0 or pos.get('partial_done') or eq<=0 or eu*fr<1.5 or eu*(1-fr)<5.0:return 1.0
  return fr
 except Exception:return 1.0
def place_trailing_sl(sym,qty):
 pos=load(DB).get(sym,{});ent=pos.get('stop_ref') or pos.get('entry_price') or live(sym)
 if not ent:return False
 fr=partial_stop_frac(pos)
 if fr<1.0:qty=min(float(qty),float(pos.get('entry_qty'))*fr)   # the exchange stop sells only 75% of the entry amount
 px=round(ent*(1-sl_pct(sym,pos)/100),6);ok=hard_sl(sym,qty,px)
 if ok:
  db=load(DB)
  if sym in db:db[sym]['xsl']=px;save(DB,db)
 return ok
def ratchet(sym,d,lvl,lv):
 if lvl<=d.get('xsl',0)*1.01 or lvl>=lv*0.995:return
 q=B('total',sym.split('-')[0])
 if hard_sl(sym,q,lvl):d['xsl']=lvl
 elif place_trailing_sl(sym,q):d['xsl']=round((d.get('stop_ref') or d['entry_price'])*(1-sl_pct(sym,d)/100),6)
def rec(sym,pnl,why,notional=0.0):
 L=load(LF);L['total_realized_usdt']=L.get('total_realized_usdt',0.0)+pnl;L['trade_count']=L.get('trade_count',0)+1;save(LF,L)
 H=load(HIST)
 if not isinstance(H,list):H=[]
 strat=load(DB).get(sym,{}).get('strategy','OTHER')
 fee=round(float(notional or 0)*2*float(cfg().get("fee_pct_side",0.1))/100,6)   # estimated exchange fee, buy + sell
 H.append({"sym":sym,"pnl":pnl,"fee":fee,"time":time.time(),"reason":why,"strategy":strat});save(HIST,H)
 journal_event("trade_closed",symbol=sym,pnl_usdt=pnl,reason=why,strategy=strat)
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
 """Sell requested amount; full exits are not successful until no tradable balance remains."""
 try:
  cs=S(sym);coin=sym.split("-")[0]
  if cancel_sells(sym):tg(f"⚠️ Sale of {sym} stopped: orders could not be safely cancelled.");return False
  time.sleep(0.5);p=live(sym)
  if not p:return False
  full_exit=pct>=0.999
  total_filled=0.0;weighted_value=0.0;attempts=0;remaining=0.0
  max_attempts=4 if full_exit else 1
  while attempts<max_attempts:
   fb=B('free',coin)
   if fb<=0:
    remaining=0.0;break
   q=fb if full_exit else fb*pct
   if q*p<1.02 and not full_exit:
    q=fb;reason+=" (dust→full)"
   q=float(ex.amount_to_precision(cs,q))
   if q<=0:
    remaining=fb;break
   try:
    o=ex.create_market_sell_order(cs,q);oid=(o or {}).get('id')
    filled=float((o or {}).get('filled') or 0);px=float((o or {}).get('average') or (o or {}).get('price') or p)
    if oid and (filled<=0 or not px):
     for _ in range(10):
      time.sleep(0.8)
      try:
       o=ex.fetch_order(oid,cs);filled=float(o.get('filled') or 0);px=float(o.get('average') or o.get('price') or p)
      except Exception:continue
      if filled>0:break
    if filled<=0:
     breaker_failure(f"SELL {sym}: fill not verified");journal_event("sell_fill_unverified",symbol=sym,order_id=oid)
     tg(f"⚠️ *SELL NOT VERIFIED* {sym}: order `{oid}` returned no confirmed fill. Reconcile exchange balance before retrying.");return False
    total_filled+=filled;weighted_value+=filled*px
    journal_event("sell_fill_verified",symbol=sym,filled=filled,average=px,order_id=oid,reason=reason,attempt=attempts+1)
   except Exception as order_error:
    # A failed attempt is not treated as a completed liquidation.
    journal_event("sell_attempt_failed",symbol=sym,attempt=attempts+1,error=str(order_error)[:180])
    if attempts+1>=max_attempts:raise
   attempts+=1
   time.sleep(0.8)
   remaining=B('free',coin)
   p=live(sym) or p
   if not full_exit:break
   if remaining<=0 or remaining*p<1.02:break
  if total_filled<=0:
   tg(f"ℹ️ {sym} has no confirmed sale fill ({reason}).");return False
  avg_px=weighted_value/total_filled
  db=load(DB)
  if sym in db:
   pnl=(avg_px-db[sym]["entry_price"])*total_filled;rec(sym,pnl,reason,avg_px*total_filled)
   tg(f"✅ *SOLD ({reason})*\n🪙 {total_filled:.8g} {sym}\n{'🟢' if pnl>=0 else '🔴'} PnL: ${pnl:.2f}")
  else:tg(f"✅ *SOLD ({reason})*\n🪙 {total_filled:.8g} {sym}\nCost basis not tracked; realized PnL not calculated.")
  if full_exit and remaining*p>=1.02:
   journal_event("full_sale_incomplete",symbol=sym,remaining_base=remaining,remaining_value_usdt=remaining*p,filled=total_filled)
   coin_db_mark_execution(sym,"HELD","Full exit incomplete; residual position remains",{"remaining":remaining,"filled":total_filled,"reason":reason})
   tg(f"⚠️ *PARTIAL SALE — POSITION RETAINED:* {sym}; approximately {remaining:.8g} remains (${remaining*p:.2f}). It will stay tracked; no replacement buy will be made.")
   return False
  if not full_exit:
   coin_db_mark_execution(sym,"HELD","Partial sale completed; residual position remains",{"remaining":remaining,"filled":total_filled,"reason":reason})
  if full_exit:
   journal_event("full_sale_verified_or_dust",symbol=sym,remaining_base=remaining,remaining_value_usdt=remaining*p if remaining else 0.0,filled=total_filled)
   if remaining>0:tg(f"🟡 {sym}: remaining balance is below the $1.02 tradable-order threshold and is treated as exchange dust.")
   coin_db_mark_execution(sym,"SOLD","Protective/full sale confirmed",{"filled":total_filled,"average_exit":avg_px,"remaining":remaining,"reason":reason})
   dust_result=cleanup_spot_dust_after_sale(sym);tg(dust_result)
   # Post-exit AI review informs future analysis; it never delays or reverses a protective sale.
   ai=ai_stage('sell_review',{'symbol':sym,'reason':reason,'sold_quantity':total_filled,'average_exit':avg_px,
    'remaining_base':remaining,'realized_pnl_usdt':(avg_px-db[sym]['entry_price'])*total_filled if sym in db else None},ttl=60,max_tokens=220)
   if ai:journal_event('ai_sell_review',symbol=sym,review=ai)
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
 # User-initiated /buy remains under the user's authority; AI provides a warning, never blocks it.
 ai_manual=ai_stage('manual_buy_review',{'symbol':sym,'amount_usdt':amt,'price':live(sym),'rsi':data(sym)[2],
  'btc_filter_active':btc_blocked(cfg()),'market_filter':market_filter(cfg()),'existing_position':bool(old)},ttl=60,max_tokens=220,soft=True)
 try:
  ob=B('total',c) if old else 0.0;f,a=m_buy(f"{c}/USDT",amt);sl=sl_pct(sym,old) if old else sl_pct(sym)
  if old:
   e0=old['entry_price'];avg=round((ob*e0+f*a)/(ob+f),6) if ob>0 else round(a,6)
   old.update(entry_price=avg,safe_zone=avg,stop_loss=round(avg*(1-sl/100),6),last_price=a,peak_price=max(old.get('peak_price',avg),a),opened_at=time.time(),purchase_source='MANUAL')
   # DCA: the entry amount grows with every add; the stop and the 75% rule restart from the new average
   old.update(entry_qty=float(old.get('entry_qty') or ob)+float(f),entry_usdt=round(float(old.get('entry_usdt') or ob*e0)+float(f)*float(a),4),dca_count=int(old.get('dca_count',0))+1)
   old.pop('partial_done',None);old.pop('stop_ref',None);old.pop('xsl',None);db[sym]=old;save(DB,db)
   coin_db_mark_execution(sym,"BOUGHT","Manual DCA fill confirmed",{"filled":f,"average":a,"spend_usdt":amt,"source":"MANUAL"})
   ok=place_trailing_sl(sym,free_qty(sym,ob+f))
   tg(f"➕ *DCA ADDED:* {c} @ {a:.6f}\nSpent: ${amt:.2f}\nNew avg entry: {avg:.6f}\nTotal entry amount: ${old['entry_usdt']:.2f}\n"+(STOP_OK if ok else STOP_NO))
  else:
   db[sym]=NP(round(a,6),round(a*(1-sl/100),6),sl);db[sym].update(entry_qty=float(f),entry_usdt=round(float(f)*float(a),4));db[sym]['strategy']="MANUAL";db[sym]['purchase_source']='MANUAL';db[sym]['opened_at']=time.time();save(DB,db)
   coin_db_mark_execution(sym,"BOUGHT","Manual buy fill confirmed",{"filled":f,"average":a,"spend_usdt":amt,"source":"MANUAL"})
   ok=place_trailing_sl(sym,free_qty(sym,f))
   tg(f"✅ *BOUGHT:* {c} @ {a:.6f}\nSpent: ${amt:.2f}\n"+(STOP_OK if ok else STOP_NO))
  if ai_manual:
   journal_event('ai_manual_buy_review',symbol=sym,review=ai_manual)
   tg('🧠 AI manual-buy advisory: '+str(ai_manual.get('summary',ai_manual.get('warning','Review available')))[:500])
 except Exception as e:tg(f"❌ *BUY FAILED:* {e}")
def cmd_sell(txt):
 r=parse(txt,"❌ Usage: `/sell COIN USDT_AMOUNT` (or `ALL`)",True)
 if not r:return
 c,amt=r;sym=f"{c}-USDT";cs=f"{c}/USDT"
 if cd(f"sell:{sym}",5):tg(f"⏳ Processing /sell {c}...");return
 db=load(DB)
 if sym not in db:tg(f"❌ No active bot trade for {c}. The bot adds held coins automatically on its next scan.");return
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
  pnl=(px-db[sym]["entry_price"])*filled;rec(sym,pnl,f"MANUAL SELL {a_s}",px*filled)
  time.sleep(1);rem=B('total',c)
  fully_filled=full_exit and filled>=q*0.999
  if rem<=0 or rem*p<1.02:
   db.pop(sym,None);save(DB,db)
   coin_db_mark_execution(sym,"SOLD","Manual full sale confirmed",{"filled":filled,"average":px,"pnl_usdt":pnl})
   tg(f"✅ *SOLD:* {filled:.8g} {c} @ {px:.6f}\nPnL: ${pnl:.2f}\nPosition closed.")
  else:
   place_trailing_sl(sym,rem);save(DB,db)
   coin_db_mark_execution(sym,"HELD","Manual partial sale; residual position remains",{"remaining":rem,"filled":filled,"average":px})
   tg(f"✅ *SOLD:* {filled:.8g} {c} @ {px:.6f}\nPnL: ${pnl:.2f}\nRemaining: {rem:.8g} {c} (stop re-armed)")
  if fully_filled:
   tg(cleanup_spot_dust_after_sale(sym))
  ai_review=ai_stage('sell_review',{'symbol':sym,'reason':'MANUAL SELL','sold_quantity':filled,'average_exit':px,
   'remaining_base':rem,'realized_pnl_usdt':pnl},ttl=60,max_tokens=220,soft=True)
  if ai_review:journal_event('ai_manual_sell_review',symbol=sym,review=ai_review)
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
def auto_sync(verbose=False):
 # Every scan: coins held on the exchange but not tracked by the bot get tracked, with a stop and trailing stop. Silent unless coins were added.
 try:b=ex.fetch_balance()['total']
 except Exception:
  if verbose:tg("❌ Failed to fetch balance.")
  return []
 db=load(DB);added=[];s=cfg()
 for c,a in b.items():
  try:a=float(a or 0)
  except (TypeError,ValueError):continue
  if c in STABLE or c in BL or c=="BGB" or a<=0:continue
  sym=f"{c}-USDT";p=live(sym)
  if p and a*p>1.02 and sym not in db:
   db[sym]=NP(p,round(p*(1-s['stop_loss_pct']/100),6));db[sym].update(entry_qty=a,entry_usdt=round(a*p,4));save(DB,db);added.append(sym)
   if not (place_trailing_sl(sym,a) or protect_retry(sym,a)):tg(f"⚠️ *STOP NOT VERIFIED* for synced coin {sym}. The watchdog will retry.")
 if added:tg(f"🔄 *AUTO-SYNC:* now tracking {len(added)} coin(s) you hold, with stop and trailing stop:\n`{chr(10).join(added)}`")
 elif verbose:tg("✅ No new coins found to sync.")
 return added
def cmd_sync():auto_sync(True)
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
 save(DB,db);save(QF,{})
 tg(f"⚠️ *PANIC INCOMPLETE.* Still held: `{', '.join(failed)}`" if failed else "🛑 *PANIC COMPLETE.* All positions sold.")

# ============================== FILE: bitget_bot/components/04_portfolio_and_coin_analysis.py ==============================
# Portfolio, holdings, coin analysis, listings, and status
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
 usdt_total=float(totals.get('USDT',0.0));usdt_free=float(free.get('USDT',0.0));total_coins_value=0.0;unpriced=[];rows=[];db=load(DB)
 for coin,qty in sorted(totals.items()):
  try: qty=float(qty or 0)
  except (TypeError,ValueError): continue
  if qty<=0 or coin in STABLE or coin in BL: continue
  sym=f"{coin}-USDT";price=live(sym)
  if not price:unpriced.append(f"{coin}: {qty:.8g}")
  if not price or qty*price<0.10: continue
  val=qty*price;total_coins_value+=val
  ed=db.get(sym,{});entry=ed.get('entry_price');stop=ed.get('stop_loss')
  _,_,rsi,ma5,ma10=data(sym)
  trend="n/a";ic="⚪"
  if rsi is not None and ma5 is not None and ma10 is not None:
   if price>ma5>ma10 and rsi>=50:trend="Bullish"
   elif price<ma5<ma10 and rsi<50:trend="Bearish"
   else:trend="Mixed"
  if entry:
   pnl=(price-entry)/entry*100;pu=(price-entry)*qty;ic="🟢" if pnl>=0 else "🔴"
   l2=f"{pnl:+.2f}% ({'+' if pu>=0 else '-'}${abs(pu):.2f}) | Entry {fp(entry)}"
  else:l2="Entry not tracked by bot"
  l3=f"Qty {qty:.8g} | Price {fp(price)}"+(f" | Stop {fp(stop)} ({(float(stop)/price-1)*100:+.0f}%)" if stop else " | No stop tracked")
  try:
   ath=get_bitget_ath_atl(sym)[0];athtxt=f" | ATH {(price/float(ath)-1)*100:+.0f}%" if ath else ""
  except Exception:athtxt=""
  l4=f"RSI {rsi:.0f} | {trend}{athtxt}" if rsi is not None else f"RSI n/a | {trend}{athtxt}"
  rows.append((val,coin,ic,l2,l3,l4))
 total_portfolio_value=wallet_value(totals)
 _BAL["t"],_BAL["m"]=time.time(),balance_text(total_portfolio_value)
 bgb_total=float(totals.get('BGB',0.0) or 0.0);bgb_price=live('BGB-USDT') if bgb_total>0 else None
 _r=ngn_rate();tp=total_portfolio_value or 0.0
 share=(lambda v:f" ({v/tp*100:.0f}%)" if tp>0 else "")
 lines=[f"💼 *PORTFOLIO* ${tp:.2f}"+(f" | ₦{tp*_r:,.0f}" if _r else ""),f"💵 Cash free ${usdt_free:.2f} | In coins ${total_coins_value:.2f}{share(total_coins_value)}"]
 if bgb_total>0:lines.append(f"🟡 BGB {bgb_total:.8g}"+(f" (${bgb_total*bgb_price:.2f})" if bgb_price else "")+" - not auto-converted")
 lines.append("━━━━━━━━━━━━━━")
 for val,coin,ic,l2,l3,l4 in sorted(rows,reverse=True):
  lines+=[f"{ic} *{coin}* ${val:.2f}{share(val)}",l2,l3,l4,""]
 if not rows:lines.append("No coin holdings.")
 if unpriced:
  note="⚠️ Held but with no USDT price: "+", ".join(unpriced[:3])
  if len(unpriced)>3:note+=f" and {len(unpriced)-3} more..."
  lines.append(note)
 return "\n".join(lines).rstrip()+f"\n\n📊 *LIVE SPOT WALLET BALANCE:* {compact_balance()}"
def check_hourly_balance():
 st=load(SC);now=time.time()
 if now-st.get('last_bal_alert',0)>=3600:tg(f"💼 *HOURLY HOLDINGS*\n{get_holdings_text()}");st['last_bal_alert']=now;save(SC,st)
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
def manual_candidate_detail(sym):
 """Show database-backed manual-candidate evidence; never submits an order."""
 sym=str(sym or "").upper().replace("/","-")
 if not sym.endswith("-USDT"):sym+="-USDT"
 memo=coin_db_get(sym)
 if not memo:
  ui_send(f"⚠️ No Coin List record for {sym}. Run a fresh scan first.",[[ _B("⬅️ Analyse","/analyse") ]]);return
 mc=(memo.get("checks",{}).get("scanner",{}) or {}).get("manual_candidate",{}) or {}
 s=cfg();threshold=7;score=float(mc.get("score") or 0)
 fresh_window=max(1800.0,float(s.get("scan_interval_minutes",15.0))*120.0)
 age=max(0.0,time.time()-float(memo.get("last_seen") or 0));fresh=age<=fresh_window
 checks=mc.get("checks") if isinstance(mc.get("checks"),dict) else {}
 labels=checks.get("labels",{}) if isinstance(checks.get("labels"),dict) else {}
 passed=[];failed=[];unknown=[]
 for group_name in ("core","additional"):
  group=checks.get(group_name,{}) if isinstance(checks.get(group_name),dict) else {}
  for key,value in group.items():
   label=labels.get(key,key.replace("_"," ").title())
   target=passed if value is True else failed if value is False else unknown
   target.append(("CORE — " if group_name=="core" else "ADDITIONAL — ")+label)
 for key,value in (checks.get("gates") or {}).items():
  (passed if value is True else failed if value is False else unknown).append("HARD GATE — "+key.replace("_"," "))
 core_count=int(checks.get("core_score",0) or 0);add_count=int(checks.get("additional_score",0) or 0)
 # Recheck spread at detail time. A missing or excessive spread disables this candidate shortcut.
 spr=spread_pct(sym);spread_ok=spr is not None and spr<=float(s.get("max_spread_pct",0.5))
 is_auto=bool(score==10 and core_count==7 and add_count==3 and mc.get('checks',{}).get('hard_ok'))
 db_eligible=(bool(mc.get("eligible")) and score>=threshold) or is_auto
 safe=bool(mc.get("eligible")) and score>=threshold and fresh and spread_ok and memo.get("signal_status")!="BOUGHT"
 px=live(sym) or 0.0
 head=f"🤖 *AUTO-ELIGIBLE CANDIDATE — {sym.split('-')[0]}*" if is_auto else f"🟡 *MANUAL BUY CANDIDATE — {sym.split('-')[0]}*"
 lines=[head,f"Setup score: *{int(score)}/10* | Core: *{core_count}/7* | Additional: *{add_count}/3*",
  f"Current price: {fp(px) if px else 'Unavailable'} USDT",f"1H change: {float((memo.get('checks',{}).get('scanner',{}) or {}).get('h1_pct') or 0):+.2f}% | RSI: {float((memo.get('checks',{}).get('scanner',{}) or {}).get('rsi') or 0):.1f}",
  f"Database signal: {memo.get('signal_status') or 'UNKNOWN'} | Auto execution: {memo.get('execution_status') or 'UNKNOWN'}",
  f"Last scan: {age/60:.0f} minutes ago" if memo.get('last_seen') else "Last scan: unknown", "",
  "✅ *Passed conditions:* "+(", ".join(passed) if passed else "None recorded"),
  "⚠️ *Missed conditions:* "+(", ".join(failed) if failed else "None recorded"),
  "ℹ️ *Unavailable evidence:* "+(", ".join(unknown) if unknown else "None"),
  *(["🧠 AI note: "+str(memo["ai_advisory"])[:180]] if memo.get("ai_advisory") else []),
  *(["📈 1H move has risen for 3 scans in a row"] if (memo.get("checks",{}).get("scanner",{}) or {}).get("h1_rising_three_scans") else []),
  "Automatic-buy exclusion: "+str(mc.get('reason') or memo.get('reason') or "Did not meet all automatic entry rules")[:180],
  f"Spread safety: {spr:.3f}% — {'within' if spread_ok else 'outside/unverified against'} {float(s.get('max_spread_pct',0.5)):g}% limit" if spr is not None else "Spread safety: UNAVAILABLE — cannot verify",
  "Classification: "+("10/10 AUTO score; independent safety and execution checks remain mandatory" if is_auto else "MANUAL score passed; manual review only" if db_eligible else "Not qualified for MANUAL/AUTO"),
  "Candidate freshness: "+("FRESH" if fresh else "STALE — wait for a new scan"),
  ("10/10 AUTO classification; independent scanner, risk and execution checks are still mandatory." if is_auto else "MANUAL classification requires at least 5/7 core and 2/3 additional; advisory only.")]
 rows=[]
 if safe and px>0:
  rows.append([_B("🛒 Continue to manual buy","m:b:"+sym.split("-")[0])])
 else:
  why=[]
  if not db_eligible:why.append("database candidate checks no longer pass")
  if not fresh:why.append("scan record is stale")
  if not spread_ok:why.append("spread is too wide or unavailable")
  if not px:why.append("live price unavailable")
  lines.append("\n⛔ Candidate shortcut unavailable: "+", ".join(why)+".")
 rows += [[_B("🔄 Refresh candidate","m:mc:"+sym.split("-")[0]),_B("⬅️ Analyse","/analyse")]]
 _send("\n".join(lines),"Markdown",{"inline_keyboard":rows})
def _coin_table(items):
 # Same look as Scan History: a monospace table, one coin per line.
 rows=[]
 for it in items:
  sc=it.get("score");sc="-" if sc is None else str(int(float(sc)))
  rows.append(f"{str(it.get('sym','?')).replace('-USDT','')[:7]:<7}{_fmt(it.get('rsi'),'.0f'):>4}{_fmt(it.get('h1'),'+.1f'):>6}{_fmt(it.get('chg'),'+.1f'):>7}{sc:>3}{ {'TREND':' T','PULLBACK':' P','BOUNCE':' B','BREAKOUT':' K'}.get(str(it.get('tag') or ''),' -'):>3}")
 if not rows:return "None right now."
 return "```\n"+f"{'COIN':<7}{'RSI':>4}{'1H':>6}{'24H':>7}{'S':>3}{'TY':>3}"+"\n"+"-"*30+"\n"+"\n".join(rows)+"\n```\n_TY: T Trend, P Pullback, B Bounce, K Breakout_"
def analyse_top3(tab="M"):
 # Analyse: three tabs that follow the pipeline Watch list > Manual buy > Auto buy. Lists only; never changes execution rules.
 tab=tab if tab in ("W","M","A") else "M";settings=cfg()
 threshold=7
 try:
  cnt=coin_db_stage_counts()
  items=coin_db_manual_candidates(50) if tab=="M" else coin_db_stage_list("WATCH" if tab=="W" else "AUTO",50)
 except Exception as e:
  print("ANALYSE LIST ERR",str(e)[:120]);tg("⚠️ Could not read the coin lists. Try again shortly.");return
 title={"W":"👁 *WATCH LIST*","M":"🟡 *MANUAL BUY*","A":"🤖 *AUTO BUY*"}[tab]
 sub={"W":"_Rising coins being watched. They move up when they pass the manual rules._",
      "M":"_Passed at least 5/7 core and 2/3 additional criteria. Manual review only._",
      "A":"_Passed all 7/7 core and 3/3 additional criteria. Still requires independent READY, risk and execution checks._"}[tab]
 lines=[title,sub,"",_coin_table(items)]
 def tb(k,lbl):return _B(("▶ " if tab==k else "")+f"{lbl} ({cnt.get({'W':'WATCH','M':'MANUAL','A':'AUTO'}[k],0)})",f"m:at:{k}")
 keyboard=[[tb("W","👁 Watch"),tb("M","🟡 Manual"),tb("A","🤖 Auto")],
           [_B("🎯 SETUPS","m:su"),_B("🗂 MORE","m:am")],
           [_B("🔄 Refresh",f"m:at:{tab}")]]
 # Coin names remain in the readable list above; do not create a separate button for every coin.
 _send("\n".join(lines)[:3800],"Markdown",{"inline_keyboard":keyboard})

def analyse_archive(tab="D"):
 """Dropped and Archived coins are never deleted; browse them here for a history check. Read-only list."""
 tab=tab if tab in ("D","A") else "D";stage="DROPPED" if tab=="D" else "ARCHIVED"
 try:
  cnt=dict(_dbq("SELECT stage,COUNT(*) FROM coins WHERE stage IN ('DROPPED','ARCHIVED') AND status<>'BOUGHT' GROUP BY stage"))
  rows=_dbq("SELECT sym,price,chg24,h1,rsi,scans,manual_score,last_seen FROM coins WHERE stage=? AND status<>'BOUGHT' ORDER BY last_seen DESC LIMIT 40",(stage,))
 except Exception as e:
  print("ARCHIVE ERR",str(e)[:120]);tg("⚠️ Could not read the archive. Try again shortly.");return
 title={"D":"⤵ *DROPPED COINS*","A":"🗄 *ARCHIVED COINS*"}[tab]
 sub={"D":"_Stopped qualifying for 24h. Kept for history; they return to Watch if they qualify again._","A":"_Not seen for 30 days. Kept for history._"}[tab]
 lines=[title,sub,"",_coin_table([{"sym":s_,"rsi":r_,"h1":h_,"chg":c_,"score":sc_} for s_,p_,c_,h_,r_,n_,sc_,ls_ in rows])]
 tb=lambda k,lbl,key:_B(("▶ " if tab==k else "")+f"{lbl} ({cnt.get(key,0)})",f"m:ar:{k}")
 _send("\n".join(lines),"Markdown",{"inline_keyboard":[[tb("D","⤵ Dropped","DROPPED"),tb("A","🗄 Archived","ARCHIVED")],[_B("⬅️ Analyse","/analyse"),_B("🔄 Refresh",f"m:ar:{tab}")]]})
def setups_items():
 """Coins from the latest scan that pass at least 3 of the 5 checks, as tuples (-passed,tag,sym,rsi,1h,24h,blocked)."""
 try:
  mx=_dbq("SELECT MAX(last_seen) FROM coins",one=True)[0] or 0
  rows=_dbq("SELECT sym,rsi,h1,chg24,tag,checks_json FROM coins WHERE status<>'BOUGHT' AND rsi IS NOT NULL AND last_seen>=?",(float(mx)-600,))
 except Exception as e:
  print("SETUPS ERR",str(e)[:120]);return None
 code={"rsi_ceiling":"R","price_above_ma":"M","momentum_1h":"H","momentum_24h_up":"D"};tc={"TREND":"T","PULLBACK":"P","BOUNCE":"B","BREAKOUT":"K"};items=[]
 for sym,r_,h_,c_,tag,cj in rows:
  try:ck=((json.loads(cj or "{}").get("scanner") or {}).get("manual_candidate") or {}).get("checks") or {}
  except Exception:ck={}
  g=ck.get("gates") or {};bl=[v for k,v in code.items() if g.get(k) is not True]
  try:sc_ok=int(ck.get("core_score",0))>=5 and int(ck.get("additional_score",0))>=2
  except Exception:sc_ok=False
  if not sc_ok:bl.append("S")
  n=5-len(bl)
  if n>=3:items.append((-n,tag or "",sym,r_,h_,c_,"".join(bl) or "OK"))
 items.sort(key=lambda x:(x[0],x[1]==""))
 return items
def analyse_setups():
 """Coins from the latest scan that pass at least 3 of the 5 checks (RSI, MA, 1H, 24H gates and the Core 5+/Extra 2+ score), with what blocked them. Read-only list."""
 tc={"TREND":"T","PULLBACK":"P","BOUNCE":"B","BREAKOUT":"K"};items=setups_items()
 if items is None:tg("⚠️ Could not read the setups list. Try again shortly.");return
 out=[f"{str(sym).replace('-USDT','')[:6]:<6}{_fmt(r_,'.0f'):>3}{_fmt(h_,'+.1f'):>5}{_fmt(c_,'+.0f'):>5}{tc.get(tag,'-'):>3} {-n}/5 {bl}" for n,tag,sym,r_,h_,c_,bl in items[:40]]
 body=("```\n"+f"{'COIN':<6}{'RSI':>3}{'1H':>5}{'24H':>5}{'TY':>3} PASS BLOCK"+"\n"+"-"*32+"\n"+"\n".join(out)+"\n```") if out else "No coin passes 3 of the 5 checks right now."
 lines=["🎯 *SETUPS*","_Coins from the latest scan that pass at least 3 of 5 checks._","",body,"_TY: T Trend, P Pullback, B Bounce, K Breakout, - none_","_BLOCK: R RSI, M MA, H 1H, D 24H, S score (Core 5+ and Extra 2+). OK = all passed._"]
 _send("\n".join(lines),"Markdown",{"inline_keyboard":[[_B("🔍 SCAN NOW","m:r:scan"),_B("📜 SCAN HISTORY","/scan_history")],[_B("⬅️ Analyse","/analyse"),_B("🔄 Refresh","m:su")]]})
def coin_search(q):
 """Type any part of a coin name: exact match opens its analysis, several matches give a pick list."""
 _UI["ub"]=True;_UI["ub_done"]=False
 term=re.sub(r"[^A-Z0-9]","",str(q).upper().replace("-USDT","").replace("/USDT",""))[:20]
 if not term:ui_send("🔍 Type letters or numbers, for example SOL.",[[_B("🔍 Search again","m:se")],[_B("⬅️ Back","m:an")]]);return
 try:
  names=[str(r[0]).split("-")[0] for r in _dbq("SELECT sym FROM coins WHERE sym LIKE ? ORDER BY last_seen DESC LIMIT 60",(f"%{term}%",))]
 except Exception:names=[]
 names=list(dict.fromkeys(n for n in names if n.upper() not in STABLE))
 if term in names:analyse_coin(f"/analyse {term}");return
 if len(names)==1:analyse_coin(f"/analyse {names[0]}");return
 if names:ui_send(f"🔍 *{len(names)} match(es) for {term}* — tap one:",_grid([_B(n,f"m:a:{n}") for n in names[:24]],3)+[[_B("🔍 Search again","m:se"),_B("⬅️ Back","m:an")]]);return
 try:
  if f"{term}/USDT" in ex.load_markets():analyse_coin(f"/analyse {term}");return
 except Exception:pass
 ui_send(f"🔍 No coin matches *{term}*.",[[_B("🔍 Search again","m:se"),_B("⬅️ Back","m:an")]])
def fav_list():
 v=load(FAV,[])
 return [str(x).upper() for x in v if isinstance(x,str)] if isinstance(v,list) else []
def fav_show(msg=""):
 """Favourites: your own watch list of tokens. Read-only view plus add/remove buttons; never buys or sells."""
 fl=fav_list();rows=[]
 for c in fl:
  sym=f"{c}-USDT"
  try:r=_dbq("SELECT price,rsi,h1,chg24 FROM coins WHERE sym=?",(sym,),one=True)
  except Exception:r=None
  px=(r[0] if r and r[0] else None) or live(sym)
  rows.append(f"{c[:6]:<6}{(format(float(px),'.6g')[:8] if px else '-'):>8}{_fmt(r[1] if r else None,'.0f'):>4}{_fmt(r[2] if r else None,'+.1f'):>6}{_fmt(r[3] if r else None,'+.1f'):>7}")
 body=("```\n"+f"{'COIN':<6}{'PRICE':>8}{'RSI':>4}{'1H':>6}{'24H':>7}"+"\n"+"-"*31+"\n"+"\n".join(rows)+"\n```") if rows else "No favourites yet. Tap Add."
 kb=[[_B("➕ Add coin","m:more:fav:0"),_B("🔄 Refresh","m:fv")]]
 kb+=_grid([_B(f"⭐ {c}",f"m:fc:{c}:fv") for c in fl],3)+[[_B("⬅️ Analyse","/analyse")]]
 _send(("\n".join(x for x in (msg,)+("⭐ *FAVOURITES*","_Your own list. Tap a coin to buy, sell or remove it._","",body) if x is not None)),"Markdown",{"inline_keyboard":kb})
PICK_BACK={"fv":"m:fv","W":"m:at:W","M":"m:at:M","A":"m:at:A","su":"m:su","D":"m:ar:D","Z":"m:ar:A","nl":"m:nl","H":"/scan_history"}
def pick_coins(src):
 """Coins listed on a given Analyse page, so each can be opened for Buy / Sell."""
 try:
  if src in ("W","M","A"):it=coin_db_manual_candidates(50) if src=="M" else coin_db_stage_list("WATCH" if src=="W" else "AUTO",50);cs=[x["sym"] for x in it]
  elif src=="su":cs=[x[2] for x in (setups_items() or [])]
  elif src in ("D","Z"):cs=[r[0] for r in _dbq("SELECT sym FROM coins WHERE stage=? AND status<>'BOUGHT' ORDER BY last_seen DESC LIMIT 24",("DROPPED" if src=="D" else "ARCHIVED",))]
  elif src=="nl":
   tr=(load(NEWLIST,{}) or {}).get("tracked",{});now=time.time()
   cs=[s for s,m in sorted(tr.items(),key=lambda kv:-float((kv[1] or {}).get("first_detected",0) or 0)) if isinstance(m,dict) and now-float(m.get("first_detected",0) or 0)<=86400]
  elif src=="H":cs=[x.get("sym","") for x in reversed(scan_history_state().get("items",[]))]
  else:cs=fav_list()
 except Exception as e:
  print("PICK ERR",str(e)[:100]);cs=[]
 out=[]
 for s in cs:
  c=str(s).split("-")[0].upper()
  if c and c not in out and c not in STABLE:out.append(c)
 return out[:24]
def coin_picker(src):
 cs=pick_coins(src);back=PICK_BACK.get(src,"/analyse")
 ui_send("💱 *BUY / SELL* — tap a coin from this list:" if cs else "💱 *BUY / SELL*\nNo coins on this list right now.",_grid([_B(c,f"m:fc:{c}:{src}") for c in cs],3)+[[_B("⬅️ Back",back)]])
def fav_coin(c,src="fv"):
 # One coin: price, what you hold, and Buy / Sell by 25-50-75-100% (ALL = like panic sell for this coin), plus add/remove favourites.
 c=str(c).upper();sym=f"{c}-USDT";px=live(sym);back=PICK_BACK.get(src,"/analyse")
 try:held=float(B('free',c) or 0)
 except Exception:held=0.0
 lines=[f"⭐ *{c}*",f"Price: {fp(px) if px else 'n/a'} USDT",f"You hold: {held:.8g}"+(f" (about ${held*px:.2f})" if px and held else "")]
 kb=[[_B(f"🟢 {x}%",f"m:bp:{c}:{x}") for x in (25,50,75)]+[_B("🟢 ALL",f"m:bp:{c}:100")],[_B(f"🛒 Buy {c}: other amount",f"m:b:{c}")]]
 if px and held*px>=1.0:kb.append([_B(f"🔴 {x}%",f"m:sc:{c}:{x}") for x in (25,50,75)]+[_B("🔴 ALL",f"m:sc:{c}:100")])
 kb.append([_B("🗑 Remove from favourites",f"m:fd:{c}")] if c in fav_list() else [_B("⭐ Add to favourites",f"m:fa:{c}")])
 kb.append([_B("⬅️ Back",back)])
 ui_send("\n".join(lines),kb)
def fav_change(c,add):
 c=str(c).upper();fl=fav_list();note=""
 if add:
  if c in fl:note=f"{c} is already in your list."
  elif c in STABLE:note=f"{c} is a stablecoin and cannot be added."
  elif len(fl)>=30:note="The list is full (30 coins). Remove one first."
  else:fl.append(c);note=f"✅ {c} added."
 else:fl=[x for x in fl if x!=c];note=f"✅ {c} removed."
 save(FAV,fl);fav_show(note)
def analyse_new_listings():
 """Show recently detected Bitget spot listings as a readable list, never as buy buttons."""
 state=load(NEWLIST,{})
 tracked=state.get("tracked",{}) if isinstance(state,dict) else {}
 now=time.time()
 recent=[]
 for sym,meta in tracked.items():
  if not isinstance(meta,dict):continue
  detected=float(meta.get("first_detected",0) or 0)
  if detected and now-detected<=86400:recent.append((detected,sym,meta))
 recent.sort(reverse=True)
 lines=["🆕 *NEW BITGET SPOT LISTINGS*",
        "_Recently detected online USDT pairs. WATCH/observation only — not buy signals._",""]
 for detected,sym,meta in recent[:40]:
  coin=str(sym).split("-")[0]
  when=datetime.datetime.fromtimestamp(detected).strftime("%d %b %H:%M")
  candles=meta.get("hourly_candles")
  status="history building" if candles is None else f"{int(candles)} hourly candles"
  lines.append(f"• *{coin}/USDT* — detected {when} | {status}")
 if not recent:lines.append("No newly detected listings in the last 24 hours. The first successful discovery scan establishes the baseline.")
 _send("\n".join(lines)[:3800],"Markdown",{"inline_keyboard":[[_B("⬅️ Back to Analyse","/analyse"),_B("🔄 Refresh","m:nl")]]})

def system_layers(s,exchange_ok=True,held=(),db=None):
 # Cheap read-only layer checks: (name, ok, detail). The audit (every run) fixes what fails.
 db=db if isinstance(db,dict) else load(DB);L=[("Exchange",bool(exchange_ok),"")]
 ls=float(load(SC,{}).get('last',0) or 0);age=(time.time()-ls)/60 if ls else None
 L.append(("Scanner",age is not None and age<=max(45.0,float(s.get('scan_interval_minutes',15) or 15)*3),f"{age:.0f}m ago" if age is not None else "never"))
 try:dbok=bool(coin_db_integrity()[0])
 except Exception:dbok=False
 L.append(("Database",dbok,""))
 g=load(GUARDIAN,{});L.append(("Buyer",bool(g.get("healthy",True)),str(g.get("summary",""))[:60]))
 miss=[c for c,sym in held if not (db.get(sym) or {}).get('stop_loss')]
 L.append(("Stops",not miss,", ".join(miss[:3])+" no stop" if miss else ""))
 return L
def system_buy_state(s):
 try:
  ok,why=risk_controls()
  if not ok:return f"PAUSED ⛔ ({str(why)[:60]})"
  if btc_blocked(s):return "PAUSED ⛔ (BTC filter)"
  mf=market_filter(s)
  if mf:return f"PAUSED ⛔ ({str(mf)[:50]})"
  w=weak_market(s)
  return "ON ✅ (weak market: half size)" if w else "ON ✅"
 except Exception as e:return "UNKNOWN ⚠️"
def cmd_status():
 s=cfg();now=time.time();db=load(DB);ex_ok=True
 try:bal=ex.fetch_balance();totals=bal.get('total') or {};free=bal.get('free') or {}
 except Exception as e:ex_ok=False;totals={};free={}
 usdt_free=float(free.get('USDT',0) or 0);held=[];coin_val=0.0
 for coin,qty in sorted(totals.items()):
  try:qty=float(qty or 0)
  except (TypeError,ValueError):continue
  if qty<=0 or coin in STABLE or coin in BL:continue
  sym=f"{coin}-USDT";px=live(sym)
  if px and qty*px>=0.10:held.append((coin,sym));coin_val+=qty*px
 total=wallet_value(totals) if ex_ok else 0.0;r=ngn_rate()
 L=system_layers(s,ex_ok,held,db);bot_pct=round(sum(1 for _,ok,_ in L if ok)/len(L)*100);ap=ai_health_pct();hs=ai_emergency_state()
 ai_mode="Recovering "+str(int(hs.get('consecutive_successes',0)))+"/3" if hs.get("emergency_mode") else "Normal"
 head="HEALTHY ✅" if bot_pct==100 and not hs.get("emergency_mode") and (ap is None or ap>=70) else "ATTENTION ⚠️"
 lay=" ".join(f"{'✅' if ok else '❌'} {n}"+(f" ({d})" if d and not ok else "") for n,ok,d in L)
 try:_,ddp=day_drawdown()
 except Exception:ddp=0.0
 q=load(QF);ls=load(SC).get('last',0);ago=f"{(now-ls)/60:.0f} min ago" if ls else "never"
 mom=f"1H +{float(s.get('mom_1h_min_pct',0.5)):g}–{float(s.get('mom_1h_max_pct',6.0)):g}%" if mom_on(s) else "24H range"
 lines=[f"🖥 *SYSTEM* — {head}",f"Buying: {system_buy_state(s)}",f"Health: Bot {bot_pct}% | AI {str(ap)+'%' if ap is not None else 'n/a'} ({ai_mode})",f"Layers: {lay}",f"Rate limits (1h): Exchange {rl_count('ccxt')} | AI {rl_count('groq')}"+(" — scanning slowed" if rl_count("ccxt",600)>=3 else "")]+([f"🔧 AI issue: {AI_CAUSE.get(le[0],le[0])}"] if (le:=ai_last_error()) and (hs.get("emergency_mode") or (ap is not None and ap<70)) else [])+["━━━━━━━━━━━━━━",
  "🧭 *STRATEGY* (4 setups)",f"Trend: price > MA5 > MA10, RSI {s.get('rsi_min')}-{s.get('rsi_max')}, {mom}",
  f"Pullback: MA5 > MA10, price between MA10 and MA5, RSI {s.get('pullback_rsi_min',40)}-{s.get('rsi_max')}, {mom}",
  f"Bounce {'ON' if s.get('oversold_enabled',True) else 'OFF'}: RSI under 30, price back above last close, {mom}",
  f"Breakout: price clears the 20H high, volume 1.5x+, RSI 50-70, hard stop {float(s.get('breakout_stop_pct',2.0)):g}%"+(" | all must be rising vs last scan" if s.get('rising_guard',True) else ""),
  f"Exit: stop {s.get('stop_loss_pct')}% sells {float(s.get('stop_exit_fraction',0.75))*100:g}% of entry, new stop on the rest | trail {s.get('trail_pct')}% | RSI {s.get('rsi_exit')}+ (dynamic)",
  (f"Size: 50% of balance (all-in if half < ${float(s.get('half_or_all_threshold_usdt',5.0)):g})" if s.get("half_or_all_sizing",True) else f"Risk: {s.get('risk_per_trade_pct')}% per trade")+f" | max {s.get('max_daily_trades')} trades/day | pause at -{s.get('max_daily_drawdown_pct')}% day",
  "━━━━━━━━━━━━━━",f"💼 ${total:.2f}"+(f" | ₦{total*r:,.0f}" if r else "")+f" | Cash ${usdt_free:.2f} | Coins ${coin_val:.2f}",
  f"Today {ddp:+.2f}% | Last scan {ago} | Queue {len(q)} | Positions {len(db)}"]
 tg("\n".join(lines))
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
  try:
   memo=coin_db_get(sym)
   lines.append("Coin database: "+(f"{memo.get('signal_status') or 'UNKNOWN'} / {memo.get('execution_status') or 'UNASSESSED'} | seen {memo.get('scans',0)} scans | "+str(memo.get('reason') or '')[:100] if memo else "not yet recorded by scanner"))
   if memo and memo.get("ai_advisory"):lines.append("AI notebook note: "+str(memo["ai_advisory"])[:180])
   if memo and memo.get("checks",{}).get("manual_candidate"): 
    mc=memo["checks"]["manual_candidate"];lines.append(f"Manual candidate score: {int(float(mc.get('score') or 0))}/10 — "+("🟡 MANUAL BUY CANDIDATE (review only)" if mc.get("eligible") else "Not qualified"));lines.append("Manual filter: "+str(mc.get("reason") or "")[:160])
  except Exception:pass
  tg("\n".join(lines))
 except Exception as e:tg(f"❌ Could not analyse {sym}: {e}")

# ============================== FILE: bitget_bot/components/05_ai_advisor.py ==============================
# AI advisory, health checks, and recovery logic
def _ai_reason(model):
 # gpt-oss models spend max_tokens on hidden reasoning; keep it low so the JSON answer is not cut off
 return {'reasoning_effort':'low'} if 'gpt-oss' in str(model) else {}
_AI_OPTIONAL=("near_miss_review","coin_advisory","command_review","portfolio_review","opportunity_ranking")
def ai_error_class(msg):
 m=str(msg).lower()
 if "missing" in m or "not configured" in m:return "NO_KEY"
 if "http 401" in m or "http 403" in m:return "BAD_KEY"
 if "http 429" in m or "rate limit" in m:rl_note("groq");return "RATE_LIMIT"
 if "http 400" in m or "http 404" in m or "unavailable. check groq_model" in m or "model_not_found" in m or "decommission" in m:return "MODEL"
 if any(f"http {c}" in m for c in (500,502,503,504)):return "SERVER"
 if "timed out" in m or "timeout" in m or "connection" in m or "resolve" in m:return "NETWORK"
 if "not json" in m or "unexpected content" in m or "must be an object" in m or "expecting value" in m or "empty" in m:return "FORMAT"
 return "OTHER"
AI_CAUSE={"NO_KEY":"API key missing","BAD_KEY":"API key rejected","RATE_LIMIT":"rate limit (too many calls)","MODEL":"model unavailable","SERVER":"Groq server error","NETWORK":"network trouble reaching Groq","FORMAT":"answers cut off or not in the right format","OTHER":"unknown error"}
def _ai_note(ok,err=""):
 # remember whether the last 20 AI calls worked, and why the failed ones failed (feeds AI health % and the audit)
 try:
  d=load(AI_RECENT,{});r=list(d.get("r",[]))[-19:]+[1 if ok else 0];d["r"]=r
  if not ok:d["e"]=(list(d.get("e",[]))+[[time.time(),ai_error_class(err),str(err)[:120]]])[-20:]
  elif len(r)>=5 and all(r[-5:]) and (d.get("boost",1)!=1):d["boost"]=1
  save(AI_RECENT,d)
 except Exception:pass
def ai_model():
 try:
  m=os.environ.get('GROQ_MODEL','').strip()
  return m or str(load(AI_RECENT,{}).get("model") or "") or GROQ_MODEL or 'openai/gpt-oss-120b'
 except Exception:return GROQ_MODEL or 'openai/gpt-oss-120b'
def ai_boost():
 try:return max(1,min(4,int(load(AI_RECENT,{}).get("boost",1) or 1)))
 except Exception:return 1
def ai_shed():
 try:
  e=load(AI_RECENT,{}).get("e",[]);return any(x[1]=="RATE_LIMIT" and time.time()-float(x[0])<300 for x in e)
 except Exception:return False
def ai_last_error():
 try:
  e=load(AI_RECENT,{}).get("e",[]);return (e[-1][1],e[-1][2],float(e[-1][0])) if e else None
 except Exception:return None
def ai_diagnose_and_fix():
 """Audit step: find out why the AI is failing and fix what can be fixed. Returns (class, cause, fix, fixable) or None."""
 hs=ai_emergency_state();le=ai_last_error()
 if not le:return None
 cls,msg,tm=le
 r=list(load(AI_RECENT,{}).get("r",[]))
 if not hs.get("emergency_mode") and (time.time()-tm>900 or (len(r)>=3 and all(r[-3:]))):return None   # healthy again: nothing to diagnose
 cause=AI_CAUSE.get(cls,"unknown error");d=load(AI_RECENT,{})
 stage=msg.split(":")[0] if ":" in msg else ""
 if stage:cause+=f" (stage: {stage})"
 if cls in ("NO_KEY","BAD_KEY"):
  return cls,cause,"Cannot fix from here. Put a valid key in GitHub: Settings > Secrets > Actions > GROQ_API_KEY.",False
 if cls=="RATE_LIMIT":return cls,cause,"Optional AI features are paused for 5 minutes so the essential checks can pass.",True
 if cls in ("SERVER","NETWORK"):return cls,cause,"Retrying with a delay on every run until Groq answers.",True
 if cls=="FORMAT":
  if int(d.get("boost",1) or 1)<4:d["boost"]=int(d.get("boost",1) or 1)*2;save(AI_RECENT,d);return cls,cause,f"Raised the AI answer length x{d['boost']}.",True
  return cls,cause,"Answer length is already at maximum; waiting for Groq to recover.",True
 if cls=="MODEL":
  try:
   key=os.environ.get('GROQ_API_KEY','').strip() or GROQ_API_KEY
   r=requests.get('https://api.groq.com/openai/v1/models',headers={'Authorization':'Bearer '+key},timeout=10)
   ids=[m.get('id') for m in r.json().get('data',[]) if m.get('id')]
   pref=[m for m in ids if 'gpt-oss' in m] or [m for m in ids if 'llama' in m and 'versatile' in m] or ids
   cur=ai_model();pick=next((m for m in pref if m!=cur),None)
   if pick:d["model"]=pick;save(AI_RECENT,d);return cls,cause,f"Switched to model {pick} (found in Groq's live list).",True
  except Exception as e:return cls,cause,"Could not read Groq's model list: "+str(e)[:60],False
  return cls,cause,"No other model available right now.",False
 return cls,cause,"Retrying; no automatic fix known for this error.",True
def ai_health_pct():
 try:
  r=list(load(AI_RECENT,{}).get("r",[]))
  if r:return round(sum(r)/len(r)*100)
  hs=ai_emergency_state()
  return 0 if hs.get("emergency_mode") else 100 if hs.get("status")=="healthy" else None
 except Exception:return None
def ai_stage(stage, payload, ttl=90, max_tokens=500, soft=False):
 """Structured Groq AI analysis. Advisory only; deterministic guards remain authoritative."""
 api_key=os.environ.get('GROQ_API_KEY','').strip() or GROQ_API_KEY
 model=ai_model()
 if not api_key:return None
 if soft and stage in _AI_OPTIONAL and ai_shed():return None  # rate limited: drop optional AI features, keep the essentials
 try:
  body=json.dumps(payload,sort_keys=True,default=str,separators=(',',':'))
  cache_key=(stage,body[:12000]);now=time.time();cached=_AI_CACHE.get(cache_key)
  if cached and now-cached[0]<ttl:return cached[1]
  _AI_LAST[stage]=now  # throttle failed calls too, preventing repeated API hammering
  schemas={
   'market_regime':'Return keys regime, risk_level, summary.',
   'opportunity_ranking':'Return rankings as an array of {symbol, adjustment, reason}; adjustment must be between -5 and +5.',
   'coin_advisory':'Return reviews as an array of {symbol, risk_level, comment}. Advisory only; do not change deterministic status or execution.',
   'portfolio_review':'Return keys portfolio_risk, attention as an array of {symbol, action, reason}, summary. Advice only.',
   'buy_advisory':'Return keys risk_level, warning, summary. Advisory only; deterministic safeguards decide eligibility and execution.',
   'manual_buy_review':'Return keys risk_level, warning, summary. Advisory only; the user explicitly requested this manual order.',
   'sell_review':'Return keys review, lesson, confidence. This is post-exit analysis only.',
   'analyse_review':'Return reviews as an array of {symbol, verdict (OK, CAUTION or WEAK), reason (max 14 words)}, one per candidate. Advisory only; the user decides manually and may buy coins that miss some filters.',
   'command_review':'Return key insight: one plain-language sentence (max 35 words) that interprets the command output using its specific numbers and flags anything needing attention. Advisory only.',
   'near_miss_review':'Return reviews as an array of {symbol, view (WATCH or SKIP), reason (max 16 words)}, one per candidate. These coins narrowly missed the entry rules; judge whether the near-miss looks like genuine building momentum or noise. Advisory only.',
   'performance_review':'Return keys win_rate_pct, net_pnl_usdt, recurring_patterns, recommendations.'}
  prompt=("You are an advisory AI module inside a live crypto SPOT bot. Return ONLY one valid JSON object, no markdown. "
   "Never claim to place orders. Never override deterministic exchange/risk safeguards. Do not invent missing data. "
   "Be conservative and explicitly represent uncertainty. Schema: "+schemas.get(stage,'Return a concise JSON object with summary and reason.')+
   " Stage="+stage+". Input JSON="+body[:12000])
  r=requests.post('https://api.groq.com/openai/v1/chat/completions',headers={'Authorization':'Bearer '+api_key,'Content-Type':'application/json'},
   json={'model':model,'messages':[{'role':'system','content':'Return valid compact JSON only.'},{'role':'user','content':prompt}], 'temperature':0.1,'max_tokens':max(max_tokens,1024)*ai_boost(),**_ai_reason(model)},timeout=18)
  if not r.ok:raise RuntimeError('Groq HTTP '+str(r.status_code))
  content=r.json().get('choices',[{}])[0].get('message',{}).get('content','')
  content=str(content).strip();start=content.find('{');end=content.rfind('}')
  if start<0 or end<=start:raise ValueError('AI response was not JSON')
  result=json.loads(content[start:end+1])
  if not isinstance(result,dict):raise ValueError('AI response must be an object')
  _AI_CACHE[cache_key]=(now,result);_AI_LAST[stage]=now
  try:
   hs=load(AI_HEALTH,{});stages=hs.setdefault('stages',{});ss=stages.setdefault(stage,{})
   ss['last_success']=now;ss['successes']=int(ss.get('successes',0))+1;ss['last_error']='';hs['last_stage_success']=now;save(AI_HEALTH,hs)
  except Exception:pass
  _ai_note(True)
  return result
 except Exception as e:
  print('AI STAGE '+str(stage)+' unavailable: '+str(e)[:180]);_ai_note(False,f'{stage}: {e}')
  try:
   hs=load(AI_HEALTH,{});stages=hs.setdefault('stages',{});ss=stages.setdefault(stage,{})
   ss['last_failure']=time.time();ss['failures']=int(ss.get('failures',0))+1;ss['last_error']=str(e)[:180];save(AI_HEALTH,hs)
  except Exception:pass
  if not soft:activate_ai_emergency(f"AI stage {stage} failed: {str(e)[:180]}")
  return None

def ai_health_check(force=False):
 # Five-minute live connectivity and structured-response self-test. Never controls orders.
 now=time.time();hs=load(AI_HEALTH,{})
 if not isinstance(hs,dict):hs={}
 if not force and now-float(hs.get('last_check',0) or 0)<240:return
 key=os.environ.get('GROQ_API_KEY','').strip() or GROQ_API_KEY
 if not key:
  _ai_note(False,'GROQ_API_KEY is missing')
  hs.update({'last_check':now,'status':'unconfigured','last_error':'GROQ_API_KEY is missing','consecutive_failures':int(hs.get('consecutive_failures',0))+1})
  save(AI_HEALTH,hs)
  hs['consecutive_successes']=0;save(AI_HEALTH,hs)
  if hs.get('alerted_status')!='unconfigured':
   hs['alerted_status']='unconfigured';save(AI_HEALTH,hs)
  activate_ai_emergency('GROQ_API_KEY is missing or not configured')
  return
 model=ai_model();started=time.time()
 if hs.get('emergency_mode') and int(hs.get('consecutive_successes',0))==0 and not hs.get('recovery_start_alerted'):
  hs['recovery_start_alerted']=True;save(AI_HEALTH,hs)
  tg('🛠 *AUDIT — AI RECOVERY STARTING*\nStage 1/3 is running. Auto-buy resumes after 3 passes.')
 try:
  r=requests.post('https://api.groq.com/openai/v1/chat/completions',headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},
   json={'model':model,'messages':[{'role':'system','content':'Return valid JSON only.'},{'role':'user','content':'AI health self-test. Return exactly this JSON object: {"status":"ok","test":"passed"}'}], 'temperature':0,'max_tokens':512*ai_boost(),**_ai_reason(model)},timeout=12)
  if not r.ok:raise RuntimeError('Groq HTTP '+str(r.status_code))
  content=str(r.json().get('choices',[{}])[0].get('message',{}).get('content','')).strip();a=content.find('{');b=content.rfind('}')
  if a<0 or b<=a:raise ValueError('AI health response was not JSON')
  result=json.loads(content[a:b+1])
  if result.get('status')!='ok' or result.get('test')!='passed':raise ValueError('AI health self-test returned unexpected content')
  previous=hs.get('status');hs.update({'last_check':time.time(),'last_success':time.time(),'status':'healthy','model':model,'latency_ms':round((time.time()-started)*1000),'last_error':'','consecutive_failures':0,'consecutive_successes':int(hs.get('consecutive_successes',0))+1,'last_response_valid':True})
  save(AI_HEALTH,hs)
  hs['recovery_start_alerted']=False;save(AI_HEALTH,hs);_ai_note(True)
  _n=int(hs.get('consecutive_successes',0))
  if hs.get('emergency_mode'):
   if _n<3:tg(f'🛠 *AUDIT — AI RECOVERY {_n}/3*\nCheck passed. {3-_n} more to go.')
   else:tg('🛠 *AUDIT — AI RECOVERY 3/3*\nFinal check passed. Restoring normal mode.')
  elif previous not in ('healthy',None) or hs.get('alerted_status') not in ('healthy',None):
   tg('✅ *AUDIT — AI HEALTHY AGAIN*\nThe AI answers correctly.')
  hs['alerted_status']='healthy';save(AI_HEALTH,hs)
  if hs.get('emergency_mode') and int(hs.get('consecutive_successes',0))>=3:ai_emergency_resume(True)
 except Exception as e:
  count=int(hs.get('consecutive_failures',0))+1;previous=hs.get('status');_prog=int(hs.get('consecutive_successes',0)) if hs.get('emergency_mode') else 0
  hs.update({'last_check':time.time(),'status':'unhealthy','model':model,'latency_ms':round((time.time()-started)*1000),'last_error':str(e)[:220],'consecutive_failures':count,'consecutive_successes':0,'last_response_valid':False})
  save(AI_HEALTH,hs)
  hs['alerted_status']='unhealthy';save(AI_HEALTH,hs)
  _ai_note(False,'health check: '+str(e));activate_ai_emergency('Groq health self-test failed: '+str(e)[:200])
  if _prog>0:tg(f'⚠️ *AUDIT — AI RECOVERY RESET*\nFailed at stage {_prog}/3 ({str(e)[:100]}). Restarting from 0/3. Auto-buy stays paused.')

def ai_market_context(s):
 """Market-regime interpretation; informational only."""
 if not (os.environ.get('GROQ_API_KEY','').strip() or GROQ_API_KEY):return None
 try:
  btc=data('BTC-USDT');reg=market_regime(s)
  payload={'btc_price':live('BTC-USDT'),'btc_rsi':btc[2] if btc else None,'btc_market_filter':btc_blocked(s),
   'btc_shock':btc_shock(s),'market_regime_rule_engine':str(reg)[:500], 'auto_buy_enabled':bool(s.get('auto_buy_enabled')),
   'risk_controls':risk_controls()[1] if not risk_controls()[0] else 'PASS'}
  return ai_stage('market_regime',payload,ttl=300,max_tokens=300)
 except Exception as e:print('AI market context error:',e);return None

def ask_ai(p):
 api_key=os.environ.get('GROQ_API_KEY','').strip() or GROQ_API_KEY
 model=ai_model()
 if not api_key:
  tg("⚠️ Groq AI is not configured. Create a free API key at https://console.groq.com/keys, add it as GROQ_API_KEY in your bot host's Secrets/Environment Variables, then restart the bot. GROQ_MODEL is optional (default: openai/gpt-oss-120b). The free tier has usage limits.");return
 try:
  db=load(DB);q=load(QF);s=cfg();bal=B('free','USDT');br=data("BTC-USDT")[2]
  pos=[]
  for sym,d in list(db.items())[:12]:
   entry=float(d.get('entry_price',0) or 0);px=live(sym) or entry
   pnl=((px/entry)-1)*100 if entry>0 and px>0 else 0.0
   pos.append(f"  {sym}: source={d.get('purchase_source','UNKNOWN')}, setup={d.get('strategy','UNKNOWN')}, entry={entry}, live={px}, unrealized_pct={pnl:+.2f}%, stop={d.get('stop_loss')}, age_min={max(0,(time.time()-float(d.get('opened_at',time.time()) or time.time()))/60):.0f}")
  if not pos:pos=["  (none)"]
  queue=[]
  for sym,qd in list(q.items())[:10] if isinstance(q,dict) else []:
   queue.append(f"  {sym}: setup={qd.get('type','?')}, score={qd.get('score','?')}, full_setup={qd.get('full','?')}")
  if not queue:queue=["  (no queued candidates)"]
  btc_state="BLOCKED" if btc_blocked(s) or btc_shock(s) else "not blocked"
  ctx=f"Free USDT: {bal:.2f}|AutoBuy: ${s['auto_buy_amount_usdt']}|BTC RSI: {br}|BTC filter: {btc_state}|AutoBuy enabled: {s.get('auto_buy_enabled',True)}\nOpen positions ({len(db)}):\n"+"\n".join(pos)+"\nScanner queue:\n"+"\n".join(queue)+f"\nRSI settings {s['rsi_min']}-{s['rsi_max']}, stop-loss {s['stop_loss_pct']}%, max spread {s.get('max_spread_pct')}%\n\n"
  prompt="You are the inbuilt crypto spot-trading analyst. Assess market regime, candidate quality, portfolio exposure, and each position's hold/reduce/exit risk. When BTC filter is blocking an otherwise attractive setup, clearly explain that the user may choose the existing manual /buy command; do not recommend bypassing other risk checks. Distinguish confirmed data from uncertainty, mention fees/slippage and avoid promising profit. You are advisory only: never claim to execute trades, never invent indicators, and never override deterministic bot risk controls. Give concise actionable analysis with BUY WATCH / HOLD / REDUCE / EXIT-style labels where supported. Context:\n"+ctx+"User question: "+str(p)
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
  fl=float(s.get('capital_floor_pct',75) or 0)   # last line of defence: a position is never held below 75% of its entry value
  if fl>0 and lv<=e*fl/100:
   if sell(sym,1.0,f"CAPITAL FLOOR (-{100-fl:g}%)"):db.pop(sym,None);save(DB,db);continue
  d['last_price']=lv
  if inc>=5.0 and d['stop_loss']<e:d['stop_loss']=e
  sp=sl_pct(sym,d);c=[(round(float(d.get('stop_ref') or e)*(1-sp/100),6),f"SL HIT ({sp}%)"),(d['stop_loss'],"STOP LOSS")]
  tpe=tp
  if tp>0 and s.get('dynamic_trail',True) and dt[3] and dt[4]:
   if r and r>=60 and lv>dt[3]>dt[4]:tpe=tp*1.25
   elif lv<dt[3] and inc>0:tpe=tp*0.75
  if tp>0:c.append((d['peak_price']*(1-tpe/100),f"TRAILING STOP ({tpe:.1f}%)"))
  lvl,why=max(c,key=lambda x:x[0])
  if lv<=lvl:
   fr=partial_stop_frac(d) if (why.startswith(("SL HIT","STOP LOSS")) and lvl<e*0.999) else 1.0
   if fr<1.0:
    coin=sym.split("-")[0]
    try:fb=float(B('total',coin) or 0)
    except Exception:fb=0.0
    eq=float(d.get('entry_qty') or 0)
    if 0<fb<=eq*0.30 or (fb>0 and fb*lv<1.02):frac_now=None   # exchange stop already sold the 75%
    else:frac_now=min(0.99,fr*eq/fb) if fb>0 else None
    ex_done=frac_now is None
    if not ex_done and frac_now>=0.99:fr=1.0
    elif ex_done or sell(sym,frac_now,f"STOP LOSS: {fr*100:.0f}% of entry amount"):
     try:rem=float(B('total',coin) or 0)
     except Exception:rem=0.0
     if rem*lv<1.02:db.pop(sym,None);save(DB,db);continue
     d['partial_done']=True;d['stop_ref']=lv;d['stop_loss']=round(lv*(1-sp/100),6);d.pop('xsl',None);db[sym]=d;save(DB,db)
     ok=place_trailing_sl(sym,rem)
     tg(f"🛡 *STOP: {fr*100:.0f}% OF ENTRY SOLD* {sym}\nThe remaining {rem:.8g} stays open with a new {sp:g}% stop at {d['stop_loss']:.8g}."+("" if ok else "\n⚠️ Exchange stop for the rest could not be placed; the bot is watching it."))
     continue
    else:continue
   if sell(sym,1.0,why):db.pop(sym,None);save(DB,db)
   continue
  # Dynamic RSI exit: reaching the RSI level only arms it. Hold while momentum continues (the trailing stop still protects);
  # sell when price drops below MA5, RSI falls 5 points from its peak, or RSI reaches 95.
  rx=float(s.get('rsi_exit',0) or 0)
  if r and rx>0:
   if r>=rx:d['rsi_peak']=max(float(d.get('rsi_peak',0) or 0),float(r))
   pk=float(d.get('rsi_peak',0) or 0)
   if pk>0:
    why=None
    if r>=95:why=f"RSI {r:.0f} (extreme)"
    elif dt[3] and lv<dt[3]:why=f"RSI exit: price below MA5 after RSI {pk:.0f}"
    elif r<=pk-5:why=f"RSI exit: RSI fell {pk:.0f}→{r:.0f}"
    if why and sell(sym,1.0,why):db.pop(sym,None);save(DB,db);continue
    db[sym]=d
  ratchet(sym,d,lvl,lv)
  # Stop watchdog: verify the exchange stop order and attempt re-arming on a cooldown.
  if s.get("stop_watchdog_enabled",True) and not in_cd(f"stopwatch_{sym}",900):
   mk_cd(f"stopwatch_{sym}")
   orders,verified=open_sells(sym)
   if not verified and not cd(f"stopunver_{sym}",3600):tg(f"⚠️ *STOP NOT VERIFIED:* could not read the exchange orders for {sym}. Checking again next cycle.")
   if verified and len(orders)!=1:
    tg(f"⚠️ *STOP WATCHDOG:* {sym} has {len(orders)} protective sell orders; attempting to restore one verified stop.")
    ok=place_trailing_sl(sym,B('free',sym.split('-')[0]))
    if not ok and s.get("sell_if_stop_fails",True):unprotected_exit(sym,db)
  db[sym]=d
 save(DB,db)
 # AI reviews run only after deterministic protective exits and stop maintenance have completed.
 if db and time.time()-_AI_LAST.get('portfolio_review',0)>=900:
  try:
   positions=[]
   for sy,dd in list(db.items())[:20]:
    en=float(dd.get('entry_price',0) or 0);px=live(sy) or en
    positions.append({'symbol':sy,'source':dd.get('purchase_source','UNKNOWN'),'setup':dd.get('strategy','UNKNOWN'),
     'entry':en,'price':px,'pnl_pct':round((px/en-1)*100,2) if en>0 and px>0 else None,
     'stop':dd.get('stop_loss'),'age_min':round((time.time()-float(dd.get('opened_at',time.time()) or time.time()))/60)})
   ai=ai_stage('portfolio_review',{'positions':positions,'btc_price':live('BTC-USDT'),'btc_filter':btc_blocked(s),
    'portfolio_risk':risk_controls()[1] if not risk_controls()[0] else 'PASS'},ttl=900,max_tokens=450)
   if ai:journal_event('ai_portfolio_review',summary=str(ai)[:900])
  except Exception as e:print('AI portfolio review error:',e)
 # Performance intelligence uses closed trade history; it cannot alter sizing or strategy rules.
 if time.time()-_AI_LAST.get('performance_review',0)>=1800:
  try:
   hist=load(HIST,[]);closed=[x for x in hist if isinstance(x,dict) and isinstance(x.get('pnl'),(int,float))][-30:]
   if closed:
    wins=sum(1 for x in closed if float(x.get('pnl',0))>0);net=sum(float(x.get('pnl',0)) for x in closed)
    ai=ai_stage('performance_review',{'sample_trades':len(closed),'wins':wins,'losses':len(closed)-wins,
     'win_rate_pct':round(wins/len(closed)*100,2),'net_pnl_usdt':round(net,4),
     'trades':[{'pnl':x.get('pnl'),'reason':x.get('reason'),'symbol':x.get('sym')} for x in closed[-20:]]},ttl=1800,max_tokens=350)
    if ai:journal_event('ai_performance_review',review=str(ai)[:900])
  except Exception as e:print('AI performance review error:',e)

# ============================== FILE: bitget_bot/components/06_risk_management.py ==============================
# Circuit breakers, exposure controls, risk sizing, execution quality
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
def journal_event(event, **fields):
 try:
  rows=load(JOURNAL,[]);rows=rows if isinstance(rows,list) else []
  rows.append({"time":time.time(),"event":str(event),**fields});save(JOURNAL,rows[-3000:])
 except Exception as e:print("JOURNAL WRITE ERR:",e)
def breaker_failure(reason):
 try:
  b=load(BREAKER,{});b=b if isinstance(b,dict) else {}
  recent=[float(x) for x in b.get("failures",[]) if time.time()-float(x)<900];recent.append(time.time())
  b.update(failures=recent,last_reason=str(reason)[:180])
  if len(recent)>=3:b.update(paused_until=time.time()+900,trigger="3 execution/API failures in 15 minutes")
  save(BREAKER,b);journal_event("circuit_breaker_failure",reason=str(reason)[:180],count=len(recent))
 except Exception as e:print("BREAKER WRITE ERR:",e)
def breaker_status():
 b=load(BREAKER,{})
 if not isinstance(b,dict):return ""
 until=float(b.get("paused_until",0) or 0)
 return f"Circuit breaker active for {(until-time.time())/60:.1f} more minutes: {b.get('trigger','operational failures')}" if until>time.time() else ""
def clear_recovered_breaker():
 b=load(BREAKER,{})
 if isinstance(b,dict) and b.get("paused_until") and float(b.get("paused_until",0))<=time.time():b.update(paused_until=0,failures=[]);save(BREAKER,b)
def portfolio_correlation_guard(sym, threshold=0.85):
 try:
  held=list(load(DB,{}).keys())
  if not held:return True,"No existing positions."
  import math
  def rets(pair):
   c=ex.fetch_ohlcv(S(pair),"1h",limit=73);v=[float(z[4]) for z in c[:-1] if float(z[4])>0]
   return [v[i]/v[i-1]-1 for i in range(1,len(v))]
  x=rets(sym)
  if len(x)<24:return True,"Correlation sample insufficient; neutral."
  for other in held:
   if other==sym:continue
   y=rets(other);n=min(len(x),len(y))
   if n<24:continue
   a=x[-n:];b=y[-n:];ma=sum(a)/n;mb=sum(b)/n;va=sum((z-ma)**2 for z in a);vb=sum((z-mb)**2 for z in b)
   if va<=0 or vb<=0:continue
   corr=sum((a[i]-ma)*(b[i]-mb) for i in range(n))/math.sqrt(va*vb)
   if corr>=threshold:return False,f"Correlation with held {other} is {corr:.2f} (limit {threshold:.2f})."
  return True,"Portfolio correlation acceptable."
 except Exception as e:return False,f"Correlation check unavailable: {str(e)[:100]}"
def estimate_market_impact(sym, spend):
 try:
  ob=ex.fetch_order_book(S(sym),limit=50);asks=ob.get("asks") or [];bids=ob.get("bids") or []
  if not asks or not bids:return False,None,"Order book unavailable for impact estimate."
  mid=(float(asks[0][0])+float(bids[0][0]))/2;left=float(spend);cost=qty=0.0
  for price,amount in asks:
   price=float(price);take=min(float(amount),left/price)
   if take>0:cost+=take*price;qty+=take;left-=take*price
   if left<=0:break
  if qty<=0 or left>max(.01,spend*.001):return False,None,"Visible asks cannot cover planned order."
  impact=(cost/qty-mid)/mid*100
  return True,impact,f"Estimated market impact {impact:.3f}%"
 except Exception as e:return False,None,f"Impact estimate failed: {str(e)[:100]}"

def net_pnl(t):return float(t.get('pnl',0) or 0)-float(t.get('fee',0) or 0)
def perf_breaker(s):
 # If the last N closed trades (counted from the last pause) lost money after fees, pause auto-buy for a while.
 n=int(s.get("perf_breaker_trades",10) or 0)
 if n<=0:return ""
 st=load(ST,{});pb=st.get("perf_breaker") or {};now=time.time()
 if float(pb.get("until",0) or 0)>now:return f"Performance breaker: last {n} trades lost ${abs(float(pb.get('loss',0))):.2f}; auto-buy paused for {(float(pb['until'])-now)/3600:.1f}h"
 H=[x for x in load(HIST) if isinstance(x,dict) and not str(x.get('reason','')).startswith('PARTIAL')]
 H=[x for x in H if float(x.get('time',0))>float(pb.get('since',0) or 0)][-n:]
 if len(H)<n:return ""
 tot=sum(net_pnl(x) for x in H)
 if tot>=0:return ""
 hrs=float(s.get("perf_breaker_hours",24.0) or 24.0)
 st["perf_breaker"]={"until":now+hrs*3600,"since":now,"loss":tot};save(ST,st)
 tg(f"🛑 *ALERT:* PERFORMANCE BREAKER\nThe last {n} trades lost ${abs(tot):.2f} after fees. Auto-buy is paused for {hrs:g}h. Stops and manual trading stay on.")
 return f"Performance breaker: last {n} trades lost ${abs(tot):.2f}; auto-buy paused for {hrs:g}h"
def risk_controls():
 s=cfg();now=time.time();breaker=breaker_status()
 if breaker:return False,breaker
 clear_recovered_breaker()
 day=[t for t in load(HIST) if now-float(t.get('time',0))<86400];realized=sum(float(t.get('pnl',0)) for t in day)
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
 try:
  pb=perf_breaker(s)
  if pb:return False,pb
 except Exception as e:print("PERF BREAKER ERR",str(e)[:100])
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
  factor=1.0  # Applied once in dynamic_auto_buy_budget, not double-counted here.
  return max(0.0,min(float(available_usdt),by_risk*factor,max_position*factor,remaining))
 except:return 0.0

def dynamic_auto_buy_budget(sym, qd, price, stop, available_usdt, s):
 """Adaptive order sizing bounded by risk, portfolio, signal quality, and wallet reserve."""
 try:
  score=float(qd.get("score",0) or 0); setup=str(qd.get("type","OTHER")).upper()
  if s.get("half_or_all_sizing",True):
   # Fixed rule: spend 50% of the free USDT; if 50% would be under the threshold (5 USDT), spend all of it (at least the 1.02 minimum).
   minsize=1.00;thr=float(s.get("half_or_all_threshold_usdt",5.0))   # Bitget minimum order is $1.00 (the 0.02 fee reserve is already taken off)
   half=(available_usdt+0.02)*0.5;size=half if half>=thr else available_usdt   # half of the FREE balance (available already has the $0.02 reserve off)
   if size<minsize:return 0.0,{"reason":f"balance ${available_usdt:.2f} below the ${minsize:.2f} minimum","score":score}
   return round(min(size,available_usdt),2),{"score_factor":1.0,"setup_factor":1.0,"vol_factor":1.0,"regime_factor":1.0,"score":score,"reason":f"50 percent of balance, or all-in when half is under ${thr:g}"}
  # Score multiplier is gradual and bounded, not a fixed amount per trade.
  score_factor=max(0.35,min(1.25,0.45+score/220.0))
  setup_factor={"BREAKOUT":1.0,"TREND":0.9,"PULLBACK":0.85,"BOUNCE":0.75}.get(setup,0.65)
  vol=atr_pct(sym);vol_factor=1.0 if not vol else max(0.45,min(1.0,4.0/max(float(vol),1.0)))
  regime=market_regime(s);regime_factor=float(regime.get("factor",0.65 if regime.get("name")=="UNKNOWN" else 1.0))
  # risk_sized_budget already constrains stop-distance, exposure and configured risk.
  base=risk_sized_budget(sym,price,stop,available_usdt)
  base=loss_streak_adjust(base,s)
  if weak_market(s):base*=max(0.1,min(1.0,float(s.get("weak_market_size_factor",0.5))))
  size=base*score_factor*setup_factor*vol_factor*regime_factor
  maxpct=float(s.get("dynamic_auto_buy_max_pct",20.0))/100.0
  try:eq=equity_now(); size=min(size,eq*maxpct) if eq>0 else 0
  except Exception:size=0
  hardcap=float(s.get("dynamic_auto_buy_max_usdt",0) or 0)
  if hardcap>0:size=min(size,hardcap)
  minsize=float(s.get("dynamic_auto_buy_min_usdt",1.02) or 1.02)
  if size<minsize:return 0.0,{"score_factor":score_factor,"setup_factor":setup_factor,"vol_factor":vol_factor,"regime_factor":regime_factor,"reason":"calculated size below exchange minimum"}
  return round(min(size,available_usdt),2),{"score_factor":score_factor,"setup_factor":setup_factor,"vol_factor":vol_factor,"regime_factor":regime_factor,"score":score,"regime":regime.get("name"),"reason":"adaptive sizing"}
 except Exception as e:return 0.0,{"reason":f"sizing error: {str(e)[:120]}"}

def guardian_audit(force=False):
 """Non-destructive Guardian checks; never edits strategy code or config limits."""
 s=cfg()
 if not s.get("guardian_enabled",True):return True,"Guardian disabled"
 st=load(GUARDIAN,{}) if isinstance(load(GUARDIAN,{}),dict) else {}
 now=time.time();interval=max(5,int(s.get("guardian_audit_interval_minutes",15))*60)
 if not force and now-float(st.get("last_audit",0))<interval:return bool(st.get("healthy",True)),st.get("summary","Audit recent")
 findings=[]
 for f in (CFG,DB,QF,SC,HIST):
  try:
   if os.path.exists(f):
    with open(f) as fh:json.load(fh)
  except Exception as e:findings.append(f"{f} unreadable/corrupt: {str(e)[:90]}")
 try:
  db=load(DB,{})
  if not isinstance(db,dict):findings.append("positions state is not a dictionary")
  else:
   for sym,d in db.items():
    if not isinstance(d,dict) or float(d.get("entry_price",0) or 0)<=0:findings.append(f"position record invalid: {sym}")
 except Exception as e:findings.append(f"position audit error: {e}")
 try:
  q=load(QF,{})
  if not isinstance(q,dict):findings.append("buy queue is not a dictionary")
  else:
   for sym,d in q.items():
    if not isinstance(d,dict) or not d.get("timestamp"):findings.append(f"queued signal invalid: {sym}")
 except Exception as e:findings.append(f"queue audit error: {e}")
 try:
  db_ok,db_msg=coin_db_integrity()
  if not db_ok:findings.append("coin database integrity: "+db_msg)
 except Exception as e:findings.append("coin database audit failed: "+str(e)[:120])
 # Store the immutable charter once, then validate its persisted structure on each audit.
 charter={"version":1,"mission":"Bitget spot trading; preserve capital, follow approved strategy logic, audit decisions and operational faults.","strategies":["TREND","PULLBACK","BOUNCE","BREAKOUT"],"constraints":["No leverage","Never silently alter strategy rules","No automatic liquidation solely from audit findings","Critical data-integrity uncertainty blocks new entries"]}
 if not os.path.exists(GCHARTER):save(GCHARTER,charter)
 else:
  saved=load(GCHARTER,{})
  if isinstance(saved,dict) and saved.get("version")==1 and saved.get("strategies") in (["TREND","OVERSOLD","BREAKOUT"],["TREND","BOUNCE","BREAKOUT"]):save(GCHARTER,charter);saved=charter   # approved upgrade: strategy list now has Pullback and Bounce
  if not isinstance(saved,dict) or saved.get("version")!=1 or saved.get("strategies")!=charter["strategies"]:findings.append("Core charter integrity mismatch")
 healthy=not findings;summary="Healthy" if healthy else "; ".join(findings)
 st.update(last_audit=now,healthy=healthy,summary=summary)
 save(GUARDIAN,st)
 ledger=load(GAUDIT,[]);ledger=ledger if isinstance(ledger,list) else []
 ledger.append({"time":now,"healthy":healthy,"findings":findings})
 save(GAUDIT,ledger[-500:])
 if findings and not in_cd("guardian_notice",1800):tg("🛡️ *BOT GUARDIAN ALERT*\n"+summary[:3000]);mk_cd("guardian_notice")
 return healthy,summary

def buy_rejection(sym,reason):
 coin_db_mark_execution(sym,"BLOCKED",reason,{"source":"auto_buy_execution"})
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
 # Preserve the configurable scanner gate; Coin List scoring separately requires real 4H evidence.
 if not s.get('mtf_confirm',True):return True
 return market_trend_4h(sym) is True
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
_REGIME={"t":0,"name":"UNKNOWN","factor":1.0,"reason":""}
def market_regime(s):
 """Conservative BTC regime snapshot; never overrides existing strategy signals."""
 if time.time()-_REGIME["t"]<300:return _REGIME.copy()
 name="UNKNOWN";factor=1.0;reason="Data unavailable; neutral sizing."
 try:
  o=ex.fetch_ohlcv("BTC/USDT",'1h',limit=60)
  c=[float(x[4]) for x in o[:-1]]
  if len(c)>=50:
   m20=sum(c[-20:])/20;m50=sum(c[-50:])/50;chg=(c[-1]-c[-24])/c[-24]*100 if c[-24]>0 else 0
   if c[-1]<m50 and m20<m50:name="BEARISH";factor=0.5;reason="BTC below declining medium averages."
   elif abs(chg)>=5:name="HIGH VOLATILITY";factor=0.5;reason=f"BTC 24h move {chg:+.1f}% is extreme."
   elif c[-1]>m20>m50:name="BULLISH";factor=1.0;reason="BTC trend is positive."
   else:name="SIDEWAYS";factor=0.75;reason="BTC trend is mixed/sideways."
 except Exception as e:reason=f"Regime unavailable ({str(e)[:80]})."
 _REGIME.update(t=time.time(),name=name,factor=factor,reason=reason);return _REGIME.copy()
def execution_quality(sym,s):
 """Reject weak execution conditions without placing orders."""
 try:
  ob=ex.fetch_order_book(S(sym),limit=20);bids=ob.get('bids') or [];asks=ob.get('asks') or []
  if not bids or not asks:return False,"Order-book depth unavailable."
  bid=float(bids[0][0]);ask=float(asks[0][0]);mid=(bid+ask)/2
  if mid<=0:return False,"Invalid order-book midpoint."
  spr=(ask-bid)/mid*100
  if spr>float(s.get('max_spread_pct',0.5)):return False,f"Order-book spread {spr:.2f}% exceeds limit."
  depth=sum(float(x[0])*float(x[1]) for x in bids[:10])+sum(float(x[0])*float(x[1]) for x in asks[:10])
  if depth<float(s.get('liquidity_depth_usdt',5000)):return False,f"Top-10 book depth ${depth:.0f} below limit."
  imb=sum(float(x[0])*float(x[1]) for x in bids[:10]);askd=sum(float(x[0])*float(x[1]) for x in asks[:10]);total=imb+askd
  pct=max(imb,askd)/total*100 if total else 100
  if pct>float(s.get('max_book_imbalance_pct',85)):return False,f"Order-book imbalance {pct:.0f}% is unusually concentrated."
  return True,f"Spread {spr:.2f}%; depth ${depth:.0f}."
 except Exception as e:return False,f"Execution quality check failed: {str(e)[:120]}"
def coin_exposure_pct(sym):
 try:
  eq=equity_now();q=B('total',sym.split('-')[0]);px=live(sym)
  return q*px/eq*100 if eq>0 and px else 0.0
 except Exception:return 0.0
def stale_signal(qd,s):
 try:return time.time()-float(qd.get('timestamp',0))>float(s.get('signal_expiry_minutes',30))*60
 except:return True
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
  if not cd('risk_pause_notice',3600):tg(f"🛑 *RISK BREAKER:* {reason}. Paused.")
  return
 try:
  ub=B('free','USDT')
  reserve_pct=max(0.0,min(50.0,float(s.get('auto_buy_reserve_pct',5.0))))
  # 50% / all-in sizing keeps only the $0.02 fee reserve (Bitget minimum order is $1.00, so $1.02 free is enough); otherwise the % reserve applies.
  ub=max(0.0,ub-0.02) if s.get("half_or_all_sizing",True) else max(0.0,ub*(1-reserve_pct/100.0))
  for sym,qd in sorted(q.items(),key=lambda x:float(x[1].get('score',0)),reverse=True):
   db_ready,db_reason=coin_db_can_execute(sym,qd)
   if not db_ready:
    buy_rejection(sym,db_reason);q.pop(sym,None);save(QF,q);continue
   if in_cd(f"fail_{sym}",300):buy_rejection(sym,"Previous fail.");continue
   if sym in db:buy_rejection(sym,"Position exists.");q.pop(sym,None);save(QF,q);continue
   if not live(sym):buy_rejection(sym,"No price.");continue
   if str(qd.get("type","")).upper()=="BOUNCE" and weak_market(s):buy_rejection(sym,"Weak market: BOUNCE skipped.");continue
   if stale_signal(qd,s):buy_rejection(sym,"Queued signal expired; waiting for a fresh scan.");q.pop(sym,None);save(QF,q);continue
   if s.get("smart_risk_enabled",True):
    eqok,eqwhy=execution_quality(sym,s)
    if not eqok:buy_rejection(sym,eqwhy);continue
    coinlim=float(s.get("max_coin_exposure_pct",20.0))
    if coin_exposure_pct(sym)>coinlim:buy_rejection(sym,f"Coin exposure exceeds {coinlim:g}% of equity.");continue
    corr_ok,corr_reason=portfolio_correlation_guard(sym,float(s.get("max_portfolio_correlation",0.85)))
    if not corr_ok:buy_rejection(sym,corr_reason);continue
   hrs=float(s.get('rebuy_cooldown_hours',6.0))
   if hrs>0 and in_cd(f"stopout_{sym}",hrs*3600):buy_rejection(sym,f"Re-buy cooldown ({hrs:g}h after a loss).");q.pop(sym,None);save(QF,q);continue
   sprd=spread_pct(sym);lim=float(s.get('max_spread_pct',0.5))
   if sprd is None or sprd>lim:buy_rejection(sym,f"Spread too wide or unavailable (limit {lim:g}%).");continue
   spend=ub
   if len(db)>=mx:buy_rejection(sym,f"Max positions ({mx}).");continue
   if spend<1.00:buy_rejection(sym,f"Insufficient USDT (${spend:.2f}).");q.pop(sym,None);save(QF,q);continue
   cap=float(s.get('max_open_risk_pct',3.0) or 0)
   if cap>0 and db and not s.get("half_or_all_sizing",True):
    orp=open_risk_pct()
    if orp+float(s.get('risk_per_trade_pct',1.0))>cap:buy_rejection(sym,f"Combined open risk {orp:.1f}% (limit {cap:g}%).");continue
   pct=sl_pct(sym)
   if qd.get('type')=="BREAKOUT":pct=min(pct,float(s.get('breakout_stop_pct',2.0)))   # breakouts get a very tight stop
   sp,alloc_meta=dynamic_auto_buy_budget(sym,qd,live(sym),qd.get('sl') or live(sym)*(1-pct/100),spend,s)
   if sp<(1.00 if s.get("half_or_all_sizing",True) else 1.02):buy_rejection(sym,f"Size too small (${sp:.2f}).");continue
   impact_ok,impact,impact_reason=estimate_market_impact(sym,sp);impact_limit=float(s.get("execution_slippage_pct",0.35))
   if not impact_ok or impact is None or impact>impact_limit:
    buy_rejection(sym,impact_reason if not impact_ok else f"Estimated impact {impact:.3f}% exceeds {impact_limit:.3f}%.");continue
   # AI supervises and warns; it never approves, vetoes, or changes a deterministic order.
   ai=ai_stage('buy_advisory',{'symbol':sym,'setup':qd.get('type'),'score':qd.get('score'),'price':live(sym),
    'rsi':data(sym)[2],'spread_pct':sprd,'estimated_impact_pct':impact,'spend_usdt':sp,
    'btc_filter':btc_blocked(s),'market_filter':market_filter(s),'risk_controls':'PASS'},ttl=120,max_tokens=250,soft=True)
   if ai:
    journal_event('ai_buy_advisory',symbol=sym,advisory=ai)
    qd['ai_advisory']=str(ai.get('warning') or ai.get('summary') or '')[:240]
   journal_event("buy_order_submitted",symbol=sym,spend_usdt=sp,estimated_impact_pct=impact,setup=qd.get("type","OTHER"))
   try:f,a=m_buy(S(sym),sp)
   except Exception as e:
    breaker_failure(f"BUY {sym}: {e}");journal_event("buy_order_failed",symbol=sym,error=str(e)[:250]);buy_rejection(sym,f"Order failed: {e}");mk_cd(f"fail_{sym}");continue
   if f<=0 or a<=0:
    breaker_failure(f"BUY {sym}: fill not verified");journal_event("buy_fill_unverified",symbol=sym,filled=f,average=a);buy_rejection(sym,"Exchange fill could not be verified.");continue
   journal_event("buy_fill_verified",symbol=sym,filled=f,average=a,spend_estimate=f*a)
   mxs=round(a*(1-pct/100),6);sl=qd.get('sl') or mxs
   if sl>=a or sl>mxs:sl=mxs
   if qd.get('type')=="BREAKOUT":sl=max(sl,mxs)
   db[sym]=NP(round(a,6),sl,pct);db[sym].update(entry_qty=float(f),entry_usdt=round(float(f)*float(a),4));db[sym]['strategy']=qd.get('type','OTHER');db[sym]['purchase_source']='AUTO';db[sym]['opened_at']=time.time();save(DB,db)
   coin_db_mark_execution(sym,"BOUGHT","Auto-buy fill confirmed",{"filled":f,"average":a,"spend_usdt":sp,"setup":qd.get("type")})
   ok=place_trailing_sl(sym,free_qty(sym,f)) or protect_retry(sym,f);tg(f"🛒 *EXECUTED — DYNAMIC AUTO-BUY!*\n{sym}|💵{sp:.2f}@{a:.6f}\nAllocation: score {alloc_meta.get('score_factor',0):.2f}x, setup {alloc_meta.get('setup_factor',0):.2f}x, volatility {alloc_meta.get('vol_factor',0):.2f}x, regime {alloc_meta.get('regime_factor',0):.2f}x\n"+(STOP_OK if ok else STOP_NO))
   if not ok and s.get('sell_if_stop_fails',True):unprotected_exit(sym,db)
   q.pop(sym,None);save(QF,q);ub=B('free','USDT');time.sleep(2);break
 except Exception as e:tg(f"⚠️ *QUEUE FAILED:* `{e}`")

# ============================== FILE: bitget_bot/components/07_scan_history_and_database.py ==============================
# Scan history, coin database, candidate classification, audits
def scan_history_state():
 h=load(SH,{})
 if not isinstance(h,dict):h={}
 now=time.time();start=float(h.get("window_start",0) or 0)
 if not start or now-start>=86400:h={"window_start":now,"items":[]};save(SH,h)
 h.setdefault("items",[])
 try:
  db_items=coin_db_history_records()
  if db_items:h["items"]=db_items
 except Exception as e:print("COIN DB HISTORY READ ERR",str(e)[:120])
 return h
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
 h=scan_history_state();raw_items=h.get("items",[])
 # One row per coin and outcome type (latest only): repeated scans of the same coin no longer inflate the count.
 _lat={}
 for _x in raw_items:
  _lat[(str(_x.get("sym","")),str(_x.get("decision","SKIP")).upper().startswith("SETUP"))]=_x
 items=list(_lat.values());n_scans=len({x.get("time") for x in raw_items})
 try: parts=shlex.split(txt)[1:]
 except: parts=txt.split()[1:]
 filters=[];page=1;limit=150;bad=[]
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
 def _rk(x):
  d=str(x.get("decision","")).upper()
  return (0 if d.startswith("SETUP") else 1 if x.get("near_miss") else 2,int(x.get("rank",10**9) or 10**9),-float(x.get("time",0) or 0))
 found=sorted((x for x in items if matches(x)),key=_rk)
 shown=found[:limit];changed=False
 for x in shown:
  if not isinstance(x.get("rsi"),(int,float)) and "candle fetch" not in str(x.get("reason","")):
   _rsi_value=scan_history_rsi(str(x.get("sym","")))
   if isinstance(_rsi_value,(int,float)):x["rsi"]=_rsi_value;changed=True
 if changed:
  _h2=dict(h);_h2["items"]=[x for x in h.get("items",[]) if str(x.get("decision","")).startswith("SETUP")][-300:];save(SH,_h2)  # full history lives in coins.db
 age=max(0,time.time()-h.get("window_start",time.time()));cs=cfg();rows=[]
 for x in shown:
  r=x.get("rsi");rsi=f"{r:.0f}" if isinstance(r,(int,float)) else "-"
  hh=x.get("h1");h1=f"{hh:+.1f}" if isinstance(hh,(int,float)) else "-"
  sym=str(x.get("sym","?")).replace("-USDT","")[:7]
  rows.append(f"{sym:<7}{rsi:>4}{h1:>6} {_hist_code(x,cs)[:14]}")
 table="COIN    RSI    1H WHY\n"+"-"*30+"\n"+"\n".join(rows)
 query=" ".join(parts) if parts else "all coins"
 more=f" (first {len(shown)})" if len(found)>len(shown) else ""
 msg=f"🔎 *SCAN HISTORY* · {len(found)}/{len(items)} coins{more}\n{n_scans} scans · {age/3600:.1f}h"+("" if not parts else " · "+query)+f"\n```\n{table}\n```"
 _send(msg,"Markdown",{"inline_keyboard":[[{"text":"🔍 Why not bought?","callback_data":"m:whylist:0"}],[{"text":"⬅️ Analyse","callback_data":"/analyse"}]]})
def _hist_code(x,s):
 d=str(x.get("decision","SKIP")).upper();rs=str(x.get("reason",""))
 if "NOT QUEUED" in d:
  rl=rs.lower()
  return "Mkt filter" if "market" in rl else "BTC filter" if "btc" in rl else "Auto-buy off" if "paused" in rl else "Cooldown" if "cooldown" in rl else "Guardian" if "guardian" in rl else "Blocked"
 if "QUEUED" in d:return "QUEUED"
 if "BOUGHT" in d or d.startswith("BUY"):return "BOUGHT"
 if "SOLD" in d or "SELL" in d:return "SOLD"
 if "Top 15" in rs:return "Top 15"
 if "Vol below" in rs:return "Vol low"
 if "24H change" in rs:return "24h range"
 if "Spread" in rs:return "Spread wide"
 if "No candle" in rs:return "No data"
 if "4h trend" in rs:return "4h down"
 r=x.get("rsi");hh=x.get("h1")
 if x.get("near_miss"):return "Near miss"
 try:
  lo,hi=float(s["rsi_min"]),float(s["rsi_max"])
  if isinstance(r,(int,float)):
   if r>hi:return "RSI high"
   if r<lo:return "RSI low"
  if isinstance(hh,(int,float)) and mom_on(s):
   if hh<float(s.get("mom_1h_min_pct",0.5)):return "1H low"
   if hh>float(s.get("mom_1h_max_pct",6.0)):return "1H high"
  if None not in (x.get("price"),x.get("ma5"),x.get("ma10")) and not (x["price"]>x["ma5"]>x["ma10"]):return "Trend weak"
 except Exception:pass
 return "No setup"
def _why_latest():
 h=scan_history_state();best={}
 for x in h.get("items",[]):best[str(x.get("sym",""))]=x
 return best
def ui_whylist(pg=0):
 best=_why_latest()
 def rk(x):
  d=str(x.get("decision","")).upper()
  return (0 if d.startswith("SETUP") else 1 if x.get("near_miss") else 2,int(x.get("rank",9999) or 9999))
 coins=[x for x in sorted(best.values(),key=rk) if int(x.get("rank",0) or 0)>15 and x.get("rsi") is not None]
 per=18;n=max(1,(len(coins)+per-1)//per);pg=max(0,min(pg,n-1))
 nav=[]
 if pg>0:nav.append(_B("◀️ Prev",f"m:whylist:{pg-1}"))
 if pg<n-1:nav.append(_B("Next ▶️",f"m:whylist:{pg+1}"))
 ui_send(f"🔍 *WHY NOT BOUGHT?* (page {pg+1}/{n})\nSetups first, then near-misses, then the rest. Tap a coin.",_grid([_B(str(x['sym']).split('-')[0],"m:why:"+str(x['sym']).split('-')[0]) for x in coins[pg*per:(pg+1)*per]],3)+([nav] if nav else [])+[[_B("⬅️ Scan History","m:scan_hist")]])
def why_text(coin):
 s=cfg();best=_why_latest();x=best.get(coin.upper()+"-USDT")
 if not x:return f"🔍 *WHY: {coin}*\nNo scan record in the history window."
 vol=float(x.get("vol",0) or 0);mv=float(x.get("min_volume",0) or 0)
 L=[f"🔍 *WHY: {coin}* — {str(x.get('decision','SKIP'))}",str(x.get('reason','-'))[:90]]
 h=x.get("h1");r=x.get("rsi");p=x.get("price");ma5=x.get("ma5");ma10=x.get("ma10");chg=float(x.get("chg",0) or 0)
 L.append(f"#{x.get('rank')} · ${vol/1e6:.1f}M"+(f" (min ${mv/1e6:.0f}M)" if mv else ""))
 lo,hi=float(s['rsi_min']),float(s['rsi_max']);a,b=float(s.get('mom_1h_min_pct',0.5)),float(s.get('mom_1h_max_pct',6.0))
 def ck(ok,t):L.append(("✅ " if ok else "❌ ")+t)
 ck(pre_ok(chg,s),f"24h {chg:+.1f}%")
 if h is None:L.append("❔ 1H: no data")
 else:ck(a<=h<=b,f"1H {h:+.2f}% (need {a:g}-{b:g})")
 if r is None:L.append("❔ RSI: no data")
 else:ck(lo<=r<=hi,f"RSI {r:.1f} (need {lo:g}-{hi:g})")
 if None in (p,ma5,ma10):L.append("❔ Trend: no data")
 else:ck(p>ma5>ma10,"Price > MA5 > MA10")
 if "4h trend" in str(x.get("reason","")):L.append("❌ 4h trend down")
 if x.get("near_miss"):L.append("🎯 "+str(x["near_miss"])[:60])
 return "\n".join(L)
def send_near_miss(s,ai_market=None):
 if not s.get('near_miss_alerts',True) or not _NM:return
 cdm=float(s.get('near_miss_cooldown_minutes',60))*60
 cands=[c for c in sorted(_NM,key=lambda x:x["score"]) if not in_cd("nm_"+c["sym"],cdm) and c["sym"].split("-")[0] not in [k.split("-")[0] for k in load(DB)]][:int(s.get('near_miss_max_per_scan',3))]
 if not cands:return
 amap={}
 ar=ai_stage('near_miss_review',{'market_regime':ai_market or {},'btc_filter_active':bool(btc_blocked(s)),'candidates':[{k:c[k] for k in ('sym','why','rsi','h1','chg')} for c in cands]},ttl=120,max_tokens=500,soft=True)
 if ar and isinstance(ar.get('reviews'),list):amap={str(x.get('symbol','')).upper().replace('/','-'):x for x in ar['reviews'] if isinstance(x,dict)}
 lines=["🎯 *ALERT:* COIN CLOSE TO YOUR FILTERS","_Almost a TREND setup. Nothing was bought._",""];rows=[]
 for c in cands:
  coin=c["sym"].split("-")[0];av=amap.get(c["sym"].upper())
  lines+=[f"*{coin}* — {fp(c['price'])} USDT | 24h {c['chg']:+.2f}% | 1H {c['h1']:+.2f}% | RSI {c['rsi']}",f"Missed by: {c['why']}","🧠 AI: "+((str(av.get('view','')).upper()+" — "+str(av.get('reason',''))[:140]) if av else "unavailable"),""]
  rows.append([_B(f"🛒 Buy {coin}",f"m:b:{coin}"),_B(f"🔎 Analyse {coin}",f"m:a:{coin}")])
  mk_cd("nm_"+c["sym"])
 _create("\n".join(lines)[:3900],"Markdown",{"inline_keyboard":rows},silent=False)
def coin_db_conn():
 import sqlite3
 c=sqlite3.connect(COINDB,timeout=20)
 c.execute("PRAGMA busy_timeout=20000")
 c.execute("CREATE TABLE IF NOT EXISTS coins(sym TEXT PRIMARY KEY,first_seen REAL,last_seen REAL,scans INTEGER,price REAL,chg24 REAL,h1 REAL,rsi REAL,ma5 REAL,ma10 REAL,vol REAL,candles INTEGER,status TEXT,setup TEXT,reason TEXT)")
 c.execute("CREATE TABLE IF NOT EXISTS snaps(sym TEXT,t REAL,price REAL,h1 REAL,rsi REAL,status TEXT)")
 c.execute("CREATE TABLE IF NOT EXISTS outcomes(id INTEGER PRIMARY KEY AUTOINCREMENT,sym TEXT,stage TEXT,t0 REAL,p0 REAL,p1 REAL,p4 REAL,p24 REAL,hi REAL,lo REAL,done INTEGER DEFAULT 0)")
 c.execute("CREATE INDEX IF NOT EXISTS snaps_sym ON snaps(sym,t)")
 cc={x[1] for x in c.execute("PRAGMA table_info(coins)")}
 for n,t in (("tag","TEXT"),("signal_status","TEXT"),("execution_status","TEXT"),("checks_json","TEXT"),("decision_at","REAL"),("expires_at","REAL"),("scan_id","TEXT"),("ai_advisory","TEXT"),("ai_advisory_at","REAL"),("manual_score","REAL"),("manual_candidate","INTEGER"),("stage","TEXT"),("stage_ok_at","REAL"),("manual_reason","TEXT")):
  if n not in cc:c.execute(f"ALTER TABLE coins ADD COLUMN {n} {t}")
 sc={x[1] for x in c.execute("PRAGMA table_info(snaps)")}
 for n,t in (("decision","TEXT"),("reason","TEXT"),("execution_status","TEXT"),("scan_id","TEXT"),
              ("rank","INTEGER"),("vol","REAL"),("chg","REAL"),("ma5","REAL"),("ma10","REAL"),
              ("min_volume","REAL"),("spread_pct","REAL"),("near_miss_json","TEXT")):
  if n not in sc:c.execute(f"ALTER TABLE snaps ADD COLUMN {n} {t}")
 c.execute("CREATE TABLE IF NOT EXISTS execution_events(id INTEGER PRIMARY KEY AUTOINCREMENT,t REAL,sym TEXT,status TEXT,reason TEXT,details_json TEXT)")
 c.execute("CREATE INDEX IF NOT EXISTS execution_events_sym ON execution_events(sym,t)")
 c.commit()
 return c
_REJ=("Vol below","Spread","Top 15","24H","No candle","Not evaluated","Skipped")
def coin_status(rec,held):
 d=str(rec.get("decision",""));r=str(rec.get("reason",""))
 if rec.get("sym") in held:return "BOUGHT"
 if d.startswith("SETUP"):return "READY"
 if r.startswith(_REJ):return "REJECTED"
 return "WATCHING"
def _dbq(sql,args=(),one=False,write=False):
 # One place for the open / run / commit / close pattern used by the coin database helpers.
 c=coin_db_conn()
 try:
  cur=c.execute(sql,args)
  if write:c.commit();return None
  return cur.fetchone() if one else cur.fetchall()
 finally:c.close()
def coin_db_integrity():
 c=coin_db_conn()
 try:
  r=c.execute("PRAGMA quick_check").fetchone()
  cols={x[1] for x in c.execute("PRAGMA table_info(coins)")}
  need={"sym","first_seen","last_seen","scans","status","signal_status","execution_status","checks_json"}
  missing=need-cols
  return (bool(r and str(r[0]).lower()=="ok" and not missing),
          "SQLite healthy" if r and str(r[0]).lower()=="ok" and not missing else "SQLite quick_check/schema failed: "+", ".join(sorted(missing)))
 finally:c.close()
def coin_db_mark_execution(sym,status,reason="",details=None):
 c=None
 try:
  now=time.time();status=str(status).upper();reason=str(reason or "")[:240];c=coin_db_conn()
  row=c.execute("SELECT first_seen,scans,signal_status,checks_json,status FROM coins WHERE sym=?",(sym,)).fetchone()
  fs,sc,signal,raw,legacy=row if row else (now,0,"MANUAL","{}","WATCHING")
  try:checks=json.loads(raw or "{}")
  except Exception:checks={}
  checks["last_execution_event"]={"status":status,"reason":reason,"time":now,"details":details or {}}
  if status=="BOUGHT":legacy="BOUGHT";signal=signal or "MANUAL"
  elif status=="SOLD":legacy="READY" if signal=="READY" else "WATCHING"
  c.execute("""INSERT INTO coins(sym,first_seen,last_seen,scans,status,signal_status,execution_status,checks_json,decision_at,reason)
   VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(sym) DO UPDATE SET last_seen=excluded.last_seen,status=excluded.status,
   signal_status=COALESCE(coins.signal_status,excluded.signal_status),execution_status=excluded.execution_status,
   checks_json=excluded.checks_json,decision_at=excluded.decision_at,
   reason=CASE WHEN excluded.reason<>'' THEN excluded.reason ELSE coins.reason END""",
   (sym,fs,now,sc,legacy,signal,status,json.dumps(checks,default=str),now,reason))
  c.execute("INSERT INTO execution_events(t,sym,status,reason,details_json) VALUES(?,?,?,?,?)",
   (now,sym,status,reason,json.dumps(details or {},default=str)))
  c.commit()
 except Exception as e:print("COIN EXECUTION STATE ERR",sym,str(e)[:150])
 finally:
  if c:
   try:c.close()
   except:pass
def _num(v):
 # finite float or None
 try:
  x=float(v);return x if x==x and abs(x)!=float("inf") else None
 except Exception:return None
def _fmt(v,fmt):return format(float(v),fmt) if isinstance(v,(int,float)) else "-"
def setup_tag(rec,s):
 # Which of the four setups a coin currently looks like: TREND, PULLBACK, BOUNCE or BREAKOUT (None if none).
 try:
  p=_num(rec.get("price"));r=_num(rec.get("rsi"));m5=_num(rec.get("ma5"));m10=_num(rec.get("ma10"));h=_num(rec.get("h1"));pc=_num(rec.get("prev_close"))
  if p is None or r is None or m5 is None or m10 is None:return None
  lo,hi=float(s.get("mom_1h_min_pct",0.5)),float(s.get("mom_1h_max_pct",6.0))
  h1ok=True if not mom_on(s) else (h is not None and lo<=h<=hi)
  rmin,rmax=float(s.get("rsi_min",42)),float(s.get("rsi_max",48));pmin=float(s.get("pullback_rsi_min",40))
  if p>m5>m10 and rmin<=r<=rmax and h1ok:return "TREND"
  if m5>m10 and m10<=p<=m5 and pmin<=r<=rmax and h1ok:return "PULLBACK"
  if s.get("oversold_enabled",True) and r<30 and pc and p>pc and h is not None and lo<=h<=hi:return "BOUNCE"
  if rec.get("breakout_confirmed") is True and p>m5>m10 and 50.0<=r<=70.0:return "BREAKOUT"
 except Exception:pass
 return None
def manual_candidate_assess(rec,settings=None):
 """Approved Coin List score: 7 mandatory core checks + 3 additional checks.
 WATCH needs >=5/7 core; MANUAL needs >=5/7 core and >=2/3 additional;
 AUTO is only considered at 7/7 core + 3/3 additional and still faces all
 independent execution/risk gates. Missing evidence never earns a point.
 """
 s=settings or cfg();tag=setup_tag(rec,s)
 try:
  vol=_num(rec.get("vol"));minvol=_num(rec.get("min_volume"));spread=_num(rec.get("spread_pct"))
  price=_num(rec.get("price"));rsi=_num(rec.get("rsi"));ma5=_num(rec.get("ma5"));ma10=_num(rec.get("ma10"))
  chg=_num(rec.get("chg"));trend=rec.get("market_trend_4h")
  btc=rec.get("btc_market_safe");high=_num(rec.get("high_24h"));rr=_num(rec.get("rr"))
  h1v=_num(rec.get("h1"));pcl=_num(rec.get("prev_close"))
  _pb_ok=None if price is None or ma5 is None or ma10 is None else (ma5>ma10 and ma10<=price<=ma5)
  _rmax=float(s.get("rsi_max",48));_chgmax=float(s.get("max_24h_pct",100.0))
  core={
   "liquidity":None if vol is None or minvol is None or minvol<=0 else vol>=minvol,
   "spread":None if spread is None else 0<=spread<=float(s.get("max_spread_pct",0.5)),
   "market_trend":None if trend is None else trend is True,
   "price_structure":(_pb_ok if tag=="PULLBACK" else (None if pcl is None or price is None else price>pcl) if tag=="BOUNCE" else (None if price is None or ma5 is None or ma10 is None else price>ma5>ma10)),
   "rsi_condition":(None if rsi is None else (float(s.get("pullback_rsi_min",40))<=rsi<=_rmax if tag=="PULLBACK" else rsi<30 if tag=="BOUNCE" else 50.0<=rsi<=70.0 if tag=="BREAKOUT" else float(s.get("rsi_min",42))<=rsi<=_rmax)),
   "momentum_24h":None if chg is None else ((chg<=_chgmax) if tag=="BOUNCE" else 0.0<chg<=_chgmax),
   "btc_market_safety":None if btc is None else btc is True,
  }
  brk=rec.get("breakout_confirmed")
  volx=_num(rec.get("volume_expansion_ratio"))
  additional={
   "breakout_confirmation":None if brk is None else brk is True,
   "volume_expansion":None if volx is None else volx>=1.5,
   "risk_reward_quality":None if rr is None else rr>=1.5,
  }
  core_score=sum(v is True for v in core.values());additional_score=sum(v is True for v in additional.values());score=core_score+additional_score
  # HARD GATES: always required, no score can override them. Missing evidence fails a gate.
  gates={
   "rsi_ceiling":None if rsi is None else (rsi<30 if tag=="BOUNCE" else rsi<=70.0 if tag=="BREAKOUT" else rsi<=_rmax),
   "price_above_ma":(_pb_ok if tag=="PULLBACK" else (None if price is None else True) if tag=="BOUNCE" else (None if price is None or ma5 is None or ma10 is None else price>ma5>ma10)),
   "momentum_1h":(True if not mom_on(s) else (None if h1v is None else float(s.get("mom_1h_min_pct",0.5))<=h1v<=float(s.get("mom_1h_max_pct",6.0)))),
   "momentum_24h_up":None if chg is None else ((chg<=_chgmax) if tag=="BOUNCE" else 0.0<chg<=_chgmax),
  }
  hard_ok=all(v is True for v in gates.values())
  glabels={"rsi_ceiling":"RSI at or below the entry ceiling","price_above_ma":"Price above MA5 above MA10","momentum_1h":"1H momentum in range","momentum_24h_up":"24H change above 0%"}
  gfailed=[glabels[k] for k,v in gates.items() if v is not True]
  auto=hard_ok and core_score==7 and additional_score==3
  manual=hard_ok and core_score>=5 and additional_score>=2 and not auto
  labels={"liquidity":"Liquidity (24H quote volume)","spread":"Bid/ask spread","market_trend":"4H market trend","price_structure":"Price above MA5 above MA10",
   "rsi_condition":"RSI within configured entry range","momentum_24h":"24H change within safe range","btc_market_safety":"BTC market safety",
   "breakout_confirmation":"20H resistance breakout","volume_expansion":"Hourly volume expansion ≥1.5x","risk_reward_quality":"Risk/reward ≥1.5:1"}
  failed=[labels[k] for group in (core,additional) for k,v in group.items() if v is False]
  unknown=[labels[k] for group in (core,additional) for k,v in group.items() if v is None]
  reason=(f"HARD GATE failed: {', '.join(gfailed)}; never Manual or Auto" if not hard_ok else
          f"AUTO score: 7/7 core + 3/3 additional; independent safety gates still required" if auto else
          (f"MANUAL: {core_score}/7 core + {additional_score}/3 additional; manual review only" if manual else
           (f"WATCH: {core_score}/7 core; need at least 5/7" if core_score>=5 else f"DROPPED threshold: {core_score}/7 core; need at least 5/7")))
  if failed:reason += "; failed: "+", ".join(failed)
  if unknown:reason += "; unavailable: "+", ".join(unknown)
  checks={"core":core,"additional":additional,"core_score":core_score,"additional_score":additional_score,"raw_score":score,"labels":labels,"risk_reward":rr,"volume_expansion_ratio":volx,"gates":gates,"hard_ok":hard_ok,"setup":tag}
  if not hard_ok:score=min(score,6)   # a failed hard gate can never show a Manual/Auto-level score
  return score,manual,reason,checks
 except Exception as e:
  return 0,False,"Unable to score safely: "+str(e)[:100],{"core":{},"additional":{},"core_score":0,"additional_score":0}

def coin_outcome_track(c,sym,prev,stg,price,now):
 # Record what a coin does after it reaches Watch / Manual / Auto, so the rules can be judged by evidence.
 if not price or float(price)<=0:return
 price=float(price)
 for oid,t0,p0,p1,p4,p24,hi,lo in c.execute("SELECT id,t0,p0,p1,p4,p24,hi,lo FROM outcomes WHERE sym=? AND done=0",(sym,)).fetchall():
  age=now-float(t0);hi=max(float(hi or p0),price);lo=min(float(lo or p0),price)
  if p1 is None and age>=3600:p1=price
  if p4 is None and age>=14400:p4=price
  done=0
  if p24 is None and age>=86400:p24=price;done=1
  if age>172800:done=1
  c.execute("UPDATE outcomes SET p1=?,p4=?,p24=?,hi=?,lo=?,done=? WHERE id=?",(p1,p4,p24,hi,lo,done,oid))
 if stg in ("WATCH","MANUAL","AUTO") and stg!=prev:
  c.execute("INSERT INTO outcomes(sym,stage,t0,p0,hi,lo) VALUES(?,?,?,?,?,?)",(sym,stg,now,price,price,price))
def coin_stage_results():
 # Average result per stage. A 4h gain counts as a win only if it beats round-trip fees.
 out={};fee=float(cfg().get("fee_pct_side",0.1))*2
 c=coin_db_conn()
 try:
  for stg in ("WATCH","MANUAL","AUTO"):
   rows=c.execute("SELECT p0,p1,p4,p24,hi,lo FROM outcomes WHERE stage=? AND p4 IS NOT NULL",(stg,)).fetchall()
   if not rows:out[stg]=None;continue
   pc=lambda a,b:(float(b)/float(a)-1)*100
   r4=[pc(x[0],x[2]) for x in rows];r1=[pc(x[0],x[1]) for x in rows if x[1]];r24=[pc(x[0],x[3]) for x in rows if x[3]]
   av=lambda l:sum(l)/len(l) if l else None
   out[stg]={"n":len(rows),"win4":sum(1 for v in r4 if v>fee)/len(r4)*100,"r1":av(r1),"r4":av(r4),"r24":av(r24),"up":av([pc(x[0],x[4]) for x in rows]),"down":av([pc(x[0],x[5]) for x in rows])}
 finally:c.close()
 return out
def stage_decide(prev,watch_ok,manual_ok,auto_ok,ok_at,now):
 """Lifecycle: a coin moves up ONE stage per scan (Watch > Manual > Auto), never skips, and moves down when it fails a stage.
 A coin that stops qualifying even for Watch is kept on Watch for 24h, then becomes DROPPED (recoverable). Re-entry always starts at Watch."""
 prev=prev or ""
 if not watch_ok:
  if prev in ("WATCH","MANUAL","AUTO"):
   if ok_at and now-float(ok_at)<86400:return "WATCH",ok_at
   return "DROPPED",ok_at
  return prev,ok_at
 if prev in ("","DROPPED","ARCHIVED"):return "WATCH",now
 if prev=="WATCH":return ("MANUAL" if manual_ok else "WATCH"),now
 if auto_ok:return "AUTO",now
 if manual_ok:return "MANUAL",now
 return "WATCH",now
def coin_stage_from_scores(prev,manual_checks,ok_at,now):
  """Score thresholds (7 core + 3 additional) AND the hard gates. A coin that fails a hard gate can never be Manual or Auto."""
  mc=manual_checks if isinstance(manual_checks,dict) else {}
  core=mc.get("core",{}) if isinstance(mc.get("core"),dict) else {}
  additional=mc.get("additional",{}) if isinstance(mc.get("additional"),dict) else {}
  core_score=int(mc.get("core_score",sum(v is True for v in core.values())));additional_score=int(mc.get("additional_score",sum(v is True for v in additional.values())))
  keys=("liquidity","spread","market_trend","price_structure","rsi_condition","momentum_24h","btc_market_safety")
  unknown=sum(1 for k in keys if core.get(k) is None)
  hard_ok=bool(mc.get("hard_ok",False))
  manual=hard_ok and core_score>=5 and additional_score>=2
  auto=hard_ok and core_score==7 and additional_score==3
  if hard_ok and core_score>=5:return stage_decide(prev,True,manual,auto,ok_at,now)
  if hard_ok and core_score+unknown>=5 and prev in ("WATCH","MANUAL","AUTO"):return (prev if prev=="WATCH" else "WATCH"),ok_at
  # A failed hard gate (RSI above the ceiling, below the MAs, 1H/24H not rising) leaves the Watch list immediately: no 24h grace.
  return stage_decide(prev,False,False,False,(ok_at if hard_ok else None),now)
def coin_stage_prev(sym):
 try:
  c=coin_db_conn()
  try:r=c.execute("SELECT stage FROM coins WHERE sym=?",(sym,)).fetchone()
  finally:c.close()
  return (r[0] or "") if r else ""
 except Exception:return ""
_GATES={}
def gate_report_text(s=None):
 # Scan-report block: how many analysed coins each hard gate blocked, then the rules that are set. Information only; changes no rule.
 g=_GATES
 if not g.get("n"):return ""
 try:s=s or cfg()
 except Exception:s=s or {}
 tg_=g.get("tags") or {}
 h1=f"1H +{float(s.get('mom_1h_min_pct',0.5)):g} to +{float(s.get('mom_1h_max_pct',6.0)):g}%" if mom_on(s) else "1H off"
 L=[f"🚧 *GATES* ({g['n']} analysed, {g.get('pass',0)} passed)",
    f"Blocked: RSI {g.get('RSI',0)} | MA {g.get('MA',0)} | 1H {g.get('1H',0)} | 24H {g.get('24H',0)} | Score {g.get('Score',0)}",
    f"Setups: Trend {tg_.get('TREND',0)} | Pullback {tg_.get('PULLBACK',0)} | Bounce {tg_.get('BOUNCE',0)} | Breakout {tg_.get('BREAKOUT',0)}",
    f"Rules: RSI ≤ {float(s.get('rsi_max',48)):g}, Price > MA5 > MA10, {h1}, 24H up, Core 5+ and Extra 2+"]
 return "\n".join(L)
def coin_db_update(records,held,newc=None,keep_days=3,keep_snaps=24,queue=None,buying_allowed=True,block_reason="",settings=None):
 now=time.time();held=set(held);newc=newc or {};queue=queue if isinstance(queue,dict) else {};settings=settings or {};c=coin_db_conn()
 _GATES.clear();_GATES.update({"n":0,"pass":0,"RSI":0,"MA":0,"1H":0,"24H":0,"Score":0,"tags":{"TREND":0,"PULLBACK":0,"BOUNCE":0,"BREAKOUT":0}})
 try:
  for rec in records:
   sym=rec.get("sym")
   if not sym:continue
   legacy=coin_status(rec,held);decision=str(rec.get("decision",""));reason=str(rec.get("reason",""))
   manual_score,manual_candidate,manual_reason,manual_checks=manual_candidate_assess(rec,settings)
   try:
    if rec.get("rsi") is not None and sym not in held:
     _GATES["n"]+=1;_g=manual_checks.get("gates") or {}
     if manual_checks.get("setup") in _GATES["tags"]:_GATES["tags"][manual_checks["setup"]]+=1
     for _k,_lab in (("rsi_ceiling","RSI"),("price_above_ma","MA"),("momentum_1h","1H"),("momentum_24h_up","24H")):
      if _g.get(_k) is not True:_GATES[_lab]+=1
     if manual_checks.get("hard_ok"):
      _GATES["pass"]+=1
      if int(manual_checks.get("core_score",0))<5 or int(manual_checks.get("additional_score",0))<2:_GATES["Score"]+=1
   except Exception:pass
   signal="READY" if decision.startswith("SETUP") else ("REJECTED" if reason.startswith(_REJ) else "WATCHING")
   qd=queue.get(sym) if isinstance(queue.get(sym),dict) else None
   ts=float(rec.get("time") or now);expiry=float((qd or {}).get("expires_at") or ts+float(settings.get("signal_expiry_minutes",30))*60)
   execution="HELD" if sym in held else "QUEUED" if qd and signal=="READY" and expiry>now else "BLOCKED" if signal=="READY" else "NOT_ELIGIBLE"
   prev=c.execute("SELECT h1 FROM snaps WHERE sym=? AND h1 IS NOT NULL ORDER BY t DESC LIMIT 2",(sym,)).fetchall()
   h1_now=rec.get("h1")
   rising_3=bool(isinstance(h1_now,(int,float)) and len(prev)==2 and prev[0][0] is not None and prev[1][0] is not None
                 and float(h1_now)>float(prev[0][0])>float(prev[1][0]))
   checks={"scanner":{"decision":decision,"reason":reason,"rank":rec.get("rank"),"min_volume":rec.get("min_volume"),
    "volume_24h":rec.get("vol"),"change_24h_pct":rec.get("chg"),"price":rec.get("price"),"h1_pct":rec.get("h1"),
    "rsi":rec.get("rsi"),"ma5":rec.get("ma5"),"ma10":rec.get("ma10"),"spread_pct":rec.get("spread_pct"),
    "strategy":rec.get("strategy"),"near_miss":rec.get("near_miss"),"h1_rising_three_scans":rising_3,
    "trend_4h_ok":rec.get("trend_4h_ok"),"rising_guard_ok":rec.get("rising_guard_ok"),
    "manual_candidate":{"score":manual_score,"eligible":manual_candidate,"reason":manual_reason,"checks":manual_checks,"threshold":{"watch_core":5,"manual_core":5,"manual_additional":2,"auto_core":7,"auto_additional":3}}},
    "execution":{"status":execution,"buying_allowed_at_scan":bool(buying_allowed),"block_reason":str(block_reason or "")[:240],
    "queue_timestamp":(qd or {}).get("timestamp"),"queue_score":(qd or {}).get("score"),"signal_expires_at":expiry}}
   row=c.execute("SELECT first_seen,scans,stage,stage_ok_at FROM coins WHERE sym=?",(sym,)).fetchone();fs,sc=(row[0],row[1]) if row else (now,0)
   if sym in held:stg,sok="",None
   else:
    _prev_stage=row[2] if row else ""
    stg,sok=coin_stage_from_scores(_prev_stage,manual_checks,row[3] if row else None,now)
   if stg!="AUTO" and execution=="QUEUED":execution="BLOCKED"
   try:coin_outcome_track(c,sym,(row[2] if row else "") or "",stg,rec.get("price"),now)
   except Exception as _e:print("OUTCOME ERR",str(_e)[:80])
   _tag=manual_checks.get("setup") or rec.get("strategy")
   advisory=str((qd or {}).get("ai_advisory",""))[:240]
   c.execute("""INSERT INTO coins(sym,first_seen,last_seen,scans,price,chg24,h1,rsi,ma5,ma10,vol,candles,status,setup,reason,
    signal_status,execution_status,checks_json,decision_at,expires_at,scan_id,ai_advisory,manual_score,manual_candidate,manual_reason,stage,stage_ok_at)
    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(sym) DO UPDATE SET
    last_seen=excluded.last_seen,scans=excluded.scans,price=excluded.price,chg24=excluded.chg24,h1=excluded.h1,
    rsi=excluded.rsi,ma5=excluded.ma5,ma10=excluded.ma10,vol=excluded.vol,candles=excluded.candles,status=excluded.status,
    setup=excluded.setup,reason=excluded.reason,signal_status=excluded.signal_status,execution_status=excluded.execution_status,
    checks_json=excluded.checks_json,decision_at=excluded.decision_at,expires_at=excluded.expires_at,
    scan_id=excluded.scan_id,ai_advisory=COALESCE(NULLIF(excluded.ai_advisory,''),coins.ai_advisory),
    manual_score=excluded.manual_score,manual_candidate=excluded.manual_candidate,manual_reason=excluded.manual_reason,stage=excluded.stage,stage_ok_at=excluded.stage_ok_at""",
    (sym,fs,now,sc+1,rec.get("price"),rec.get("chg"),rec.get("h1"),rec.get("rsi"),rec.get("ma5"),rec.get("ma10"),
     rec.get("vol"),rec.get("candles"),legacy,rec.get("strategy"),reason[:240],signal,execution,json.dumps(checks,default=str),
     now,expiry,str(rec.get("time") or int(now)),advisory,manual_score,int(manual_candidate),manual_reason[:240],stg,sok))
   c.execute("UPDATE coins SET tag=? WHERE sym=?",(_tag or "",sym))
   c.execute("""INSERT INTO snaps(sym,t,price,h1,rsi,status,decision,reason,execution_status,scan_id,
    rank,vol,chg,ma5,ma10,min_volume,spread_pct,near_miss_json)
    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
    (sym,now,rec.get("price"),rec.get("h1"),rec.get("rsi"),legacy,decision,reason[:240],execution,
     str(rec.get("time") or int(now)),rec.get("rank"),rec.get("vol"),rec.get("chg"),rec.get("ma5"),rec.get("ma10"),
     rec.get("min_volume"),rec.get("spread_pct"),json.dumps(rec.get("near_miss")) if rec.get("near_miss") else None))
  for sym,(p,h,n) in newc.items():
   row=c.execute("SELECT first_seen,scans FROM coins WHERE sym=?",(sym,)).fetchone();fs,sc=row if row else (now,0)
   checks={"scanner":{"decision":"NEW","reason":f"Too new to score ({n} candles)","candles":n,"h1_pct":h},
           "execution":{"status":"NOT_ELIGIBLE","reason":"Insufficient candle history"}}
   c.execute("""INSERT INTO coins(sym,first_seen,last_seen,scans,price,h1,candles,status,reason,signal_status,execution_status,
    checks_json,decision_at,scan_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(sym) DO UPDATE SET
    last_seen=excluded.last_seen,scans=excluded.scans,price=excluded.price,h1=excluded.h1,candles=excluded.candles,
    status='NEW',reason=excluded.reason,signal_status='NEW',execution_status='NOT_ELIGIBLE',checks_json=excluded.checks_json,
    decision_at=excluded.decision_at,scan_id=excluded.scan_id""",
    (sym,fs,now,sc+1,p,h,n,"NEW",f"New Bitget spot pair; too new to score ({n} hourly candles)","NEW","NOT_ELIGIBLE",json.dumps(checks),now,str(int(now))))
   c.execute("UPDATE coins SET stage='WATCH',stage_ok_at=COALESCE(stage_ok_at,?) WHERE sym=? AND (stage IS NULL OR stage='')",(now,sym))
   c.execute("INSERT INTO snaps(sym,t,price,h1,rsi,status,decision,reason,execution_status,scan_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
    (sym,now,p,h,None,"NEW","NEW",f"Too new to score ({n} candles)","NOT_ELIGIBLE",str(int(now))))
  c.execute("UPDATE coins SET stage='DROPPED' WHERE stage IN ('WATCH','MANUAL','AUTO') AND status<>'BOUGHT' AND last_seen<?",(now-86400,))
  c.execute("UPDATE coins SET stage='ARCHIVED' WHERE stage IN ('WATCH','MANUAL','AUTO','DROPPED') AND status<>'BOUGHT' AND last_seen<?",(now-30*86400,))
  c.execute("DELETE FROM snaps WHERE rowid IN (SELECT rowid FROM (SELECT rowid,ROW_NUMBER() OVER (PARTITION BY sym ORDER BY t DESC) rn FROM snaps) WHERE rn>?)",(keep_snaps,))
  c.commit()
 finally:c.close()
def coin_db_can_execute(sym,qd):
 c=coin_db_conn()
 try:
  row=c.execute("SELECT signal_status,execution_status,expires_at,status,stage,manual_score FROM coins WHERE sym=?",(sym,)).fetchone()
  if not row:return False,"No coin database record; waiting for a fresh scanner decision."
  signal,execution,expiry,legacy,stage,manual_score=row
  if stage!="AUTO" or int(float(manual_score or 0))!=10:return False,f"Stage/score is {stage or 'none'} / {manual_score or 0}/10; AUTO requires 10/10."
  if signal!="READY":return False,f"Database signal status is {signal or 'UNKNOWN'}, not READY."
  if execution!="QUEUED":return False,f"Database execution status is {execution or 'UNASSESSED'}, not QUEUED."
  if expiry is not None and float(expiry)<=time.time():return False,"Database signal expired; waiting for a fresh scan."
  if legacy=="BOUGHT":return False,"Database says this coin is already held."
  return True,"Database confirms READY and QUEUED."
 finally:c.close()
def coin_db_ai_rows(limit=25):
 rows=_dbq("""SELECT sym,signal_status,execution_status,price,chg24,h1,rsi,scans,reason,setup
   FROM coins ORDER BY CASE signal_status WHEN 'READY' THEN 0 WHEN 'NEW' THEN 1 WHEN 'WATCHING' THEN 2 ELSE 3 END,
   last_seen DESC LIMIT ?""",(int(limit),))
 return [{"symbol":x[0],"signal_status":x[1],"execution_status":x[2],"price":x[3],"change_24h_pct":x[4],
   "change_1h_pct":x[5],"rsi":x[6],"scans_seen":x[7],"scanner_reason":x[8],"setup":x[9]} for x in rows]
def coin_db_set_ai_advisory(sym,comment):
 _dbq("UPDATE coins SET ai_advisory=?,ai_advisory_at=? WHERE sym=?",(str(comment or "")[:300],time.time(),sym),write=True)
def coin_db_get(sym):
 row=_dbq("SELECT first_seen,last_seen,scans,signal_status,execution_status,reason,ai_advisory,checks_json,ai_advisory_at FROM coins WHERE sym=?",(sym,),one=True)
 if not row:return None
 try:checks=json.loads(row[7] or "{}")
 except Exception:checks={}
 return {"first_seen":row[0],"last_seen":row[1],"scans":row[2],"signal_status":row[3],
   "execution_status":row[4],"reason":row[5],"ai_advisory":row[6],"checks":checks,"ai_advisory_at":row[8]}
def coin_db_history_records(hours=24,limit=60000):
 c=coin_db_conn()
 try:
  rows=c.execute("""SELECT s.sym,s.t,s.price,s.h1,s.rsi,s.status,s.decision,
   CASE WHEN c.execution_status IN ('BLOCKED','EXPIRED','FAILED') THEN c.reason ELSE s.reason END,
   COALESCE(c.execution_status,s.execution_status),s.scan_id,s.rank,s.vol,s.chg,s.ma5,s.ma10,
   s.min_volume,s.spread_pct,s.near_miss_json
   FROM snaps s LEFT JOIN coins c ON c.sym=s.sym WHERE s.t>=? ORDER BY s.t DESC LIMIT ?""",
   (time.time()-float(hours)*3600,int(limit))).fetchall()
  out=[]
  for x in rows:
   try:nm=json.loads(x[17]) if x[17] else None
   except Exception:nm=None
   out.append({"sym":x[0],"time":x[1],"price":x[2],"h1":x[3],"rsi":x[4],"status":x[5],
    "decision":x[6] or "SKIP","reason":x[7] or "","execution_status":x[8],"scan_id":x[9],
    "rank":x[10],"vol":x[11],"chg":x[12],"ma5":x[13],"ma10":x[14],"min_volume":x[15],
    "spread_pct":x[16],"near_miss":nm})
  return out
 finally:c.close()
def coin_db_auto_ready_candidates(limit=3):
 """Return only fresh, unexpired Coin List records approved for automatic execution."""
 fresh_after=time.time()-max(1800.0,float(cfg().get("scan_interval_minutes",15.0))*120.0)
 rows=_dbq("""SELECT sym,price,h1,rsi,scans,execution_status,last_seen,expires_at
   FROM coins WHERE signal_status='READY' AND execution_status='QUEUED' AND status<>'BOUGHT' AND manual_score=10
   AND last_seen>=? AND (expires_at IS NULL OR expires_at>?)
   ORDER BY COALESCE(h1,-999) DESC,last_seen DESC LIMIT ?""",(fresh_after,time.time(),int(limit)))
 return [{"sym":x[0],"price":x[1],"h1":x[2],"rsi":x[3],"scans":x[4],"execution_status":x[5],"last_seen":x[6],"expires_at":x[7]} for x in rows]
def coin_db_manual_candidates(limit=10):
 rows=_dbq("SELECT sym,price,chg24,h1,rsi,scans,manual_score,manual_reason,reason,execution_status,tag FROM coins WHERE manual_candidate=1 AND stage='MANUAL' AND manual_score BETWEEN 7 AND 9 AND status<>'BOUGHT' ORDER BY manual_score DESC,last_seen DESC LIMIT ?",(int(limit),))
 return [{"sym":x[0],"price":x[1],"chg":x[2],"h1":x[3],"rsi":x[4],"scans":x[5],"score":x[6],"manual_reason":x[7],"scanner_reason":x[8],"execution_status":x[9],"tag":x[10]} for x in rows]
def coin_db_stage_list(stage,limit=20):
 rows=_dbq("SELECT sym,price,chg24,h1,rsi,scans,first_seen,manual_score,tag FROM coins WHERE stage=? AND status<>'BOUGHT' ORDER BY COALESCE(h1,-99) DESC,last_seen DESC LIMIT ?",(stage,int(limit)))
 return [{"sym":x[0],"price":x[1],"chg":x[2],"h1":x[3],"rsi":x[4],"scans":x[5],"first_seen":x[6],"score":x[7],"tag":x[8]} for x in rows]
def coin_db_stage_counts():
 try:
  out=dict(_dbq("SELECT stage,COUNT(*) FROM coins WHERE stage IN ('WATCH','MANUAL','AUTO','DROPPED','ARCHIVED') AND status<>'BOUGHT' GROUP BY stage"))
  out.update(WATCH=out.get("WATCH",0),MANUAL=out.get("MANUAL",0),AUTO=out.get("AUTO",0),W=out.get("WATCH",0),M=out.get("MANUAL",0),A=out.get("AUTO",0))
  return out
 except Exception:return {}
def coin_db_rows(limit=12):
 c=coin_db_conn()
 try:
  cnt=dict(c.execute("SELECT status,COUNT(*) FROM coins GROUP BY status").fetchall())
  ec=dict(c.execute("SELECT execution_status,COUNT(*) FROM coins GROUP BY execution_status").fetchall())
  fresh_after=time.time()-max(1800.0,float(cfg().get("scan_interval_minutes",15.0))*120.0)
  cnt["MANUAL_CANDIDATE"]=c.execute("SELECT COUNT(*) FROM coins WHERE manual_candidate=1 AND stage='MANUAL' AND status<>'BOUGHT'").fetchone()[0]
  cnt["W"]=c.execute("SELECT COUNT(*) FROM coins WHERE stage='WATCH' AND status<>'BOUGHT'").fetchone()[0]
  cnt["A"]=c.execute("SELECT COUNT(*) FROM coins WHERE stage='AUTO' AND status<>'BOUGHT'").fetchone()[0]
  cnt["DROPPED"]=c.execute("SELECT COUNT(*) FROM coins WHERE stage='DROPPED' AND status<>'BOUGHT'").fetchone()[0]
  cnt["ARCHIVED"]=c.execute("SELECT COUNT(*) FROM coins WHERE stage='ARCHIVED' AND status<>'BOUGHT'").fetchone()[0]
  rows=c.execute("SELECT sym,status,execution_status,price,h1,rsi,scans,reason,(manual_candidate=1 AND stage='MANUAL'),manual_score,ai_advisory,checks_json,stage FROM coins WHERE status IN ('READY','NEW','BOUGHT') OR stage IN ('WATCH','MANUAL','AUTO','DROPPED','ARCHIVED') ORDER BY CASE WHEN stage='AUTO' THEN 0 WHEN stage='MANUAL' THEN 1 WHEN stage='WATCH' THEN 2 WHEN stage='DROPPED' THEN 3 WHEN stage='ARCHIVED' THEN 4 WHEN status='BOUGHT' THEN 5 ELSE 6 END,COALESCE(h1,-99) DESC LIMIT ?",(limit,)).fetchall()
  return cnt,rows,ec
 finally:c.close()
def cmd_coins():
 try:cnt,rows,ec=coin_db_rows();dropped=coin_db_stage_list("DROPPED",5)
 except Exception as e:ui_send(f"❌ Coin list error: {e}",[_MENU_BTN]);return
 if not cnt:ui_send("📚 *COIN LIST*\nEmpty. It fills after the next scan.",[_MENU_BTN]);return
 ic={"READY":"✅","NEW":"🆕","BOUGHT":"💼"};sic={"WATCH":"👁","MANUAL":"🟡","AUTO":"🤖"}
 lines=["📚 *COIN LIST*",f"✅ Ready {cnt.get('READY',0)} | 🆕 New {cnt.get('NEW',0)} | 💼 Held {cnt.get('BOUGHT',0)} | ⛔ Rejected {cnt.get('REJECTED',0)}",
  f"Pipeline: 👁 Watch {cnt.get('WATCH',0)} → 🟡 Manual {cnt.get('MANUAL',0)} → 🤖 Auto {cnt.get('AUTO',0)} | ⤵ Dropped {cnt.get('DROPPED',0)} | Archived {cnt.get('ARCHIVED',0)}",
  f"Execution: 📥 queued {ec.get('QUEUED',0)} | ⛔ blocked {ec.get('BLOCKED',0)} | 🟢 held {ec.get('HELD',0)}",""]
 for sym,st,exst,p,h,r,n,why,mc,mscore,adv,cj,stg in rows:
  try:rising=bool(json.loads(cj or "{}").get("scanner",{}).get("h1_rising_three_scans"))
  except Exception:rising=False
  ln=f"{ic.get(st) if st in ('BOUGHT','NEW') else (sic.get(stg) or ic.get(st,''))} *{sym.split('-')[0]}* {fp(p) if p else '-'} | {exst or 'UNASSESSED'}"
  if mc:ln+=f" | 🟡 {int(float(mscore or 0))}/10 MANUAL"
  if h is not None:ln+=f" | 1H {h:+.2f}%"+(" 📈" if rising else "")
  if r is not None:ln+=f" | RSI {r:.0f}"
  lines.append(ln+f" | seen {n}x")
  if adv:lines.append("   🧠 "+str(adv)[:110])
 if dropped:
  lines += ["", "⤵ *DROPPED — retained for recovery:* "+", ".join(f"{x['sym'].split('-')[0]} ({int(float(x.get('score') or 0))}/10)" for x in dropped)]
 _send("\n".join(lines),"Markdown",{"inline_keyboard":[_MENU_BTN]})
def rising_vs_last_scan(sym,p,r,now):
 # Falling-knife guard: the price must be higher and RSI not lower than at the previous scan (within the last hour).
 try:
  row=_dbq("SELECT t,price,rsi FROM snaps WHERE sym=? AND price IS NOT NULL AND rsi IS NOT NULL ORDER BY t DESC LIMIT 1",(sym,),one=True)
 except Exception:return False,"Falling-knife guard: scan-history database unavailable; confirmation failed"
 if not row or now-float(row[0])>3600:return False,"Falling-knife guard: waiting for the next scan to confirm it is rising"
 pp,pr_=float(row[1]),float(row[2])
 if float(p)>pp and float(r)>=pr_:return True,""
 return False,f"Falling-knife guard: not rising vs last scan (price {fp(pp)}→{fp(float(p))}, RSI {pr_:.0f}→{float(r):.0f})"
def coin_db_maintenance():
 # Once a day: drop old execution events and orphaned readings, then compact the file. Changes no trading behaviour.
 if cd("coindb_maint",86400):return
 c=None
 try:
  c=coin_db_conn();now=time.time()
  c.execute("DELETE FROM execution_events WHERE t<?",(now-14*86400,))
  c.execute("DELETE FROM execution_events WHERE id NOT IN (SELECT id FROM execution_events ORDER BY id DESC LIMIT 5000)")
  c.execute("DELETE FROM snaps WHERE sym NOT IN (SELECT sym FROM coins)")
  c.execute("DELETE FROM outcomes WHERE t0<?",(now-10*86400,))
  c.commit();c.execute("VACUUM")
 except Exception as e:print("COIN DB MAINT ERR",str(e)[:120])
 finally:
  if c:
   try:c.close()
   except Exception:pass
def audit_ai():
 """AI health checks, diagnosis and fixing are owned by the audit. Returns (class, cause, fix, fixable) while the AI has a problem, else None."""
 ai_health_check()
 info=ai_diagnose_and_fix()
 if info and info[0] in ("MODEL","FORMAT","SERVER","NETWORK") and info[3]:
  if info[0] in ("SERVER","NETWORK"):time.sleep(5)
  ai_health_check(True)          # retry right away with the fix / after a short wait
  info=ai_diagnose_and_fix()
 return info
def system_audit():
 """Runs on every run: checks every layer, fixes what it can, and reports only changes."""
 s=cfg();now=time.time();fixed=[];down={}
 # 1 Exchange
 ok=False;err=""
 for _ in range(2):
  try:ex.fetch_balance();ok=True;break
  except Exception as e:err=str(e)[:80];time.sleep(2)
 if not ok:down["Exchange"]=err or "no answer"
 # 2 Coin database
 try:good=bool(coin_db_integrity()[0])
 except Exception:good=False
 if not good:
  try:
   if os.path.exists(COINDB):os.replace(COINDB,COINDB+".bad")
   coin_db_conn().close();good=bool(coin_db_integrity()[0])
  except Exception as e:good=False
  if good:fixed.append("Database rebuilt (history refills on the next scans)")
  else:down["Database"]="could not rebuild"
 # 3 Queue file
 try:
  q=load(QF)
  if not isinstance(q,dict):save(QF,{});fixed.append("Queue file reset")
 except Exception:down["Queue"]="unreadable"
 # 4 Stops: every tracked position must carry a stop
 try:
  db=load(DB);ch=False
  for sym,d in db.items():
   if isinstance(d,dict) and not d.get("stop_loss") and d.get("entry_price"):
    d["stop_loss"]=round(float(d["entry_price"])*(1-float(sl_pct(sym,d))/100),6);ch=True;fixed.append(f"Stop restored for {sym.split('-')[0]}")
  if ch:save(DB,db)
 except Exception as e:down["Stops"]=str(e)[:60]
 # 5 Scanner freshness
 try:
  lim=max(2700.0,float(s.get('scan_interval_minutes',15) or 15)*180.0);last=float(load(SC,{}).get('last',0) or 0)
  if last>0 and now-last>lim:
   try:scan(True)
   except Exception as e:print("AUDIT SCAN ERR",str(e)[:100])
   last=float(load(SC,{}).get('last',0) or 0)
   if time.time()-last<=lim:fixed.append("Scanner was stale; ran a scan")
   else:down["Scanner"]=f"no scan for {(time.time()-last)/60:.0f} min"
 except Exception as e:down["Scanner"]=str(e)[:60]
 # 6 Buyer safety (guardian)
 try:
  healthy,summary=guardian_audit(False)
  if not healthy:
   healthy,summary=guardian_audit(True)
   if not healthy:down["Buyer"]=str(summary)[:80]
 except Exception as e:down["Buyer"]=str(e)[:60]
 # 7 AI (the recovery stages send their own audit messages)
 ai_info=None
 try:ai_info=audit_ai()
 except Exception as e:print("AUDIT AI ERR",str(e)[:100])
 ai_line=""
 try:
  st0=load(ST,{});lastcls=st0.get("audit_ai_cls","")
  if ai_info:
   cls,cause,fix,fixable=ai_info
   if not fixable:down["AI"]=cause+". "+fix
   if cls!=lastcls and fixable:ai_line=f"AI — {cause}. {fix}"
   st0["audit_ai_cls"]=cls
  else:
   if lastcls:fixed.append("AI problem cleared ("+AI_CAUSE.get(lastcls,lastcls)+")")
   st0["audit_ai_cls"]=""
  save(ST,st0)
 except Exception as e:print("AUDIT AI DIAG ERR",str(e)[:100])
 if ai_line:fixed.append(ai_line)
 # report only changes
 st=load(ST,{});prev=st.get("audit_down",{}) if isinstance(st.get("audit_down"),dict) else {}
 new=[k for k in down if k not in prev];back=[k for k in prev if k not in down]
 remind=bool(down) and not new and not cd("audit_remind",3600)
 if fixed or new or back or remind:
  head="*ALERT:* AUDIT — "+str(len(down))+" layer(s) down" if down else "AUDIT"
  lines=["🛠 "+head]+[f"✅ Fixed: {x}" for x in fixed]+[f"✅ Back: {k}" for k in back]+[f"❌ Down: {k} — {v}" for k,v in down.items()]
  tg("\n".join(lines))
 st=load(ST,{});st["audit_down"]=down;st["audit_last"]=now;save(ST,st)
def send_new_alerts(s):
 # New tokens (under 50 hourly candles) cannot be scored; alert only, never auto-buy. Rare: one alert per coin per 3h, max 2 per scan.
 try:
  if not s.get('new_token_alerts',True) or not _NEWC:return
  mn=float(s.get('mom_1h_min_pct',0.5));held=[k.split("-")[0] for k in load(DB)]
  c=[(sym,v) for sym,v in sorted(_NEWC.items(),key=lambda x:-x[1][1]) if v[1]>=mn and sym.split("-")[0] not in held and not in_cd("new_"+sym,10800)][:2]
  if not c:return
  lines=["🆕 *ALERT:* NEW BITGET SPOT PAIR MOVING","_Recently detected pair with limited hourly history. No RSI/trend qualification yet. Nothing was bought; early listings are high risk._",""];rows=[]
  for sym,(p,h,n) in c:
   coin=sym.split("-")[0];lines.append(f"*{coin}* — {fp(p)} USDT | 1H {h:+.2f}% | {n} hourly candles available")
   rows.append([_B(f"🛒 Buy {coin}",f"m:b:{coin}"),_B(f"🔎 Analyse {coin}",f"m:a:{coin}")]);mk_cd("new_"+sym)
  _create("\n".join(lines)[:3900],"Markdown",{"inline_keyboard":rows},silent=False)
 finally:_NEWC.clear()

# ============================== FILE: bitget_bot/components/08_scanner.py ==============================
# Watchers, new listings, market scanning, settings commands
def fast_watch():
 # Once a minute in live mode: re-check the radar (coins near the entry rules) so alerts fire without waiting for the next full scan.
 s=cfg()
 if not (s.get('near_miss_alerts',True) and mom_on(s)):return
 if time.time()-float(load(SC,{}).get('last',0) or 0)<45:return
 radar=(load(ST,{}).get('radar') or [])[:int(s.get('radar_max',30))]
 if not radar:return
 try:tk=ex.fetch_tickers()
 except Exception as e:print("FASTWATCH TICKERS",str(e)[:100]);return
 from concurrent.futures import ThreadPoolExecutor
 with ThreadPoolExecutor(max_workers=4) as pool:res=list(pool.map(_calc,radar))
 _NM.clear();hits=[]
 for sym,(p,pr,r,ma5,ma10) in zip(radar,res):
  if not p or r is None or ma5 is None or ma10 is None:continue
  chg=float(((tk.get(sym.replace('-','/')) or {}).get('percentage')) or 0)
  if trend_range(sym,chg,s) and float(s['rsi_min'])<=r<=float(s['rsi_max']) and p>ma5>ma10 and trend_4h_ok(sym,s):
   if not in_cd("sm_"+sym,3600) and sym not in load(DB):hits.append((sym,p,chg,r,_H1.get(sym) or 0.0))
   continue
  nmr=near_miss(sym,chg,r,p,ma5,ma10,s)
  if nmr:_NM.append({"sym":sym,"score":nmr[0],"why":nmr[1],"rsi":round(r,1),"h1":round(nmr[2],2),"chg":round(chg,2),"price":p})
 if hits:
  lines=["🎯 *ALERT:* SETUP MATCHED NOW","_TREND entry rules just passed. The bot is checking it for auto-buy now._",""];rows=[]
  for sym,p,chg,r,h in hits[:3]:
   coin=sym.split("-")[0];mk_cd("sm_"+sym)
   lines.append(f"*{coin}* — {fp(p)} USDT | 24h {chg:+.2f}% | 1H {h:+.2f}% | RSI {r:.1f}")
   rows.append([_B(f"🛒 Buy {coin}",f"m:b:{coin}"),_B(f"🔎 Analyse {coin}",f"m:a:{coin}")])
  _create("\n".join(lines),"Markdown",{"inline_keyboard":rows},silent=False)
  if not in_cd("fast_scan",300):
   mk_cd("fast_scan")
   try:scan(True)
   except Exception as e:print("FASTWATCH SCAN ERR",str(e)[:120])
  return
 send_near_miss(s,None)
def send_new_listing_discovery_alerts(state):
 # Send one immediate Telegram alert per newly detected pair. Failed sends remain pending.
 tracked=state.get("tracked",{}) if isinstance(state,dict) else {}
 pending=[(sym,meta) for sym,meta in tracked.items()
          if isinstance(meta,dict) and not meta.get("discovery_alerted_at")]
 if not pending:return 0
 sent=0
 for start in range(0,len(pending),8):
  batch=pending[start:start+8]
  lines=["🆕 *NEW BITGET SPOT LISTING DETECTED*",
         "_A new online USDT spot pair has appeared on Bitget._",
         "_Added to Coin List WATCH for observation. This alert is not a buy signal._",""]
  rows=[]
  for sym,meta in batch:
   coin=sym.split("-")[0]
   detected=meta.get("first_detected")
   when=datetime.datetime.fromtimestamp(float(detected)).strftime("%Y-%m-%d %H:%M") if detected else "time unavailable"
   lines.append(f"*{coin}/USDT* — detected {when}; monitoring in WATCH")
   rows.append([_B(f"🔎 Analyse {coin}",f"m:a:{coin}")])
  mid=_create("\\n".join(lines),"Markdown",{"inline_keyboard":rows},silent=False)
  if not mid:break
  now=time.time()
  for sym,meta in batch:
   meta["discovery_alerted_at"]=now
   meta["discovery_alert_message_id"]=mid
   sent+=1
  state["tracked"]=tracked
  save(NEWLIST,state)
 return sent

def discover_new_spot_pairs(markets=None, tickers=None, interval_seconds=900, max_probe=20):
 """Detect newly appearing Bitget USDT spot pairs; discovery is WATCH-only, never an entry signal.
 The first successful call seeds a baseline to avoid falsely classifying every existing pair as new.
 """
 now=time.time();state=load(NEWLIST,{})
 if not isinstance(state,dict):state={}
 if now-float(state.get("last_check",0) or 0)<float(interval_seconds):return 0
 try:
  resp=requests.get("https://api.bitget.com/api/v2/spot/public/symbols",timeout=12)
  resp.raise_for_status();payload=resp.json()
  if not isinstance(payload,dict) or str(payload.get("code"))!="00000" or not isinstance(payload.get("data"),list):
   raise ValueError("Bitget symbol endpoint returned an unexpected response")
  rows=payload["data"]
  current=set()
  for row in rows:
   if not isinstance(row,dict):continue
   if str(row.get("status","")).lower()!="online":continue
   if str(row.get("quoteCoin","")).upper()!="USDT":continue
   base=str(row.get("baseCoin","")).upper();raw=str(row.get("symbol","")).upper()
   if not base or not raw or skip(base):continue
   current.add(base+"-USDT")
  if not state.get("initialized"):
   state.update({"version":1,"initialized":True,"known_symbols":sorted(current),"tracked":{},"last_check":now,"baseline_at":now})
   save(NEWLIST,state)
   print(f"NEW LISTING DISCOVERY: seeded baseline with {len(current)} online USDT spot pairs")
   return 0
  known=set(state.get("known_symbols") or []);tracked=state.get("tracked") if isinstance(state.get("tracked"),dict) else {}
  added=sorted(current-known)
  for sym in added:
   tracked[sym]={"first_detected":now,"status":"NEW_LISTING_WATCH","source":"bitget_spot_symbols_api"}
  # Keep a rolling 24-hour observation window; do not delete historical Coin List records.
  tracked={k:v for k,v in tracked.items() if isinstance(v,dict) and now-float(v.get("first_detected",now) or now)<=86400}
  state.update({"version":1,"known_symbols":sorted(known|current),"tracked":tracked,"last_check":now,"last_added_count":len(added)})
  save(NEWLIST,state)
  if added:print("NEW BITGET SPOT PAIRS:",", ".join(added[:30]))
  # Probe recent additions and still-young pairs. Probe failures remain unknown, never positive signals.
  candidates=sorted(tracked.items(),key=lambda kv:(float(kv[1].get("last_probe",0) or 0),float(kv[1].get("first_detected",now))))[:max_probe]
  for sym,meta in candidates:
   try:
    # Ensure every detected pair with a valid ticker reaches Coin List even if candles are not ready.
    tick=(tickers or {}).get(S(sym),{}) if isinstance(tickers,dict) else {}
    fallback_price=float((tick or {}).get("last") or 0)
    if fallback_price>0:_NEWC[sym]=(fallback_price,0.0,0)
    candles=ex.fetch_ohlcv(S(sym),"1h",limit=100)
    if not isinstance(candles,list) or not candles:continue
    valid=[x for x in candles if isinstance(x,(list,tuple)) and len(x)>5 and x[4] is not None]
    if not valid:continue
    price=float(valid[-1][4]);n=len(valid)
    if price<=0:continue
    # Use closed hourly candles for an early momentum estimate; avoid a fabricated 24h change.
    closed=valid[:-1] if len(valid)>1 else valid
    h=None
    if len(closed)>=2:
     prev=float(closed[-2][4]);last=float(closed[-1][4])
     if prev>0:h=(last/prev-1.0)*100.0
    if n<50:_NEWC[sym]=(price,float(h) if h is not None else 0.0,n)
    else:_NEWC.pop(sym,None)
    meta["last_probe"]=now;meta["hourly_candles"]=n;meta["last_price"]=price;meta["h1_pct"]=h
    if n>=50:meta["status"]="HISTORY_MATURED_PENDING_NORMAL_SCANNER"
   except Exception as e:
    meta["last_probe_error"]=str(e)[:120]
  state["tracked"]=tracked;save(NEWLIST,state)
  try:
   alerted=send_new_listing_discovery_alerts(state)
   if alerted:print(f"NEW LISTING TELEGRAM ALERTS SENT: {alerted}")
  except Exception as e:print("NEW LISTING ALERT ERROR:",str(e)[:140])
  return len(added)
 except Exception as e:
  print("NEW LISTING DISCOVERY ERROR:",str(e)[:180])
  # Leave last_check unchanged so a transient API failure can be retried next scan.
  return 0

def scan(force=False):
 st=load(SC);now=time.time();_t0=time.time()
 iv=max(1.0,float(cfg().get('scan_interval_minutes',15) or 15))
 if not force and now-st.get('last',0)<max(60,iv*60-30):return
 try:auto_sync()
 except Exception as _e:print('AUTO-SYNC ERR',str(_e)[:100])
 db=load(DB);q=load(QF);s=cfg();ai_market=ai_market_context(s);guardian_ok,guardian_reason=guardian_audit();auto_enabled=guardian_ok;btc_is_blocked=btc_blocked(s) if auto_enabled else False
 mf=market_filter(s) if auto_enabled and not btc_is_blocked else ""
 if mf and not cd("mktblock",21600):tg(f"⏸ *MARKET FILTER:* {mf}.")
 rbh=float(s.get('rebuy_cooldown_hours',6.0))
 buying_allowed=auto_enabled and not btc_is_blocked and not mf
 block_reason=(f"Guardian integrity check: {guardian_reason}" if not guardian_ok else "BTC safety filter blocked buying" if btc_is_blocked else f"market filter: {mf}" if mf else "")
 try:tk=ex.fetch_tickers();mk=ex.load_markets()
 except Exception as e:print(f"SCAN ERROR: {e}");tg(f"⚠️ *SCAN FAILED:* {e}");return
 try:discover_new_spot_pairs(mk,tk,interval_seconds=900,max_probe=20)
 except Exception as e:print("NEW LISTING DISCOVERY FAILED:",str(e)[:140])
 v=[{'sym':k.replace("/","-"),'vol':t['quoteVolume'] if t.get('quoteVolume') is not None else 0,'vol_available':t.get('quoteVolume') is not None,'chg':t['percentage'] if t.get('percentage') is not None else 0,'chg_available':t.get('percentage') is not None,'last':t.get('last'),'high':t.get('high'),'bid':t.get('bid'),'ask':t.get('ask')} for k,t in tk.items() if k.endswith('/USDT') and not skip(k.split('/')[0]) and not (k in mk and not mk[k].get('spot',False))]
 v.sort(key=lambda x:x['vol'],reverse=True);ct=int(time.time());qu=False
 for sym,d in list(q.items()):
  if ct-d.get('timestamp',0)>86400:q.pop(sym,None);qu=True
 if not buying_allowed and q:q.clear();qu=True
 sh=scan_history_state();records=[];matched=0;queued=0
 btc_core_safe=btc_market_safety_check(s)
 _PF.clear();_NM.clear();_RADAR.clear()
 if s.get('scan_parallel',True):
  try:
   cand=[]
   for rank,t in enumerate(v[:500],start=1):
    if rank<=15:continue
    i=rank-16;th=(1000000 if i<=84 else 3000000 if i<=183 else 4000000 if i<=383 else 5000000);chg=float(t['chg'] or 0)
    if float(t['vol'] or 0)<th:continue
    if s.get('scan_prefilter',True) and not pre_ok(chg,s):continue
    _sp=tk_spread(t)
    if _sp is not None and _sp>2*float(s.get('max_spread_pct',0.5)):continue
    cand.append(t['sym'])
   from concurrent.futures import ThreadPoolExecutor
   with ThreadPoolExecutor(max_workers=rl_workers(s.get('scan_workers',8))) as pool:
    for sy,res in zip(cand,pool.map(_calc,cand)):_PF[sy]=res
  except Exception as e:print(f"PREFETCH ERR: {e}");_PF.clear()
 for rank,t in enumerate(v[:500],start=1):
  sym=t['sym'];vol=float(t['vol'] or 0);chg=float(t['chg'] or 0)
  recd={"time":ct,"rank":rank,"sym":sym,"vol":vol if t.get("vol_available",True) else None,"chg":chg if t.get("chg_available",True) else None,"price":t.get("last"),"high_24h":t.get("high"),"spread_pct":tk_spread(t),"rsi":None,"rr":None,"ma5":None,"ma10":None,"btc_market_safe":btc_core_safe,"market_trend_4h":None,"breakout_confirmed":None,"volume_expansion_ratio":None,"decision":"SKIP","reason":"Top 15 excluded" if rank<=15 else "Not evaluated"}
  if rank>15:
   i=rank-16;threshold=(1000000 if i<=84 else 3000000 if i<=183 else 4000000 if i<=383 else 5000000);recd["min_volume"]=threshold
   if vol<threshold:recd["reason"]=f"Vol below ${threshold/1e6:.0f}M"
   elif s.get('scan_prefilter',True) and not pre_ok(chg,s):recd["reason"]="24H change outside every setup range (skipped before candle fetch)"
   elif s.get('scan_parallel',True) and (tk_spread(t) or 0)>2*float(s.get('max_spread_pct',0.5)):recd["reason"]=f"Spread {tk_spread(t):.2f}% is more than twice your {float(s.get('max_spread_pct',0.5)):g}% limit (skipped before candle fetch)"
   else:
    p,pr,r,ma5,ma10=_PF.get(sym) or data(sym);recd.update(price=p,rsi=r,ma5=ma5,ma10=ma10,prev_close=pr)
    if _H1.get(sym) is not None:recd['h1']=round(_H1[sym],2)
    if not p or r is None:recd["reason"]="No candle data"
    else:
     setup=sl=None;breakout_volume=None
     if trend_range(sym,chg,s) and s['rsi_min']<=r<=s['rsi_max'] and p>ma5>ma10:setup="TREND";sl=round(ma5*(1-s['stop_loss_pct']/100),6)
     elif trend_range(sym,chg,s) and float(s.get('pullback_rsi_min',40))<=r<=s['rsi_max'] and ma5>ma10 and ma10<=p<=ma5:setup="PULLBACK";sl=round(p*(1-s['stop_loss_pct']/100),6)
     elif s.get("oversold_enabled",False) and setup_range(sym,chg,s,"BOUNCE") and r<30 and p>pr:setup="BOUNCE";sl=round(p*(1-s['stop_loss_pct']/100),6)
     elif setup_range(sym,chg,s,"BREAKOUT") and 50.0<=r<=70.0 and p>ma5>ma10:
      confirmed,breakout_volume,_=breakout_confirmation(sym)
      if confirmed:setup="BREAKOUT";sl=round(p*(1-s['stop_loss_pct']/100),6)
     if setup in ("TREND","BREAKOUT","PULLBACK"):
      recd["trend_4h_ok"]=bool(trend_4h_ok(sym,s))
      if not recd["trend_4h_ok"]:
       recd["reason"]=f"{setup} matched but 4h confirmation failed (downtrend or unavailable data)";setup=None
     else:recd["trend_4h_ok"]=False
     if setup and s.get("rising_guard",True):
      _ok,_why=rising_vs_last_scan(sym,p,r,ct);recd["rising_guard_ok"]=bool(_ok)
      if not _ok:recd["reason"]=f"{setup} matched; {_why}";setup=None
     elif setup:recd["rising_guard_ok"]=True
     if setup:
      recd["strategy"]=setup
      if setup=="BREAKOUT":recd["reason"]=f"BREAKOUT vol {breakout_volume:.2f}x"
      matched+=1;rr=None;recd["rr"]=None;score=setup_score(setup,vol,r,ma5,ma10,breakout_volume)
      if not buying_allowed:recd["decision"]="SETUP — NOT QUEUED";recd["reason"]=f"{setup} matched, {block_reason}"
      elif rbh>0 and in_cd(f"stopout_{sym}",rbh*3600):recd["decision"]="SETUP — NOT QUEUED";recd["reason"]=f"{setup} matched, re-buy cooldown"
      elif coin_stage_prev(sym) not in ("MANUAL","AUTO"):
       _ps=coin_stage_prev(sym) or "none";recd["decision"]="SETUP — NOT QUEUED";recd["reason"]=f"{setup} matched; stage {_ps}, must pass Watch > Manual > Auto first"
      else:
       recd["decision"]="SETUP — QUEUED";recd["reason"]=setup+" matched; queued"
       if sym not in q or q[sym].get('score',0)<score:
        if sym not in q:tg(f"📥 *QUEUED:* {sym}\nScore:{score:.1f}")
        q[sym]={"timestamp":ct,"created_at":ct,"scan_id":str(ct),"expires_at":ct+float(s.get("signal_expiry_minutes",30))*60,"type":setup,"sl":sl,"score":score,"price":p,"chg":chg,"source":"deterministic_scanner"};qu=True
       queued+=1
     elif not any(k in str(recd.get("reason","")) for k in ("4h trend is down","Falling-knife")):
      reasons=[]
      if not trend_range(sym,chg,s):reasons.append("Trend range")
      if not (s['rsi_min']<=r<=s['rsi_max']):reasons.append(f"RSI {s['rsi_min']}-{s['rsi_max']}")
      if not (p>ma5>ma10):reasons.append("MA trend")
      if not (setup_range(sym,chg,s,"BOUNCE") and r<30 and p>pr):reasons.append("Oversold recovery")
      if not (setup_range(sym,chg,s,"BREAKOUT") and 50.0<=r<=70.0 and p>ma5>ma10):reasons.append("Breakout trend")
      else:reasons.append("No breakout")
      recd["reason"]="; ".join(reasons) or "No match"
      try:
       if mom_on(s) and pre_ok(chg,s) and p>ma5>ma10:
        _d=max(float(s['rsi_min'])-r,r-float(s['rsi_max']),0.0)
        if _d<=float(s.get('radar_rsi_margin',10.0)):_RADAR.append((_d,sym))
       nmr=near_miss(sym,chg,r,p,ma5,ma10,s)
       if nmr:recd["near_miss"]=nmr[1];_NM.append({"sym":sym,"score":nmr[0],"why":nmr[1],"rsi":round(r,1),"h1":round(nmr[2],2),"chg":round(chg,2),"price":p})
      except Exception as e:print("NEAR-MISS ERR",str(e)[:100])
  if rank>15 and recd.get("price") is not None and recd.get("rsi") is not None:
   try:
    _vol_ok=vol>=float(recd.get("min_volume") or 1e100)
    _spread=recd.get("spread_pct");_spread_ok=_spread is not None and float(_spread)<=float(s.get("max_spread_pct",0.5))
    _p=float(recd.get("price"));_r=float(recd.get("rsi"));_m5=recd.get("ma5");_m10=recd.get("ma10")
    _structure=_m5 is not None and _m10 is not None and _p>float(_m5)>float(_m10)
    _rsi_ok=float(s.get("rsi_min",42))<=_r<=float(s.get("rsi_max",48))
    _chg_ok=bool(recd.get("chg") is not None and 0.0<float(recd.get("chg"))<=float(s.get("max_24h_pct",100.0)))
    _pre=sum(bool(x) for x in (_vol_ok,_spread_ok,_structure,_rsi_ok,_chg_ok,btc_core_safe))
    if _pre>=4:
     recd["market_trend_4h"]=market_trend_4h(sym)
     recd["trend_4h_ok"]=recd["market_trend_4h"] is True
     if sum(bool(x) for x in (_vol_ok,_spread_ok,_structure,_rsi_ok,_chg_ok,btc_core_safe,recd["trend_4h_ok"]))>=5:
      _brk,_volx,_res=breakout_metrics(sym)
      recd["breakout_confirmed"]=_brk;recd["volume_expansion_ratio"]=_volx
      # One stop for everything: the ATR stop used for position size and exit is also the risk here.
      # Reward = measured move: breakout level + height of the last 20-hour range (capped at 20% of price).
      _rg=_BRK_RANGE.get(sym)
      if _res is not None and _rg and _p>0:
       _rk=sl_pct(sym)
       _tg=float(_res)+min(float(_rg),_p*0.20)
       recd["rr"]=max(0.0,_tg-_p)/(_p*_rk/100.0) if _rk>0 else None
       recd["rr_target"]=_tg;recd["rr_stop_pct"]=_rk
   except Exception as e:print("COIN CORE EVIDENCE ERR",sym,str(e)[:100])
  records.append(recd)
 try:
  _rs=load(ST,{});_rs["radar"]=[y for _,y in sorted(_RADAR)[:int(s.get('radar_max',30))]];save(ST,_rs)
 except Exception as e:print("RADAR SAVE ERR",str(e)[:100])
 try:send_near_miss(s,ai_market)
 except Exception as e:print("NEAR-MISS SEND ERR",str(e)[:120])
 try:coin_db_update(records,load(DB).keys(),dict(_NEWC),queue=q,buying_allowed=buying_allowed,block_reason=block_reason,settings=s)
 except Exception as e:print("COIN DB ERR",str(e)[:120])
 try:
  _coin_rows=coin_db_ai_rows(25)
  if _coin_rows:
   _coin_review=ai_stage("coin_advisory",{"coins":_coin_rows},ttl=240,max_tokens=700,soft=True)
   _reviews=_coin_review.get("reviews",[]) if isinstance(_coin_review,dict) else []
   _review_map={str(x.get("symbol","")).upper().replace("/","-"):x for x in _reviews if isinstance(x,dict)}
   for _coin in _coin_rows:
    _item=_review_map.get(str(_coin["symbol"]).upper())
    if _item:
     _comment=str(_item.get("comment") or _item.get("reason") or "")[:300]
     if _comment:coin_db_set_ai_advisory(_coin["symbol"],str(_item.get("risk_level","INFO"))+": "+_comment)
   if _review_map:journal_event("ai_coin_advisory",reviewed=len(_review_map),source="coins.db")
 except Exception as e:print("AI COIN ADVISORY ERR",str(e)[:140])
 try:send_new_alerts(s)
 except Exception as e:print("NEW-TOKEN ALERT ERR",str(e)[:120])
 full=force or now-st.get('last_full',0)>=3300
 sh["items"]=([x for x in sh["items"] if str(x.get('decision','')).startswith('SETUP')]+[x for x in records if str(x.get('decision','')).startswith('SETUP')])[-300:];save(SH,sh)  # full history lives in coins.db
 if qu or not buying_allowed:save(QF,q)
 st['last']=time.time()
 if full:st['last_full']=st['last']
 save(SC,st)
 if matched:
  msg=f"🔍 *SCAN COMPLETE*\nChecked {len(records)} markets in {time.time()-_t0:.0f}s.\nMatched: {matched} | Queued: {queued}." + ("\n" + block_reason if not buying_allowed else "")+("\n"+gate_report_text() if gate_report_text() else "")
  if ai_market:
   regime=str(ai_market.get('regime',''))[:60];risk=str(ai_market.get('risk_level',''))[:30]
   if regime or risk:msg+="\n🧠 AI market view: "+" | ".join(x for x in (regime,risk) if x)
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
 else:tg(f"🔍 *SCAN COMPLETE*\nChecked {len(records)} markets in {time.time()-_t0:.0f}s.\nNo coin matched any setup."+("\n"+gate_report_text() if gate_report_text() else ""))
def fp(p):
 if p is None:return "N/A"
 return f"{p:.2f}" if p>=100 else f"{p:.4f}" if p>=1 else f"{p:.6f}" if p>=0.001 else f"{p:.8f}"
def cmd_set(txt):
 p=txt.split()
 if len(p)!=3:tg("❌ Usage: `/set <key> <value>`");return
 k,val=p[1],p[2];s=cfg()
 if k not in s:tg(f"❌ Unknown key. Valid: `{'`, `'.join(s.keys())}`");return
 try:new=val.lower() in ('true','1','yes','on') if isinstance(s[k],bool) else float(val) if isinstance(s[k],float) else int(val)
 except:tg(f"❌ Invalid value for {k}");return
 limits={"weak_market_floor_pct":(0.0,50.0),"weak_market_size_factor":(0.1,1.0),"risk_per_trade_pct":(0.1,2.0),"max_daily_loss_pct":(0.5,10.0),"max_portfolio_exposure_pct":(5.0,100.0),"max_position_pct":(1.0,50.0),"max_daily_trades":(1,50),"max_spread_pct":(0.05,3.0),"trail_pct":(0.0,30.0),"scan_interval_minutes":(5.0,240.0),"auto_clear_hours":(0.0,168.0),"live_mode_seconds":(0,330),"stop_loss_pct":(0.5,20.0),"rsi_min":(1.0,99.0),"rsi_max":(1.0,99.0),"rsi_exit":(0.0,99.0),"auto_buy_amount_usdt":(1.0,100000.0),"max_tokens_before_consolidation":(1,30),"crash_guard_pct":(1.0,50.0),"btc_rsi_min":(0.0,99.0),"rebuy_cooldown_hours":(0.0,168.0),"atr_stop_mult":(0.5,6.0),"atr_stop_min_pct":(1.0,10.0),"atr_stop_max_pct":(2.0,25.0),"btc_trend_hours":(10,200),"min_breadth_pct":(0.0,100.0),"offline_alert_hours":(0.0,72.0),"daily_summary_hour_utc":(0,23),"max_coin_exposure_pct":(1.0,100.0),"liquidity_depth_usdt":(0.0,1000000.0),"max_book_imbalance_pct":(50.0,99.0),"regime_risk_factor":(0.1,1.0),"execution_slippage_pct":(0.05,5.0),"portfolio_stress_pct":(1.0,50.0),"signal_expiry_minutes":(1,240),"mom_1h_min_pct":(0.0,20.0),"mom_1h_max_pct":(0.5,30.0),"max_24h_pct":(30.0,500.0),"near_miss_rsi_margin":(0.5,10.0),"near_miss_1h_margin":(0.1,3.0),"near_miss_cooldown_minutes":(5,1440),"near_miss_max_per_scan":(1,10),"scan_workers":(1,16),"radar_max":(5,60),"radar_rsi_margin":(3.0,25.0),"manual_candidate_min_score":(70.0,70.0)}
 if k in limits and not (limits[k][0] <= new <= limits[k][1]):tg(f"❌ `{k}` must be between {limits[k][0]} and {limits[k][1]}.");return
 if k=="rsi_min" and new>=s.get("rsi_max",99):tg("❌ rsi_min must be lower than rsi_max.");return
 if k=="rsi_max" and new<=s.get("rsi_min",1):tg("❌ rsi_max must be higher than rsi_min.");return
 s[k]=new;save(CFG,s);tg(f"✅ `{k}` = `{new}`")
def setc(k,v,m):s=cfg();s[k]=v;save(CFG,s);tg(m)
MSG={"ai_info":"🧠 *AI ANALYST*\nTo ask the AI a question, type:\n`/ai <your question>`","/help":"🤖 *CMDS*\n`/menu` `/ai [q]` `/analyse` (top 3) `/analyse COIN` `/audit` `/cancel`\n`/bot` `/holdings` `/stats`\n`/clear` `/dust` `/alert COIN PRICE` `/alerts` `/cancel\\_alerts`\n`/scan_history [coin/filter]`\n`/set key value` `/panic`"}
_UI={"mid":None,"edit_mode":False,"panel_used":False}
_B=lambda t,c:{"text":t,"callback_data":c}
_MENU_BTN=[_B("⬅️ Menu","m:menu")]
RUN={"scan":"/scan","sync":"/sync","holdings":"/holdings","analyse":"/analyse","alerts":"/alerts","cancelalerts":"/cancel_alerts","dust":"/dust","bot":"/bot","audit":"/audit","stats":"/stats","clear":"/clear","cancel":"/cancel","pricealert":"/alerts","airesume":"/ai_resume"}
AI_Q={"1":"Give me a market overview.","2":"Review my current holdings and say which to hold or sell.","3":"Which single coin looks best to buy right now and why?","4":"What is the biggest risk in my portfolio right now?","5":"Which of my open positions is closest to its stop-loss, and is that a concern?","6":"Is the market safe for buying right now? Consider the BTC filter.","7":"Summarise my recent trading results and what to improve.","8":"What should I watch out for in the next 24 hours?"}

# ============================== FILE: bitget_bot/components/09_telegram_interface.py ==============================
# Telegram dashboard, callbacks, command routing, polling
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
def ui_more(kind,pg=0):
 pre={"buy":"m:b:","analyse":"m:a:","alert":"m:a2:","fav":"m:fa:"}.get(kind,"m:b:");back={"buy":"m:buy","analyse":"m:an","alert":"m:al","fav":"m:fv"}.get(kind,"m:menu")
 try:
  tk=ex.fetch_tickers();mk=ex.load_markets()
  v=[(k.split('/')[0],float(t.get('quoteVolume') or 0)) for k,t in tk.items() if k.endswith('/USDT') and not skip(k.split('/')[0]) and not (k in mk and not mk[k].get('spot',False))]
  v=[c for c,q in sorted(v,key=lambda x:x[1],reverse=True) if c.upper() not in STABLE and c.upper() not in BL]
 except Exception as e:
  ui_send("⚠️ Could not load the coin list: "+str(e)[:100],[[_B("⬅️ Back",back)]]);return
 per=18;n=max(1,(len(v)+per-1)//per);pg=max(0,min(pg,n-1))
 nav=[]
 if pg>0:nav.append(_B("◀️ Prev",f"m:more:{kind}:{pg-1}"))
 if pg<n-1:nav.append(_B("Next ▶️",f"m:more:{kind}:{pg+1}"))
 ui_send(f"➕ *MORE COINS* (page {pg+1}/{n})\nBiggest 24h volume first. Tap a coin.",_grid([_B(c,pre+c) for c in v[pg*per:(pg+1)*per]],3)+([nav] if nav else [])+[[_B("⬅️ Back",back)]])
PRESETS={"safe":{"risk_per_trade_pct":0.5,"max_position_pct":8.0,"max_portfolio_exposure_pct":30.0,"max_daily_trades":5},
 "balanced":{"risk_per_trade_pct":0.5,"max_position_pct":15.0,"max_portfolio_exposure_pct":50.0,"max_daily_trades":13},
 "active":{"risk_per_trade_pct":0.5,"max_position_pct":20.0,"max_portfolio_exposure_pct":60.0,"max_daily_trades":25}}
PRESET_NAMES={"safe":"🛡 Safe","balanced":"⚖️ Balanced","active":"🚀 Active"}
SET_SWITCHES=(("rising_guard","Falling-knife guard"),("weak_market_mode","Weak-market half size"),("h1_all_setups","1H rule for all setups"),("half_or_all_sizing","50% / all-in sizing"))
def ui_settings_page(pg=0,note=""):
 cur=cfg();mode=next((n for n,v in PRESETS.items() if all(float(cur.get(k,-1))==float(x) for k,x in v.items())),None)
 rows=[[_B(("✅ " if n==mode else "")+PRESET_NAMES[n],f"m:pre:{n}") for n in PRESETS]]
 rows+=[[_B(f"{'🟢' if cur.get(k) else '⚪'} {lb}: {'ON' if cur.get(k) else 'OFF'}",f"m:tg:{k}")] for k,lb in SET_SWITCHES]
 txt="⚙️ *SETTINGS*\n"+(note+"\n" if note else "")+f"Mode: {PRESET_NAMES.get(mode,'Custom')}\nSafe = small trades, few per day. Active = bigger trades, more per day.\nEverything else adjusts itself."
 ui_send(txt,rows+[_MENU_BTN])
def ui_setting(k):
 cur=cfg()
 if k not in cur:ui_send("❌ Unknown setting.",[_MENU_BTN]);return
 v=cur[k]
 if isinstance(v,bool):row=[_B("🔄 Switch ON/OFF",f"m:kv:{k}:t")]
 else:row=[_B("➖➖ -25%",f"m:kv:{k}:--"),_B("➖ -10%",f"m:kv:{k}:-"),_B("➕ +10%",f"m:kv:{k}:+"),_B("➕➕ +25%",f"m:kv:{k}:++")]
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
   ui_send("🛒 *BUY* — choose a coin:",_grid([_B(c,f"m:b:{c}") for c in cs],3)+[[_B("➕ More coins","m:more:buy:0")],_MENU_BTN])
  elif a=="at" and len(p)>2:analyse_top3(p[2])
  elif a=="nl":analyse_new_listings()
  elif a=="ar":analyse_archive(p[2] if len(p)>2 else "D")
  elif a=="su":analyse_setups()
  elif a=="am":ui_send("🗂 *ANALYSE — MORE*",[[_B("⭐ FAVOURITES","m:fv"),_B("🆕 NEW LISTINGS","m:nl")],[_B("🗄 ARCHIVE","m:ar:D"),_B("🔎 ANALYSE A COIN","m:an")],[_B("⬅️ Analyse","/analyse")]])
  elif a=="fv":fav_show()
  elif a=="fc" and len(p)>2:fav_coin(p[2],p[3] if len(p)>3 else "fv")
  elif a=="pk" and len(p)>2:coin_picker(p[2])
  elif a=="fa" and len(p)>2:fav_change(p[2],True)
  elif a=="fd" and len(p)>2:fav_change(p[2],False)
  elif a=="mc" and len(p)>2:
   manual_candidate_detail(p[2])
  elif a=="b":
   c=p[2];free=B('free','USDT');amts=[x for x in (2,5,10,15) if x<=free]
   ui_send(f"🛒 *BUY {c}*\nFree USDT: ${free:.2f}\nChoose how much:",[[_B(f"${x}",f"m:bc:{c}:{x}") for x in amts]+[_B("ALL",f"m:bc:{c}:ALL")],[_B("✏️ Custom amount",f"m:cu:b:{c}")],[_B("⬅️ Back","m:buy")]])
  elif a=="bc":
   c,x=p[2],p[3];px=live(f"{c}-USDT")
   ui_send(f"⚠️ *CONFIRM BUY*\n{c} for {'ALL free USDT' if x=='ALL' else '$'+x} at about {fp(px)} USDT.",[[_B("✅ Confirm",f"m:bx:{c}:{x}"),_B("❌ Cancel","m:menu")]])
  elif a=="bp" and len(p)>3:
   c,pc=p[2],int(p[3]);free=B('free','USDT');px=live(f"{c}-USDT");x="ALL" if pc>=100 else f"{free*pc/100:.2f}"
   if x!="ALL" and float(x)<1.0:ui_send(f"⚠️ {pc}% of your free USDT (${free:.2f}) is under the $1 minimum order.",[[_B("⬅️ Back",f"m:fc:{c}")]])
   else:ui_send(f"⚠️ *CONFIRM BUY*\n{c} for {'ALL free USDT' if x=='ALL' else '$'+x} ({pc}% of free USDT) at about {fp(px)} USDT.",[[_B("✅ Confirm",f"m:bx:{c}:{x}"),_B("❌ Cancel","m:menu")]])
  elif a=="bx":handle(f"/buy {p[2]} {p[3]}",0)
  elif a=="sell":
   cs=ui_coins("sell")
   if not cs:ui_send("💸 *SELL*\nNo bot-tracked positions. The bot adds coins you hold automatically on its next scan.",[_MENU_BTN])
   else:ui_send("💸 *SELL* — choose a position:",_grid([_B(c,f"m:s:{c}") for c in cs],3)+[_MENU_BTN])
  elif a=="s":
   c=p[2];fb=B('free',c);px=live(f"{c}-USDT") or 0
   ui_send(f"💸 *SELL {c}*\nYou hold {fb:.8g} (about ${fb*px:.2f}).\nHow much?",[[_B(f"{x}%",f"m:sc:{c}:{x}") for x in (25,50,75,100)],[_B("✏️ Custom amount",f"m:cu:s:{c}")],[_B("⬅️ Back","m:sell")]])
  elif a=="sc":
   c,x=p[2],int(p[3]);fb=B('free',c);px=live(f"{c}-USDT") or 0
   ui_send(f"⚠️ *CONFIRM SELL*\n{x}% of {c} (about ${fb*px*x/100:.2f}) at market.",[[_B("✅ Confirm",f"m:sx:{c}:{x}"),_B("❌ Cancel","m:menu")]])
  elif a=="coins":cmd_coins()
  elif a=="cu" and len(p)>3:
   st=load(ST,{});st["amt_pending"]={"side":p[2],"coin":p[3],"t":time.time()};save(ST,st)
   ui_send(f"✏️ *{'BUY' if p[2]=='b' else 'SELL'} {p[3]}*\nSend the amount in USDT (example: 7.5).",[[_B("❌ Cancel","m:menu")]])
  elif a=="sxu" and len(p)>3:handle(f"/sell {p[2]} {p[3]}",0)
  elif a=="sx":
   c,x=p[2],int(p[3])
   if x>=100:handle(f"/sell {c} ALL",0)
   else:
    fb=B('free',c);px=live(f"{c}-USDT") or 0;handle(f"/sell {c} {fb*px*x/100:.2f}",0)
  elif a=="an":
   cs=ui_coins("analyse")
   ui_send("🔎 *ANALYSE A COIN* — choose:",[[_B("🔍 Search any coin","m:se")]]+_grid([_B(c,f"m:a:{c}") for c in cs],3)+[[_B("➕ More coins","m:more:analyse:0")],_MENU_BTN])
  elif a=="se":
   st=load(ST,{});st["search_pending"]={"t":time.time()};save(ST,st)
   ui_send("🔍 *SEARCH A COIN*\nType a coin name or part of it (example: SOL).",[[_B("❌ Cancel","m:an")]])
  elif a=="a":handle(f"/analyse {p[2]}",0)
  elif a=="al":
   cs=ui_coins("alert");al=[x.get("coin") for x in _alerts()]
   rows=[[_B("📋 My alerts","m:r:alerts"),_B("🗑 Cancel all alerts","m:r:cancelalerts")]]
   rows+=_grid([_B(f"🗑 {c}",f"m:ad:{c}") for c in dict.fromkeys(al)],3)
   ui_send("🔔 *PRICE ALERT* — choose a coin to set an alert on:",rows+_grid([_B(c,f"m:a2:{c}") for c in cs],3)+[[_B("➕ More coins","m:more:alert:0")],_MENU_BTN])
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
  elif a=="pre" and len(p)>2 and p[2] in PRESETS:
   s=cfg()
   for k,v in PRESETS[p[2]].items():s[k]=type(s.get(k,v))(v) if not isinstance(s.get(k),bool) else v
   save(CFG,s);ui_settings_page(0,f"✅ {PRESET_NAMES[p[2]]} mode set.")
  elif a=="tg" and len(p)>2 and p[2] in dict(SET_SWITCHES):
   s=cfg();s[p[2]]=not bool(s.get(p[2]));save(CFG,s);ui_settings_page(0,f"✅ {dict(SET_SWITCHES)[p[2]]} is now {'ON' if s[p[2]] else 'OFF'}.")
  elif a=="kv":
   k,d=p[2],p[3];v=cfg().get(k)
   if isinstance(v,bool):new=not v
   elif isinstance(v,int):
    big=d in ("++","--");new=v+(max(1,round(abs(v)*0.25)) if big else 1)*(1 if d.startswith("+") else -1)
   else:
    step=max(abs(v)*(0.25 if d in ("++","--") else 0.1),0.1);new=round(v+(step if d.startswith("+") else -step),4)
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
   if len(p)>3 and p[2]=="pl":
    c=cfg();c["page_lines"]=max(6,min(24,int(c.get("page_lines",12) or 12)+(1 if p[3]=="+" else -1)));save(CFG,c);note="\n✅ Page size: %d lines."%c["page_lines"]
   note+=_UI.pop("lay_note","")
   rows=[[_B(("⌨️ " if k in b else "📋 ")+lbl(k),f"m:lay:t:{k}") for k,l,_ in LAYOUT_ITEMS[i:i+2]] for i in range(0,len(LAYOUT_ITEMS),2)]
   ui_send(f"🧩 *LAYOUT*\nTap a button to move it.\n⌨️ = bottom key (below the text box)\n📋 = Menu list\nBottom keys: {len(b)+1}/{LAYOUT_MAX+1} (Menu always stays){note}",rows+[[_B("➖","m:lay:pl:-"),_B("📏 Page: %d lines"%int(cfg().get("page_lines",12) or 12),"m:lay"),_B("➕","m:lay:pl:+")],[_B("✏️ Rename buttons","m:lay:rn"),_B("↩️ Reset layout","m:lay:reset")],[_B("⬅️ Tools","m:tools")]])
  elif a=="tools":
   ui_send("🧰 *TOOLS*\nChoose a group.",[[_B("🧹 Orders & Dust","m:tl:trade"),_B("🔔 Alerts","m:tl:alerts")],[_B("📚 Coin List","m:coins"),_B("🛠️ System","m:tl:sys")],[_B("⬅️ Main Menu","m:menu")]])
  elif a=="tl" and len(p)>2:
   _T=[_B("⬅️ Tools","m:tools"),_B("🏠 Main Menu","m:menu")]
   if p[2]=="trade":ui_send("🧹 *ORDERS & DUST*\n_Buy, Sell, Scan and Scan History are in Analyse → Setups._",[[_B("❌ Cancel Open Orders","m:r:cancel"),_B("🧹 Dust → BGB","m:r:dust")],_T])
   elif p[2]=="alerts":ui_send("🔔 *ALERTS*",[[_B("🔔 Set alert","m:al"),_B("📋 My alerts","m:r:alerts")],[_B("🗑 Cancel all alerts","m:r:cancelalerts")],_T])
   elif p[2]=="sys":ui_send("🛠️ *SYSTEM*",[[_B("🛠️ Audit","m:r:audit"),_B("🧩 Layout","m:lay")],[_B("🧠 Resume AI Mode","m:r:airesume")],_T])
  elif a=="more" and len(p)>3:ui_more(p[2],int(p[3]))
  elif a=="dash":
   ui_send("📊 *DASHBOARD*\nChoose a dashboard screen.",[
              [_B("📈 Performance","m:r:stats"),_B("🧾 Trade History","m:r:history")],
              [_B("🖥 System","m:r:bot")],
              [_B("⬅️ Tools","m:tools"),_B("🏠 Main Menu","m:menu")]
            ])
  elif a=="scan_hist":
   cmd_scan_history("/scan_history")
  elif a=="whylist":ui_whylist(int(p[2]) if len(p)>2 else 0)
  elif a=="why" and len(p)>2:_send(why_text(p[2]),"Markdown",{"inline_keyboard":[[_B("⬅️ Why list","m:whylist:0"),_B("🔎 Analyse "+p[2],"m:a:"+p[2])]]})
  elif a=="r" and len(p)>2 and p[2]=="history":
   H=load(HIST);lines=["🧾 *TRADE HISTORY*"]
   for x in H[-12:]:lines.append(f"{str(x.get('sym','?')).replace('-USDT','')} | {x.get('reason','?')} | PnL ${float(x.get('pnl',0)):+.2f}")
   _send("\n".join(lines),"Markdown",{"inline_keyboard":[[_B("⬅️ Menu","m:menu")]]})
  elif a=="r" and len(p)>2 and p[2]=="cancel":
   cmd_cancel_all()
  elif a=="ai" and len(p)>2 and p[2] in AI_Q:ask_ai(AI_Q[p[2]])
 except Exception as e:print(f"UI ERR {t}: {e}");tg(f"⚠️ Button failed, nothing changed: `{str(e)[:150]}`")
def handle(t,age):
 if t.startswith("m:"):ui_cb(t);return
 if t.startswith("pg:"):page_nav(t);return
 if t=="rf:go":
  c=load(ST,{}).get("last_cmd")
  if c in REFRESH:_UI["cmd"]=c;_UI["fresh"]=False;handle(c,age)
  return
 if not t.startswith(("/","scan_history")):
  try:
   _ap=load(ST,{}).get("amt_pending")
   if _ap:
    st=load(ST,{});st.pop("amt_pending",None);save(ST,st)
    if time.time()-float(_ap.get("t",0))<600:
     try:amt=round(float(t.replace(",","").replace("$","").strip()),2)
     except Exception:amt=None
     if amt and amt>0:
      c=_ap["coin"]
      if _ap["side"]=="b":ui_send(f"⚠️ *CONFIRM BUY*\n{c} for ${amt:g} at about {fp(live(f'{c}-USDT'))} USDT.",[[_B("✅ Confirm",f"m:bx:{c}:{amt:g}"),_B("❌ Cancel","m:menu")]])
      else:ui_send(f"⚠️ *CONFIRM SELL*\n${amt:g} of {c} at market.",[[_B("✅ Confirm",f"m:sxu:{c}:{amt:g}"),_B("❌ Cancel","m:menu")]])
      return
   _sp=load(ST,{}).get("search_pending")
   if _sp:
    st=load(ST,{});st.pop("search_pending",None);save(ST,st)
    if time.time()-float(_sp.get("t",0))<600:coin_search(t);return
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
 elif l=="panic_confirm":panic_sell() if age<=300 else tg("⌛ That confirmation expired.")
 elif l=="/sync":cmd_sync()
 elif l=="/ai_resume":ai_emergency_resume()
 elif l in ("/cancel","❌ cancel"):cmd_cancel_all()
 elif l=="/audit":
  r=audit_system()
  if r and r.startswith("✅"):tg(r)
 elif l=="trail_on":setc('trail_pct',8.0,"📈 Trailing Stop-Loss ON (8%)");menu()
 elif l=="trail_off":setc('trail_pct',0.0,"📉 Trailing Stop-Loss OFF");menu()
 elif l in ("/holdings","/portfolio","💼 holdings","💰 balance","/balance"):tg(get_holdings_text())
 elif l=="/scan_history" or l.startswith("/scan_history "):cmd_scan_history(t)
 elif l in ("/status","/bot","/system","🤖 bot","🖥 system"):cmd_status()
 elif l.startswith("/buy"):cmd_buy(t)
 elif l.startswith("/sell"):cmd_sell(t)
 elif l.startswith("/set"):cmd_set(t)
 elif l in ("🧠 ask ai", "ai_info"):_send(MSG["ai_info"]+"\n\nOr tap a question:","Markdown",{"inline_keyboard":[[_B("📈 Market overview","m:ai:1"),_B("💼 Review my holdings","m:ai:2")],[_B("🏆 Best coin to buy","m:ai:3"),_B("⚠️ Biggest risk","m:ai:4")],[_B("🛑 Closest to stop","m:ai:5"),_B("🧭 Safe to buy now?","m:ai:6")],[_B("📊 My results","m:ai:7"),_B("👀 Next 24 hours","m:ai:8")]]})
_AI_CMD={"/holdings":"holdings","/portfolio":"holdings","💼 holdings":"holdings","💰 balance":"holdings","/balance":"holdings","/stats":"performance stats","/alerts":"price alerts","/audit":"system audit","/scan_history":"scan history","m:r:history":"trade history"}
_AICAP={"on":False,"buf":[]}
def _ai_run(kind,fn,*a):
 # Runs a read-only command normally, then appends one advisory AI insight. Never delays or blocks the command's own output.
 global _send,_panel
 if _AICAP["on"]:return fn(*a)
 _AICAP["on"]=True;_AICAP["buf"]=[];s0,p0=_send,_panel
 def s1(m,mode,btn=None):
  _AICAP["buf"].append(str(m or ""));return s0(m,mode,btn)
 def p1(text,rows,touch=True):
  _AICAP["buf"].append(str(text or ""));return p0(text,rows,touch)
 _send,_panel=s1,p1
 try:fn(*a)
 finally:_send,_panel=s0,p0;_AICAP["on"]=False
 try:
  txt=re.sub(r"[`*_]","","\n".join(_AICAP["buf"]))[:3500]
  if len(txt)>=40:
   r=ai_stage('command_review',{'command':kind,'output':txt,'btc_filter_active':bool(btc_blocked(cfg()))},ttl=60,max_tokens=300,soft=True)
   if isinstance(r,dict):
    ins=str(r.get('insight') or r.get('key insight') or r.get('summary') or next(iter(r.values()),'')).strip()
    if ins:tg("🧠 *AI:* "+ins[:300])
 except Exception as e:print("CMD AI ERR",str(e)[:120])
_h0=handle;_u0=ui_cb
def handle(t,age):
 k=_AI_CMD.get(str(t).strip().lower())
 return _ai_run(k,_h0,t,age) if k else _h0(t,age)
def ui_cb(t):
 k=_AI_CMD.get(str(t).strip().lower())
 return _ai_run(k,_u0,t) if k else _u0(t)
_UB_CB={"at","ar","su","pk","fc","fv","fa","fd","nl","am","an","se","a"}
_ub_cb0=ui_cb;_ub_h0=handle
def ui_cb(t):
 p=str(t).split(":");_UI["ub"]=len(p)>1 and p[0]=="m" and (p[1] in _UB_CB or (p[1]=="more" and len(p)>2 and p[2] in ("analyse","fav")));_UI["ub_done"]=False
 try:return _ub_cb0(t)
 finally:_UI["ub"]=False
def handle(t,age):
 l=str(t).strip().lower();_UI["ub"]=l.startswith(("/analyse","🏆 analyse","/scan_history","scan_history"));_UI["ub_done"]=False
 try:return _ub_h0(t,age)
 finally:_UI["ub"]=False
_TG_N={"n":0}
def tg_check(poll=0):
 st=load(ST);lid=st.get("last_update_id",0)
 try:
  res=requests.get(f"{API}/getUpdates?offset={lid+1}&timeout={int(poll)}",timeout=10+int(poll)).json()
  if not (res.get("ok") and res.get("result")):return
  top=max(u["update_id"] for u in res["result"]);st["last_update_id"]=top;save(ST,st)
  try:requests.get(f"{API}/getUpdates?offset={top+1}&limit=1&timeout=0",timeout=10)
  except:pass
  # Last-in/last-out command policy: consume the entire Telegram update batch,
  # execute only the newest update addressed to this bot, and discard older pending commands.
  _eligible=[]
  for _u in res["result"]:
   _m=(_u.get("callback_query") or {}).get("message") if "callback_query" in _u else _u.get("message",{})
   if str((_m or {}).get("chat",{}).get("id",""))==CHAT:_eligible.append(_u)
  for _old in _eligible[:-1]:
   _om=(_old.get("callback_query") or {}).get("message") if "callback_query" in _old else _old.get("message",{})
   if _om and _om.get("message_id"):_del_msg(_om.get("message_id"))
  for u in _eligible[-1:]:
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
   if age>300:
    _del_msg(m.get("message_id"));continue
   _UI["mid"]=m.get("message_id") if "callback_query" in u else None
   if "callback_query" in u and (txt.startswith("m:") or txt.startswith("scan_history:")) and not _mid("panel_mid"):
    _set_mid("panel_mid",m.get("message_id"))
   _TG_N["n"]+=1
   if ("callback_query" in u or tapped) and (txt.startswith("m:") or txt.startswith("scan_history:")):_UI["edit_mode"]=True
   _UI["panel_used"]=False;_UI["fresh"]=True;_UI["cmd"]=txt if (("callback_query" in u) or tapped) else None;label="";_UI["acc"]=() if (("callback_query" in u) or tapped) else None
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

# ============================== FILE: bitget_bot/components/10_process_runtime.py ==============================
# Process lock, Git sync, scheduled loop, original startup sequence
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
  # GitHub sometimes answers 5xx for a moment; retry with waits and a re-sync before giving up (the runner's disk is thrown away after the run).
  push=run(["git","push"])
  for wait in (5,15,30):
   if push.returncode==0:break
   print(f"git push failed, retrying in {wait}s: {push.stderr[-120:]}")
   time.sleep(wait)
   rp=run(["git","pull","--rebase","--autostash"])
   if rp.returncode!=0:
    run(["git","rebase","--abort"])
    mm=run(["git","pull","--no-rebase","--no-edit","-s","recursive","-X","ours"])
    if mm.returncode!=0:run(["git","merge","--abort"])
   push=run(["git","push"])
  if push.returncode!=0:notify_fail("git push failed after 4 tries: "+push.stderr[-250:]);return
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
  if now-last_light>=30:
   for fn in (audit_ai,manage,check_alerts,scan,fast_watch,exec_q):
    if fn is scan and deadline-time.time()<60:continue
    run_step(fn)
   last_light=time.time()
  if now-last_heavy>=300:
   for fn in (auto_clear_daily,check_hourly_balance,check_daily_summary,stale_output):run_step(fn)
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
  for fn in (heartbeat,_C.clear,cleanup_telegram_output,stale_output,_kb_refresh,auto_clear_daily,scan_history_state,audit_system,tg_check,manage,track_day_equity,git_sync,check_hourly_balance,check_alerts,check_daily_summary,scan,system_audit,exec_q,coin_db_maintenance):run_step(fn)
  live_loop(T0)
  run_step(_kb_refresh)
 except Exception as e:
  try:tg(f"🚨 *CRASH:* `{e}`\n`{traceback.format_exc()[-200:]}`")
  except:pass
 git_sync();ping_watchdog()
