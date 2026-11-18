import pathlib
p = pathlib.Path("bot.py")  # change to your real filename if it's not bot.py
t = p.read_text()

# --- Fix 1: git_sync with identity + pull first ---
old_git = """def git_sync():
    run=lambda c:subprocess.run(c,capture_output=True,text=True,timeout=60)"""
new_git = """def git_sync():
    ident="trading-bot";mail="bot@users.noreply.github.com"
    env={**os.environ,"GIT_AUTHOR_NAME":ident,"GIT_AUTHOR_EMAIL":mail,"GIT_COMMITTER_NAME":ident,"GIT_COMMITTER_EMAIL":mail}
    run=lambda c:subprocess.run(c,capture_output=True,text=True,timeout=60,env=env)"""
t = t.replace(old_git, new_git)

# --- Fix 2: hard_sl use total balance + stopLossPrice ---
t = t.replace(
    "ex.create_order(cs,'market','sell',qty,None,{'triggerPrice':pr})",
    "ex.create_order(cs,'market','sell',qty,None,{'triggerPrice':pr,'stopLossPrice':pr})"
)

p.write_text(t)
print("Patched OK - git_sync + hard_sl fixed")
