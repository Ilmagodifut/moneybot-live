// MONEYBOT Live: dati del bot (aggiornati a ogni giro orario) + prezzi in diretta da Binance.
(() => {
  const SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "LINK", "DOT", "LTC", "TRX", "SUI", "NEAR", "ATOM", "UNI", "TON", "APT"];
  const REST = "https://data-api.binance.vision/api/v3";
  const WS = "wss://data-stream.binance.vision/stream?streams=" + SYMBOLS.map((s) => s.toLowerCase() + "usdt@miniTicker").join("/");
  const BOT_MINUTES = [7, 37]; // il giro parte intorno ai minuti 7 e 37 di ogni ora

  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (id) => document.getElementById(id);
  const fmtUsd = (v) => "$" + d3.format(",.2f")(v);
  const fmtPct = d3.format("+.2%");
  const fmtPrice = (v) => (v >= 1000 ? d3.format(",.1f")(v) : v >= 1 ? d3.format(",.3f")(v) : d3.format(".4~g")(v));
  const parseT = (t) => new Date(String(t).replace(" ", "T") + ":00Z");

  const bot = { summary: null, equity: [], market: [], positions: [], trades: [], verdicts: [], comparison: [], backtest: null,
    fast_summary: null, fast_positions: [], fast_trades: [], fast_equity: [], fast_watch: [],
    multi_summary: null, multi_positions: [], multi_trades: [], multi_equity: [], multi_backtest: null };
  const live = {};   // simbolo -> {price, open24}
  const spark = {};  // simbolo -> prezzi di chiusura orari (48)

  // ---------- caricamento dati ----------
  async function loadBot() {
    const names = ["summary", "equity", "market", "positions", "trades", "verdicts", "comparison", "backtest",
      "fast_summary", "fast_positions", "fast_trades", "fast_equity", "fast_watch",
      "multi_summary", "multi_positions", "multi_trades", "multi_equity", "multi_backtest"];
    const bust = "?t=" + Date.now();
    const results = await Promise.allSettled(names.map((n) => fetch("data/" + n + ".json" + bust).then((r) => r.json())));
    results.forEach((r, i) => { if (r.status === "fulfilled") bot[names[i]] = r.value; });
    bot.summary = Array.isArray(bot.summary) ? bot.summary[0] : bot.summary;
    bot.fast_summary = Array.isArray(bot.fast_summary) ? bot.fast_summary[0] : bot.fast_summary;
    bot.multi_summary = Array.isArray(bot.multi_summary) ? bot.multi_summary[0] : bot.multi_summary;
    renderAll();
  }

  async function loadSparks() {
    await Promise.all(SYMBOLS.map(async (s) => {
      try {
        const rows = await fetch(`${REST}/klines?symbol=${s}USDT&interval=1h&limit=48`).then((r) => r.json());
        spark[s] = rows.map((k) => Number(k[4]));
      } catch (e) { /* la miniatura resta vuota */ }
    }));
    renderRadar();
  }

  async function pollPrices() {
    try {
      const syms = encodeURIComponent(JSON.stringify(SYMBOLS.map((s) => s + "USDT")));
      const rows = await fetch(`${REST}/ticker/24hr?symbols=${syms}`).then((r) => r.json());
      rows.forEach((t) => setPrice(t.symbol.replace("USDT", ""), Number(t.lastPrice), Number(t.openPrice)));
      onPrices();
    } catch (e) { /* riprova al prossimo giro */ }
  }

  let ws, wsOk = false, pollTimer;
  function connect() {
    try { ws = new WebSocket(WS); } catch (e) { startPolling(); return; }
    ws.onopen = () => { wsOk = true; setLive(true); stopPolling(); };
    ws.onmessage = (ev) => {
      const d = JSON.parse(ev.data).data;
      if (!d || !d.s) return;
      setPrice(d.s.replace("USDT", ""), Number(d.c), Number(d.o));
      schedulePrices();
    };
    ws.onclose = () => { wsOk = false; setLive(false); startPolling(); setTimeout(connect, 5000); };
    ws.onerror = () => ws.close();
  }
  function startPolling() { if (!pollTimer) { pollPrices(); pollTimer = setInterval(pollPrices, 5000); } }
  function stopPolling() { clearInterval(pollTimer); pollTimer = null; }

  function setPrice(sym, price, open24) {
    const prev = live[sym] && live[sym].price;
    live[sym] = { price, open24, dir: prev === undefined ? 0 : Math.sign(price - prev) };
  }
  let pricesQueued = false;
  function schedulePrices() {
    if (pricesQueued) return;
    pricesQueued = true;
    requestAnimationFrame(() => { pricesQueued = false; onPrices(); });
  }

  function setLive(on) {
    $("live-pill").classList.toggle("on", on);
    $("live-text").textContent = on ? "Live" : "Prezzi ogni 5 s";
  }

  // ---------- calcoli live ----------
  const priceOf = (sym) => (live[sym] ? live[sym].price : (bot.market.find((m) => m.symbol === sym) || {}).price);

  function liveNumbers() {
    const s = bot.summary;
    if (!s) return null;
    const held = bot.positions.reduce((sum, p) => sum + p.qty * (priceOf(p.symbol) ?? p.price), 0);
    const equity = s.cash + held;
    const ratios = bot.market.filter((m) => m.bench_start && priceOf(m.symbol)).map((m) => priceOf(m.symbol) / m.bench_start);
    const bench = ratios.length ? s.start_cash * d3.mean(ratios) : s.benchmark;
    return { equity, bench, pnl: equity / s.start_cash - 1, benchPct: bench / s.start_cash - 1 };
  }

  function liveFast() {
    const s = bot.fast_summary;
    if (!s) return null;
    return s.cash + bot.fast_positions.reduce((sum, p) => sum + p.qty * (priceOf(p.symbol) ?? p.price), 0);
  }

  // Prezzo per un prodotto del capitale multi-mercato: le crypto in diretta da Binance, il resto dall'ultimo giro.
  const multiPrice = (p) => {
    const sym = p.symbol.endsWith("-USD") ? p.symbol.slice(0, -4) : null;
    return (sym && live[sym] ? live[sym].price : null) ?? p.price;
  };
  function liveMulti() {
    const s = bot.multi_summary;
    if (!s) return null;
    return s.cash + bot.multi_positions.reduce((sum, p) => sum + p.qty * multiPrice(p), 0);
  }

  // Variazione del capitale rispetto a un momento passato, usando lo storico orario.
  function changeSince(history, live, startCash, sinceMs) {
    if (live == null) return null;
    let base = null;
    for (const h of history) {
      if (parseT(h.time) <= sinceMs) base = h.equity;
      else break;
    }
    if (base == null) base = history.length ? history[0].equity : startCash;
    return live / base - 1;
  }

  function renderDuo() {
    const now = Date.now();
    const midnight = new Date(); midnight.setUTCHours(0, 0, 0, 0);
    const sets = [
      ["slow", liveNumbers() && liveNumbers().equity, bot.equity, bot.summary, bot.positions.length],
      ["fast", liveFast(), bot.fast_equity, bot.fast_summary, bot.fast_positions.length],
      ["multi", liveMulti(), bot.multi_equity, bot.multi_summary, bot.multi_positions.length],
    ];
    for (const [k, live, hist, sum, npos] of sets) {
      if (!sum || live == null) continue;
      countTo($(k + "-eq"), live, fmtUsd);
      countTo($(k + "-day"), changeSince(hist, live, sum.start_cash, +midnight), fmtPct, true);
      countTo($(k + "-24"), changeSince(hist, live, sum.start_cash, now - 24 * 3600e3), fmtPct, true);
      countTo($(k + "-all"), live / sum.start_cash - 1, fmtPct, true);
      $(k + "-pos").textContent = npos ? `${npos} ${npos === 1 ? "posizione" : "posizioni"}` : "tutto in contanti";
    }
    if (bot.multi_summary) {
      $("multi-next").textContent = bot.multi_positions.length
        ? `Prossimo ribilanciamento: ${bot.multi_summary.next_rebalance}, dopo la chiusura di Wall Street.`
        : "Primo acquisto stasera dopo la chiusura di Wall Street (22:00 ora italiana).";
    }
    const w = $("fast-watch");
    const rows = (bot.fast_watch || []).map((x) => {
      const held = bot.fast_positions.find((p) => p.symbol === x.symbol);
      const price = priceOf(x.symbol) ?? x.price;
      const dist = price / x.mean - 1;
      const row = document.createElement("div");
      row.className = "watch-row";
      const b = document.createElement("b"); b.textContent = x.symbol;
      const bar = document.createElement("div"); bar.className = "watch-bar";
      const fill = document.createElement("i");
      // 0% = sulla media, 100% = soglia di acquisto raggiunta
      fill.style.width = Math.max(0, Math.min(100, (dist / x.trigger) * 100)) + "%";
      bar.append(fill);
      const em = document.createElement("em");
      em.textContent = held ? "in portafoglio" : `${fmtPct(dist)} dalla media · compra a ${d3.format(".0%")(x.trigger)}`;
      em.className = held ? "up" : "";
      row.append(b, bar, em);
      return row;
    });
    w.replaceChildren(...rows);
  }

  // ---------- numeri animati ----------
  function countTo(el, value, fmt, signed) {
    if (value === null || value === undefined || Number.isNaN(value)) { el.textContent = "—"; return; }
    const from = el._v ?? value * 0.0;
    const first = el._v === undefined;
    el._v = value;
    if (signed) { el.classList.toggle("up", value > 0); el.classList.toggle("down", value < 0); }
    if (reduce || (!first && Math.abs(value - from) < 1e-9)) { el.textContent = fmt(value); return; }
    cancelAnimationFrame(el._raf);
    const t0 = performance.now(), dur = first ? 1100 : 450;
    const step = (now) => {
      const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      el.textContent = fmt(k < 1 ? from + (value - from) * e : value);
      if (k < 1) el._raf = requestAnimationFrame(step);
    };
    el._raf = requestAnimationFrame(step);
  }

  // ---------- disegno ----------
  function renderKpis() {
    const s = bot.summary;
    if (!s) return;
    const n = liveNumbers();
    countTo($("k-equity"), n.equity, fmtUsd);
    countTo($("k-pnl"), n.pnl, fmtPct, true);
    countTo($("k-bench"), n.bench, fmtUsd);
    countTo($("k-bench-pct"), n.benchPct, fmtPct, true);
    countTo($("k-edge"), n.pnl - n.benchPct, fmtPct, true);
    countTo($("k-cash"), s.cash, fmtUsd);
    countTo($("k-open"), s.open_positions, d3.format("d"));
    countTo($("k-closed"), s.closed_trades, d3.format("d"));
    $("k-win").textContent = s.closed_trades ? d3.format(".0%")(s.winning_trades / s.closed_trades) : "—";
    countTo($("k-fees"), s.fees_paid, fmtUsd);
    $("status-pill").textContent = s.status === "halted" ? "Bot fermo per sicurezza" : "Bot attivo";
    $("status-pill").classList.toggle("bad", s.status === "halted");
    $("status-pill").classList.toggle("on", s.status !== "halted");
    $("mode-pill").textContent = s.mode === "claude" ? "Claude giudice" : "Solo regole";
    $("mode-pill").classList.toggle("ai", s.mode === "claude");
    $("bot-updated").textContent = s.updated;
  }

  function renderTicker() {
    const el = $("ticker");
    const make = () => SYMBOLS.map((sym) => {
      const span = document.createElement("span");
      span.className = "tick";
      span.dataset.sym = sym;
      const b = document.createElement("b"); b.textContent = sym;
      const p = document.createElement("span"); p.className = "p";
      const c = document.createElement("span"); c.className = "c";
      span.append(b, p, c);
      return span;
    });
    if (!el.children.length) el.append(...make(), ...make());
    el.querySelectorAll(".tick").forEach((t) => {
      const sym = t.dataset.sym, l = live[sym], m = bot.market.find((x) => x.symbol === sym);
      const price = l ? l.price : m && m.price;
      const chg = l ? l.price / l.open24 - 1 : m && m.change_24h;
      if (price === undefined) return;
      t.querySelector(".p").textContent = fmtPrice(price);
      const c = t.querySelector(".c");
      c.textContent = (chg >= 0 ? "▲ " : "▼ ") + fmtPct(chg);
      c.className = "c " + (chg >= 0 ? "up" : "down");
    });
  }

  function sparkSvg(values, up) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 100 34");
    svg.setAttribute("preserveAspectRatio", "none");
    if (!values || values.length < 2) return svg;
    const x = d3.scaleLinear([0, values.length - 1], [0, 100]);
    const y = d3.scaleLinear(d3.extent(values), [31, 3]);
    const color = up ? "#31e6a0" : "#ff5d7d";
    const area = document.createElementNS(ns, "path");
    area.setAttribute("d", d3.area().x((d, i) => x(i)).y0(34).y1((d) => y(d))(values));
    area.setAttribute("fill", color);
    area.setAttribute("fill-opacity", "0.12");
    const line = document.createElementNS(ns, "path");
    line.setAttribute("d", d3.line().x((d, i) => x(i)).y((d) => y(d))(values));
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", color);
    line.setAttribute("stroke-width", "1.6");
    line.setAttribute("vector-effect", "non-scaling-stroke");
    svg.append(area, line);
    return svg;
  }

  const trendLabel = { up: "trend su", down: "trend giù", sideways: "laterale" };
  function renderRadar() {
    const el = $("radar");
    const bySym = Object.fromEntries(bot.market.map((m) => [m.symbol, m]));
    if (!el.children.length) {
      SYMBOLS.forEach((sym, i) => {
        const tile = document.createElement("div");
        tile.className = "coin";
        tile.dataset.sym = sym;
        tile.style.animationDelay = i * 0.03 + "s";
        tile.innerHTML = '<div class="coin-top"><span class="coin-sym"></span><span class="coin-chg"></span></div><div class="coin-price"></div><div class="spark"></div><div class="coin-foot"><span class="rsi"></span><span class="tag"></span></div>';
        tile.querySelector(".coin-sym").textContent = sym;
        el.append(tile);
      });
    }
    el.querySelectorAll(".coin").forEach((tile) => {
      const sym = tile.dataset.sym, m = bySym[sym], l = live[sym];
      const price = l ? l.price : m && m.price;
      const chg = l ? l.price / l.open24 - 1 : m && m.change_24h;
      tile.classList.toggle("held", !!(m && m.held));
      tile.classList.toggle("cand", !!(m && m.candidate && !m.held));
      if (chg !== undefined) {
        const c = tile.querySelector(".coin-chg");
        c.textContent = fmtPct(chg);
        c.className = "coin-chg " + (chg >= 0 ? "up" : "down");
      }
      const p = tile.querySelector(".coin-price");
      if (price !== undefined) p.textContent = "$" + fmtPrice(price);
      if (l && l.dir && !reduce) {
        p.classList.remove("flash-up", "flash-down");
        void p.offsetWidth;
        p.classList.add(l.dir > 0 ? "flash-up" : "flash-down");
        clearTimeout(p._t);
        p._t = setTimeout(() => p.classList.remove("flash-up", "flash-down"), 120);
        l.dir = 0;
      }
      const sp = tile.querySelector(".spark");
      const values = spark[sym] ? spark[sym].slice(0, -1).concat(price ?? spark[sym].at(-1)) : null;
      if (values && (!sp._n || sp._n !== values.at(-1))) {
        sp._n = values.at(-1);
        sp.replaceChildren(sparkSvg(values, values.at(-1) >= values[0]));
      }
      if (m) {
        tile.querySelector(".rsi").textContent = "RSI " + Math.round(m.rsi);
        const tag = tile.querySelector(".tag");
        tag.textContent = trendLabel[m.trend] || m.trend;
        tag.className = "tag " + (m.trend === "up" ? "up" : m.trend === "down" ? "down" : "");
      }
    });
  }

  function td(text, cls) {
    const c = document.createElement("td");
    if (cls) c.className = cls;
    c.textContent = text;
    return c;
  }
  function badge(text, cls) {
    const c = document.createElement("td");
    const s = document.createElement("span");
    s.className = "badge " + cls;
    s.textContent = text;
    c.append(s);
    return c;
  }
  function signed(v, fmt) {
    if (v === null || v === undefined) return td("", "num");
    return td((v > 0 ? "+" : v < 0 ? "-" : "") + fmt(Math.abs(v)), "num " + (v > 0 ? "up" : v < 0 ? "down" : ""));
  }
  const usd2 = (v) => "$" + d3.format(",.2f")(v);

  function renderPositions() {
    $("positions-empty").hidden = bot.positions.length > 0;
    $("positions").replaceChildren(...bot.positions.map((p) => {
      const price = priceOf(p.symbol) ?? p.price;
      const tr = document.createElement("tr");
      tr.append(td(p.symbol), td(fmtPrice(p.entry_price), "num"), td(fmtPrice(price), "num"),
        signed(p.qty * price - p.invested, usd2), td(fmtPrice(p.stop), "num"));
      return tr;
    }));
  }

  function renderTables() {
    const trades = bot.trades.map((t) => ({ ...t, cap: "Lento" }))
      .concat(bot.fast_trades.map((t) => ({ ...t, cap: "Rapido" })))
      .concat(bot.multi_trades.map((t) => ({ ...t, cap: "200 prodotti" })))
      .sort((a, b) => (a.time < b.time ? 1 : -1));
    $("trades-empty").hidden = trades.length > 0;
    $("trades").replaceChildren(...trades.map((t) => {
      const tr = document.createElement("tr");
      tr.append(td(t.time), td(t.cap), td(t.symbol), badge(t.side === "BUY" ? "Acquisto" : "Vendita", t.side === "BUY" ? "buy" : "sell"),
        td(usd2(t.value), "num"), signed(t.pnl, usd2), td(t.reason));
      return tr;
    }));

    const verdicts = bot.verdicts.slice().reverse();
    const actionLabel = { buy: "compra", watch: "aspetta", pass: "passa" };
    $("verdicts-empty").hidden = verdicts.length > 0;
    $("verdicts").replaceChildren(...verdicts.map((v) => {
      const tr = document.createElement("tr");
      const judged = v.action ? badge(actionLabel[v.action] || v.action, v.action) : badge(v.passed ? "via libera" : "scartata", v.passed ? "buy" : "pass");
      tr.append(td(v.time), td(v.symbol), td(v.mode === "claude" ? "Claude" : "Regole"), judged,
        td(v.action_prob == null ? "" : d3.format(".0%")(v.action_prob), "num"),
        badge(v.bought ? "comprata" : "non comprata", v.bought ? "buy" : "pass"),
        v.outcome_24h == null ? td("in attesa", "num") : signed(v.outcome_24h, d3.format(".2%")));
      return tr;
    }));
  }

  let chartDrawn = false;
  function renderChart() {
    const el = $("chart");
    const hist = bot.equity.map((d) => ({ t: parseT(d.time), eq: d.equity, bh: d.benchmark }));
    const n = liveNumbers();
    if (!hist.length && !n) return;
    const data = n ? hist.concat({ t: new Date(), eq: n.equity, bh: n.bench, live: true }) : hist;
    const fastHist = bot.fast_equity.map((d) => ({ t: parseT(d.time), v: d.equity }));
    const fl = liveFast();
    const fastData = fl != null ? fastHist.concat({ t: new Date(), v: fl, live: true }) : fastHist;
    const multiLive = liveMulti();
    const multiHist = bot.multi_equity.map((d) => ({ t: parseT(d.time), v: d.equity }));
    const multiData = multiLive != null ? multiHist.concat({ t: new Date(), v: multiLive, live: true }) : multiHist;
    const W = Math.max(280, el.clientWidth), H = 290;
    el.replaceChildren();
    const vals = data.flatMap((d) => [d.eq, d.bh]).concat(fastData.map((d) => d.v), multiData.map((d) => d.v));
    let lo = d3.min(vals), hi = d3.max(vals);
    const pad = Math.max((hi - lo) * 0.25, hi * 0.003);
    lo -= pad; hi += pad;
    let [t0, t1] = d3.extent(data, (d) => d.t);
    if (t1 - t0 < 3 * 3600e3) t0 = new Date(+t1 - 3 * 3600e3);

    const svg = d3.select(el).append("svg").attr("width", "100%").attr("viewBox", `0 0 ${W} ${H}`)
      .attr("role", "img").attr("aria-label", "Capitale del bot e del compra e tieni nel tempo");
    const y = d3.scaleLinear([lo, hi], [H - 24, 10]);
    const yAxis = d3.axisLeft(y).ticks(5).tickFormat((v) => "$" + d3.format(",.0f")(v));
    const probe = svg.append("g").attr("class", "axis").call(yAxis);
    let left = 0;
    probe.selectAll("text").each(function () { left = Math.max(left, this.getComputedTextLength()); });
    probe.remove();
    const ml = left + 14;
    const x = d3.scaleUtc([t0, t1], [ml, W - 16]);
    svg.append("g").attr("class", "grid").attr("transform", `translate(${ml},0)`).call(d3.axisLeft(y).ticks(5).tickSize(-(W - ml - 16)).tickFormat(""));
    svg.append("g").attr("class", "axis").attr("transform", `translate(${ml},0)`).call(yAxis).attr("font-size", null).attr("font-family", null);
    svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - 24})`)
      .call(d3.axisBottom(x).ticks(Math.max(2, Math.floor(W / 120))).tickFormat(d3.utcFormat("%d/%m %H:%M")))
      .attr("font-size", null).attr("font-family", null);

    const defs = svg.append("defs");
    const g = defs.append("linearGradient").attr("id", "area").attr("x1", 0).attr("x2", 0).attr("y1", 0).attr("y2", 1);
    [[0, 0.3], [0.5, 0.09], [1, 0]].forEach(([o, a]) => g.append("stop").attr("offset", o).attr("stop-color", "#3ee0ff").attr("stop-opacity", a));
    const f = defs.append("filter").attr("id", "glow").attr("x", "-20%").attr("y", "-20%").attr("width", "140%").attr("height", "140%");
    f.append("feGaussianBlur").attr("stdDeviation", 3).attr("result", "b");
    const m = f.append("feMerge"); m.append("feMergeNode").attr("in", "b"); m.append("feMergeNode").attr("in", "SourceGraphic");

    svg.append("path").datum(data).attr("fill", "url(#area)").attr("d", d3.area().x((d) => x(d.t)).y0(H - 24).y1((d) => y(d.eq)));
    svg.append("path").datum(data).attr("fill", "none").attr("stroke", "#8f7dff").attr("stroke-width", 2).attr("stroke-dasharray", "5 4")
      .attr("d", d3.line().x((d) => x(d.t)).y((d) => y(d.bh)));
    const eq = svg.append("path").datum(data).attr("fill", "none").attr("stroke", "#3ee0ff").attr("stroke-width", 2.5)
      .attr("stroke-linecap", "round").attr("stroke-linejoin", "round").attr("filter", "url(#glow)")
      .attr("d", d3.line().x((d) => x(d.t)).y((d) => y(d.eq)));
    if (!chartDrawn && !reduce) {
      eq.attr("class", "line-eq").style("--len", eq.node().getTotalLength());
      chartDrawn = true;
    }
    if (multiData.length) {
      svg.append("path").datum(multiData).attr("fill", "none").attr("stroke", "#ffc65a").attr("stroke-width", 2.2)
        .attr("stroke-linecap", "round").attr("d", d3.line().x((d) => x(d.t)).y((d) => y(d.v)));
      const lm = multiData[multiData.length - 1];
      svg.append("circle").attr("cx", x(lm.t)).attr("cy", y(lm.v)).attr("r", 4.5).attr("fill", "#ffc65a");
    }
    if (fastData.length) {
      svg.append("path").datum(fastData).attr("fill", "none").attr("stroke", "#ff7ac6").attr("stroke-width", 2.2)
        .attr("stroke-linecap", "round").attr("d", d3.line().x((d) => x(d.t)).y((d) => y(d.v)));
      const lf = fastData[fastData.length - 1];
      svg.append("circle").attr("cx", x(lf.t)).attr("cy", y(lf.v)).attr("r", 4.5).attr("fill", "#ff7ac6");
    }
    const last = data[data.length - 1];
    svg.append("circle").attr("cx", x(last.t)).attr("cy", y(last.bh)).attr("r", 4).attr("fill", "#8f7dff");
    svg.append("circle").attr("class", last.live ? "live-dot" : "").attr("cx", x(last.t)).attr("cy", y(last.eq)).attr("r", 5).attr("fill", "#3ee0ff").attr("filter", "url(#glow)");

    const tip = d3.select(el).append("div").attr("class", "tip").style("display", "none");
    const rule = svg.append("line").attr("y1", 10).attr("y2", H - 24).attr("stroke", "rgba(231,238,251,.25)").style("display", "none");
    const bis = d3.bisector((d) => d.t).center;
    svg.append("rect").attr("x", ml).attr("width", W - ml).attr("height", H).attr("fill", "transparent")
      .on("pointermove", (event) => {
        const [mx] = d3.pointer(event);
        const d = data[bis(data, x.invert(mx))], px = x(d.t);
        rule.style("display", null).attr("x1", px).attr("x2", px);
        tip.style("display", null).selectAll("*").remove();
        tip.append("div").attr("class", "t").text(d.live ? "Adesso (live)" : d3.utcFormat("%d/%m %H:%M UTC")(d.t));
        const a = tip.append("div"); a.append("i").style("background", "#3ee0ff"); a.append("span").text("Lento " + fmtUsd(d.eq));
        const fd = fastData.length ? fastData[Math.min(fastData.length - 1, d3.bisector((q) => q.t).center(fastData, d.t))] : null;
        if (fd) { const f = tip.append("div"); f.append("i").style("background", "#ff7ac6"); f.append("span").text("Rapido " + fmtUsd(fd.v)); }
        const md = multiData.length ? multiData[Math.min(multiData.length - 1, d3.bisector((q) => q.t).center(multiData, d.t))] : null;
        if (md) { const g = tip.append("div"); g.append("i").style("background", "#ffc65a"); g.append("span").text("200 prodotti " + fmtUsd(md.v)); }
        const b = tip.append("div"); b.append("i").style("background", "#8f7dff"); b.append("span").text("Compra e tieni " + fmtUsd(d.bh));
        const box = el.getBoundingClientRect(), tw = tip.node().offsetWidth;
        tip.style("left", Math.min(Math.max(0, (px / W) * box.width + 12), box.width - tw) + "px").style("top", "12px");
      })
      .on("pointerleave", () => { tip.style("display", "none"); rule.style("display", "none"); });
  }

  // ---------- backtest e confronto ----------
  const pct1 = (v) => (v == null ? "—" : (v > 0 ? "+" : "") + d3.format(".1%")(v));
  function pctCell(v) {
    const c = td(pct1(v), "num " + (v > 0 ? "up" : v < 0 ? "down" : ""));
    return c;
  }

  function renderBacktest() {
    const bt = bot.backtest;
    if (!bt || !bt.variants) return;
    $("bt-period").textContent = `${bt.start} – ${bt.end} (verifica dal ${bt.split})`;
    const live = bt.variants.find((v) => v.name === bt.live) || bt.variants[0];
    const hourly = bt.variants.find((v) => v.name.startsWith("orarie"));
    const h = $("bt-headline");
    h.replaceChildren();
    const parts = [
      "Sui 2 anni le regole attuali fanno ", ["b", pct1(live.full.return)],
      ", le prime regole orarie ", ["b", pct1(hourly ? hourly.full.return : null)],
      " e il semplice compra e tieni ", ["b", pct1(bt.buy_and_hold.full)],
      ". Nel periodo di verifica: ", ["b", pct1(live.test.return)], " contro ", ["b", pct1(bt.buy_and_hold.test)], " del mercato.",
    ];
    parts.forEach((p) => {
      if (Array.isArray(p)) { const b = document.createElement("b"); b.textContent = p[1]; h.append(b); }
      else h.append(p);
    });

    $("bt-table").replaceChildren(...bt.variants.map((v) => {
      const tr = document.createElement("tr");
      if (v.name === bt.live) tr.className = "live";
      const name = td(v.name);
      name.title = v.description;
      tr.append(name, td(v.family || ""), pctCell(v.train.return), pctCell(v.test.return), pctCell(v.full.return),
        td("-" + d3.format(".0%")(v.full.max_drawdown), "num"), td(String(v.full.trades), "num"),
        td(v.full.win_rate == null ? "—" : d3.format(".0%")(v.full.win_rate), "num"));
      return tr;
    }));

    const el = $("bt-chart");
    const data = bt.curve.map((d) => ({ t: new Date(d.time + "T00:00:00Z"), a: d.attuale, o: d.orarie, h: d.compra_e_tieni }));
    if (!data.length) return;
    const W = Math.max(280, el.clientWidth), H = 260;
    el.replaceChildren();
    const vals = data.flatMap((d) => [d.a, d.o, d.h]).filter((v) => v != null);
    const y = d3.scaleLinear(d3.extent(vals), [H - 24, 10]).nice();
    const svg = d3.select(el).append("svg").attr("width", "100%").attr("viewBox", `0 0 ${W} ${H}`)
      .attr("role", "img").attr("aria-label", "Backtest: regole attuali, prime regole e compra e tieni");
    const yAxis = d3.axisLeft(y).ticks(5).tickFormat((v) => "$" + d3.format(",.0f")(v));
    const probe = svg.append("g").attr("class", "axis").call(yAxis);
    let left = 0;
    probe.selectAll("text").each(function () { left = Math.max(left, this.getComputedTextLength()); });
    probe.remove();
    const ml = left + 14;
    const x = d3.scaleUtc(d3.extent(data, (d) => d.t), [ml, W - 16]);
    svg.append("g").attr("class", "grid").attr("transform", `translate(${ml},0)`).call(d3.axisLeft(y).ticks(5).tickSize(-(W - ml - 16)).tickFormat(""));
    svg.append("g").attr("class", "axis").attr("transform", `translate(${ml},0)`).call(yAxis).attr("font-size", null).attr("font-family", null);
    svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - 24})`)
      .call(d3.axisBottom(x).ticks(Math.max(2, Math.floor(W / 110))).tickFormat(d3.utcFormat("%b %y")))
      .attr("font-size", null).attr("font-family", null);
    const split = new Date(bt.split + "T00:00:00Z");
    svg.append("line").attr("x1", x(split)).attr("x2", x(split)).attr("y1", 10).attr("y2", H - 24)
      .attr("stroke", "rgba(231,238,251,.35)").attr("stroke-dasharray", "3 4");
    svg.append("text").attr("x", x(split) + 6).attr("y", 20).attr("fill", "#8794ad").attr("font-size", 10).attr("font-family", "JetBrains Mono, monospace").text("verifica →");
    const line = (key) => d3.line().defined((d) => d[key] != null).x((d) => x(d.t)).y((d) => y(d[key]));
    svg.append("path").datum(data).attr("fill", "none").attr("stroke", "#8f7dff").attr("stroke-width", 1.6).attr("stroke-dasharray", "5 4").attr("d", line("h"));
    svg.append("path").datum(data).attr("fill", "none").attr("stroke", "#ffc65a").attr("stroke-width", 1.6).attr("stroke-opacity", 0.85).attr("d", line("o"));
    svg.append("path").datum(data).attr("fill", "none").attr("stroke", "#3ee0ff").attr("stroke-width", 2.4).attr("filter", "url(#glow)").attr("d", line("a"));

    const tip = d3.select(el).append("div").attr("class", "tip").style("display", "none");
    const bis = d3.bisector((d) => d.t).center;
    svg.append("rect").attr("x", ml).attr("width", W - ml).attr("height", H).attr("fill", "transparent")
      .on("pointermove", (event) => {
        const d = data[bis(data, x.invert(d3.pointer(event)[0]))];
        tip.style("display", null).selectAll("*").remove();
        tip.append("div").attr("class", "t").text(d3.utcFormat("%d/%m/%Y")(d.t));
        [["#3ee0ff", "Attuali", d.a], ["#ffc65a", "Orarie", d.o], ["#8f7dff", "Compra e tieni", d.h]].forEach(([c, n, v]) => {
          if (v == null) return;
          const r = tip.append("div"); r.append("i").style("background", c); r.append("span").text(n + " " + fmtUsd(v));
        });
        const box = el.getBoundingClientRect(), tw = tip.node().offsetWidth;
        tip.style("left", Math.min(Math.max(0, (x(d.t) / W) * box.width + 12), box.width - tw) + "px").style("top", "12px");
      })
      .on("pointerleave", () => tip.style("display", "none"));
  }

  function renderComparison() {
    const rows = bot.comparison || [];
    $("cmp-table").replaceChildren(...rows.map((r) => {
      const tr = document.createElement("tr");
      tr.append(td(r.group), td(String(r.cases), "num"),
        r.avg_24h == null ? td("in attesa", "num") : pctCell(r.avg_24h),
        td(r.green_rate == null ? "—" : d3.format(".0%")(r.green_rate), "num"));
      return tr;
    }));
  }

  function renderMulti() {
    const pos = bot.multi_positions || [];
    $("multi-empty").hidden = pos.length > 0;
    $("multi-table").replaceChildren(...pos.slice().sort((a, b) => b.qty * multiPrice(b) - a.qty * multiPrice(a)).map((p) => {
      const tr = document.createElement("tr");
      const value = p.qty * multiPrice(p);
      tr.append(td(p.symbol.replace("-USD", "")), td(p.kind), td(usd2(p.invested), "num"), td(usd2(value), "num"), signed(value - p.invested, usd2));
      return tr;
    }));
    const bt = bot.multi_backtest;
    if (!bt) return;
    $("multi-bt-note").textContent = `Dal ${bt.start} al ${bt.end} su ${bt.products} prodotti; regola scelta sugli anni ${bt.start.slice(0, 4)}-${bt.split.slice(0, 4)} e verificata sugli anni successivi.`;
    const best = bt.variants.find((v) => v.name === bt.best);
    const rows = [["Tutti i 200 prodotti", best]].concat(bt.by_kind.map((k) => [k.universe.replace("solo ", "Solo "), k]))
      .concat([["Borsa USA (S&P 500), compra e tieni", bt.spy]]);
    $("multi-bt").replaceChildren(...rows.map(([name, r]) => {
      const tr = document.createElement("tr");
      tr.append(td(name), pctCell(r.train.cagr), pctCell(r.test.cagr), td("-" + d3.format(".0%")(r.full.max_drawdown), "num"));
      return tr;
    }));
  }

  function renderAll() { renderMulti(); renderDuo(); renderKpis(); renderTicker(); renderRadar(); renderPositions(); renderTables(); renderChart(); renderBacktest(); renderComparison(); }

  // I prezzi cambiano più volte al secondo: KPI e radar subito, grafico al massimo ogni 5 s.
  let lastChart = 0;
  function onPrices() {
    renderDuo(); renderKpis(); renderTicker(); renderRadar(); renderPositions(); renderMulti();
    if (performance.now() - lastChart > 5000) { lastChart = performance.now(); renderChart(); }
  }

  // ---------- conto alla rovescia al prossimo giro ----------
  function tickCountdown() {
    const now = new Date();
    const candidates = BOT_MINUTES.flatMap((m) => [0, 1].map((h) => {
      const d = new Date(now);
      d.setUTCMinutes(m, 0, 0);
      d.setUTCHours(d.getUTCHours() + h);
      return d;
    })).filter((d) => d > now);
    const next = new Date(Math.min(...candidates));
    const s = Math.round((next - now) / 1000);
    $("countdown").textContent = "~" + String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
  }

  // ---------- sfondo animato ----------
  function background() {
    const c = $("bg"), ctx = c.getContext("2d");
    let w, h, pts;
    const resize = () => {
      w = c.width = innerWidth * devicePixelRatio;
      h = c.height = innerHeight * devicePixelRatio;
      pts = Array.from({ length: Math.min(70, Math.floor(innerWidth / 18)) }, () => ({
        x: Math.random() * w, y: Math.random() * h, vx: (Math.random() - 0.5) * 0.25 * devicePixelRatio, vy: (Math.random() - 0.5) * 0.25 * devicePixelRatio,
      }));
    };
    resize();
    addEventListener("resize", resize);
    const link = 140 * devicePixelRatio;
    const frame = () => {
      ctx.clearRect(0, 0, w, h);
      const g = ctx.createRadialGradient(w * 0.85, 0, 0, w * 0.85, 0, w * 0.7);
      g.addColorStop(0, "rgba(143,125,255,0.16)"); g.addColorStop(1, "rgba(143,125,255,0)");
      ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
      for (const p of pts) {
        p.x += p.vx; p.y += p.vy;
        if (p.x < 0 || p.x > w) p.vx *= -1;
        if (p.y < 0 || p.y > h) p.vy *= -1;
      }
      for (let i = 0; i < pts.length; i++) {
        for (let j = i + 1; j < pts.length; j++) {
          const dx = pts[i].x - pts[j].x, dy = pts[i].y - pts[j].y, d = Math.hypot(dx, dy);
          if (d < link) {
            ctx.strokeStyle = `rgba(62,224,255,${0.12 * (1 - d / link)})`;
            ctx.lineWidth = devicePixelRatio;
            ctx.beginPath(); ctx.moveTo(pts[i].x, pts[i].y); ctx.lineTo(pts[j].x, pts[j].y); ctx.stroke();
          }
        }
        ctx.fillStyle = "rgba(62,224,255,0.45)";
        ctx.beginPath(); ctx.arc(pts[i].x, pts[i].y, 1.4 * devicePixelRatio, 0, Math.PI * 2); ctx.fill();
      }
      if (!reduce) requestAnimationFrame(frame);
    };
    frame();
  }

  // ---------- avvio ----------
  background();
  tickCountdown();
  setInterval(tickCountdown, 1000);
  loadBot().then(() => { connect(); startPolling(); loadSparks(); });
  setInterval(loadBot, 60 * 1000);
  setInterval(loadSparks, 5 * 60 * 1000);
  addEventListener("resize", () => { renderChart(); renderBacktest(); });
})();
