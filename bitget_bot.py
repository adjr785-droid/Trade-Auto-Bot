def get_holdings_text():
    try:
        balance = ex.fetch_balance()
        totals = balance.get('total') or {}
        usdt = float(totals.get('USDT') or 0)
    except Exception as e:
        return f"❌ Could not fetch exchange holdings: {e}"

    db = load(DB)
    lines = ["💼 *YOUR ASSET HOLDINGS*", ""]
    count = 0
    total_value = 0.0
    unpriced = []

    for coin, qty in sorted(totals.items()):
        try:
            qty = float(qty or 0)
        except (TypeError, ValueError):
            continue

        # USDT is displayed separately below, not as an asset.
        if qty <= 0 or coin == "USDT" or coin in STABLE or coin in BL:
            continue

        sym = f"{coin}-USDT"
        price = live(sym)
        if not price:
            unpriced.append(f"{coin}: {qty:.8g}")
            continue
        if qty * price < 0.10:
            continue

        total_value += qty * price
        entry_data = db.get(sym, {})
        entry = entry_data.get('entry_price')
        _, _, rsi, ma5, ma10 = data(sym)

        sentiment = "⚪ Unavailable"
        if rsi is not None and ma5 is not None and ma10 is not None:
            if price > ma5 > ma10 and rsi >= 50:
                sentiment = "🟢 Bullish"
            elif price < ma5 < ma10 and rsi < 50:
                sentiment = "🔴 Bearish"
            else:
                sentiment = "🟡 Neutral / Mixed"

        pnl_line = "Entry / PnL: Not tracked by bot"
        if entry:
            pnl = (price - entry) / entry * 100
            pnl_usdt = (price - entry) * qty
            pnl_line = (
                f"Entry: {fp(entry)} USDT | "
                f"PnL: {pnl:+.2f}% (${pnl_usdt:+.2f})"
            )

        lines.extend([
            f"🪙 *{coin}*",
            f"Quantity: {qty:.8g}",
            f"Current price: {fp(price)} USDT",
            f"Market value: ${qty * price:.2f}",
            pnl_line,
            f"RSI (1h): {rsi if rsi is not None else 'N/A'}",
            f"MA5 (1h): {fp(ma5) if ma5 is not None else 'N/A'}",
            f"MA10 (1h): {fp(ma10) if ma10 is not None else 'N/A'}",
            f"Market sentiment: {sentiment}",
            f"Bot trailing stop: {'ON (' + str(cfg().get('trail_pct', 0)) + '% below peak)' if cfg().get('trail_pct', 0) > 0 else 'OFF'}",
            f"Peak price: {fp(entry_data.get('peak_price')) if entry_data.get('peak_price') else 'N/A'}",
            f"Exchange stop level: {fp(entry_data.get('xsl')) if entry_data.get('xsl') else 'Not verified'}",
            ""
        ])
        count += 1

    if unpriced:
        note = "\n⚠️ Held but with no Bitget USDT price (dust/delisted): " + ", ".join(unpriced[:3])
        if len(unpriced) > 3:
            note += f" and {len(unpriced) - 3} more..."
        lines.append(note)

    lines.extend(["", f"💵 *USDT spot balance: ${usdt:.2f}*"])

    if count == 0:
        lines.insert(1, "No eligible USDT-priced holdings found.")

    if count:
        lines.insert(2, f"Assets: {count} | Estimated value: ${total_value:.2f} USDT")

    return "\n".join(lines)
