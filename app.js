/* app.js — UI 統括ロジック */
(function () {
  "use strict";

  const state = {
    cfg: null,
    portfolio: null,
    lastData: null,      // {ticker: ohlcv}
    lastSignals: null,   // [{ticker, signal, reason, prob, close, size_mult}]
    lastOrders: null,
    lastPrices: null,
  };

  // ---------------- 汎用ユーティリティ ----------------
  function yen(x) {
    if (!Number.isFinite(x)) return "-";
    return Math.round(x).toLocaleString("ja-JP") + "円";
  }
  function pct(x, digits) {
    digits = digits === undefined ? 2 : digits;
    if (!Number.isFinite(x)) return "-";
    return (x >= 0 ? "+" : "") + (x * 100).toFixed(digits) + "%";
  }
  function el(id) { return document.getElementById(id); }
  function h(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  let toastTimer = null;
  function toast(msg, ms) {
    const t = el("toast");
    t.textContent = msg;
    t.style.display = "block";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.style.display = "none"; }, ms || 3200);
  }

  function appendLog(boxId, msg, cls) {
    const box = el(boxId);
    if (box.dataset.empty) { box.textContent = ""; box.dataset.empty = ""; }
    const line = document.createElement("div");
    if (cls) line.className = cls;
    line.textContent = msg;
    box.appendChild(line);
    box.scrollTop = box.scrollHeight;
  }
  function clearLog(boxId, placeholder) {
    const box = el(boxId);
    box.textContent = placeholder;
    box.dataset.empty = "1";
  }

  // ---------------- タブ切り替え ----------------
  function switchView(id) {
    document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === id));
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === id));
    if (id === "view-portfolio") renderPortfolioView();
  }
  document.querySelectorAll(".tab-btn").forEach((b) => {
    b.addEventListener("click", () => switchView(b.dataset.view));
  });

  // ---------------- 確認パネル（window.confirmの代わり） ----------------
  function showConfirmPanel(afterEl, itemsHtml, onConfirm, onCancel) {
    const old = document.getElementById("inline-confirm");
    if (old) old.remove();
    const panel = h("div", "card");
    panel.id = "inline-confirm";
    panel.innerHTML = `<h2>実行内容の確認</h2><div style="font-size:13px;line-height:1.9;">${itemsHtml}</div>
      <div class="btn-row" style="margin-top:12px;">
        <button class="btn ghost" id="confirm-cancel">やめる</button>
        <button class="btn primary" id="confirm-ok">実行する</button>
      </div>`;
    afterEl.insertAdjacentElement("afterend", panel);
    el("confirm-cancel").onclick = () => { panel.remove(); if (onCancel) onCancel(); };
    el("confirm-ok").onclick = () => { panel.remove(); onConfirm(); };
  }

  // ---------------- SVG 簡易折れ線グラフ ----------------
  function sparklineSvg(values, color) {
    if (!values.length) return "";
    const w = 300, hgt = 90, pad = 4;
    const lo = Math.min(...values), hi = Math.max(...values);
    const span = hi - lo || 1;
    const pts = values.map((v, i) => {
      const x = pad + (i / Math.max(1, values.length - 1)) * (w - pad * 2);
      const y = hgt - pad - ((v - lo) / span) * (hgt - pad * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    const line = `<polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    const areaPts = `${pad},${hgt - pad} ` + pts.join(" ") + ` ${w - pad},${hgt - pad}`;
    const area = `<polygon points="${areaPts}" fill="${color}" opacity="0.10"/>`;
    return area + line;
  }

  // ---------------- ティッカーテープ ----------------
  function renderTape(signals) {
    const wrap = el("tape-wrap"), track = el("tape-track");
    if (!signals || !signals.length) { wrap.style.display = "none"; return; }
    const make = (s) => {
      const cls = s.signal === "BUY候補" ? "up" : s.signal === "SELL" ? "down" : "flat";
      const probTxt = Number.isFinite(s.prob) ? s.prob.toFixed(2) : "-";
      return `<span class="tick-item"><span class="tick-dot ${cls}"></span><span class="code">${s.ticker}</span><span class="prob ${cls}">${probTxt}</span></span>`;
    };
    const doubled = signals.concat(signals);
    track.innerHTML = doubled.map(make).join("");
    wrap.style.display = "block";
  }

  // ---------------- 今日のシグナル画面 ----------------
  function signalTagClass(sig) {
    if (sig === "BUY候補") return "buy";
    if (sig === "SELL") return "sell";
    if (sig === "HOLD") return "hold";
    if (sig === "対象外") return "excl";
    return "watch";
  }

  function renderSignals(signals, pf) {
    const box = el("today-signals");
    box.innerHTML = "";
    const order = { SELL: 0, "BUY候補": 1, HOLD: 2, "様子見": 3, "対象外": 4 };
    const sorted = signals.slice().sort((a, b) => {
      const oa = order[a.signal] ?? 9, ob = order[b.signal] ?? 9;
      if (oa !== ob) return oa - ob;
      const pa = Number.isFinite(a.prob) ? a.prob : -1, pb = Number.isFinite(b.prob) ? b.prob : -1;
      return pb - pa;
    });
    for (const s of sorted) {
      const row = h("div", "signal-row");
      const held = !!pf.positions[s.ticker];
      const priceTxt = Number.isFinite(s.close) ? s.close.toLocaleString("ja-JP", { maximumFractionDigits: 1 }) : "-";
      row.innerHTML = `
        <div class="signal-left">
          <span class="held-dot ${held ? "on" : ""}"></span>
          <div>
            <div class="signal-ticker">${s.ticker}</div>
            <div class="signal-reason">${s.reason}</div>
          </div>
        </div>
        <div class="signal-right">
          <span class="signal-tag ${signalTagClass(s.signal)}">${s.signal}</span>
          <div class="signal-price">終値 ${priceTxt}</div>
        </div>`;
      box.appendChild(row);
    }
  }

  function renderTodayHero(pf, prices) {
    const equity = Engine.portfolioEquity(pf, prices || {});
    const init = pf.initial_capital || equity;
    el("today-equity").textContent = yen(equity);
    const delta = equity / init - 1;
    const deltaEl = el("today-delta");
    deltaEl.textContent = `初期資金比 ${pct(delta)}`;
    deltaEl.className = "delta num " + (delta >= 0 ? "up" : "down");
    el("today-cash").textContent = yen(pf.cash);
    el("today-posn").textContent = Object.keys(pf.positions).length + " / " + state.cfg.max_positions;
  }

  async function doFetch() {
    const btn = el("btn-fetch");
    btn.disabled = true; btn.textContent = "取得中...";
    clearLog("fetch-log", "");
    try {
      const { data, failed } = await Store.fetchAll(state.cfg, {
        onProgress: (i, n, ticker, status, err) => {
          if (status === "cache") appendLog("fetch-log", `[${i}/${n}] ${ticker}: キャッシュ利用`);
          else if (status === "ok") appendLog("fetch-log", `[${i}/${n}] ${ticker}: OK`, "ok");
          else appendLog("fetch-log", `[${i}/${n}] ${ticker}: 失敗 (${err})`, "err");
        },
      });
      state.lastData = data;
      if (failed.length) toast(`${failed.length}銘柄の取得に失敗しました（ログ参照）`);
      const { signals, dataDate } = Engine.computeLiveSignals(data, state.cfg, state.portfolio);
      state.lastSignals = signals;
      if (Object.keys(state.portfolio.positions).length) await Store.savePortfolio(state.portfolio);
      const age = dataDate ? Math.floor((Date.now() - new Date(dataDate).getTime()) / 86400000) : null;
      el("today-datadate").textContent = dataDate
        ? `データ最終日: ${dataDate}${age !== null && age >= 5 ? `（${age}日前、休場か更新遅延の可能性）` : ""}`
        : "データがありません。";
      renderSignals(signals, state.portfolio);
      renderTape(signals);
      renderTodayHero(state.portfolio, Object.fromEntries(signals.map((s) => [s.ticker, s.close])));
      const { orders, prices } = Engine.planOrders(signals, state.cfg, state.portfolio);
      state.lastOrders = orders; state.lastPrices = prices;
      el("btn-execute").disabled = orders.length === 0;
    } catch (e) {
      appendLog("fetch-log", "エラー: " + e.message, "err");
      toast("データ取得でエラーが発生しました");
    } finally {
      btn.disabled = false; btn.textContent = "データ取得";
    }
  }

  function doExecute() {
    if (!state.lastOrders || !state.lastOrders.length) { toast("実行する注文はありません"); return; }
    const items = state.lastOrders.map((o) => {
      const px = state.lastPrices[o.ticker];
      const pxTxt = Number.isFinite(px) ? px.toLocaleString("ja-JP", { maximumFractionDigits: 1 }) : "不明";
      const cls = o.side === "SELL" ? "color:var(--ai)" : "color:var(--shu)";
      return `<div><span style="${cls};font-weight:700;">${o.side}</span> ${o.ticker} @${pxTxt}（${o.note}）</div>`;
    }).join("");
    showConfirmPanel(el("btn-execute").closest(".card"), items, async () => {
      const done = Engine.applyOrders(state.portfolio, state.lastOrders, state.lastPrices, state.cfg);
      await Store.savePortfolio(state.portfolio);
      toast(`${done.length}件を約定しました（仮想）`);
      renderTodayHero(state.portfolio, state.lastPrices);
      renderSignals(state.lastSignals, state.portfolio);
      el("btn-execute").disabled = true;
      state.lastOrders = [];
    });
  }

  // ---------------- バックテスト画面 ----------------
  async function doRunBacktest() {
    const btn = el("btn-run-backtest");
    const log = el("backtest-log");
    log.style.display = "block"; log.textContent = "";
    btn.disabled = true; btn.textContent = "検証中...";
    el("backtest-results").style.display = "none";
    try {
      appendLog("backtest-log", "データ取得中...");
      const { data, failed } = await Store.fetchAll(state.cfg, {});
      if (failed.length) appendLog("backtest-log", `失敗: ${failed.map((f) => f.ticker).join(", ")}`, "err");
      appendLog("backtest-log", "ウォークフォワード検証を実行中...（銘柄数・期間によっては数十秒かかります）");
      await new Promise((r) => setTimeout(r, 30)); // UIに描画の猶予を与える
      let blockCount = 0;
      const res = Engine.runBacktest(data, state.cfg, (b, n) => {
        blockCount = b;
        appendLog("backtest-log", `再学習 ${b}/${n} 完了`);
      });
      appendLog("backtest-log", "完了しました。", "ok");
      renderBacktestResult(res);
    } catch (e) {
      appendLog("backtest-log", "エラー: " + e.message, "err");
      toast("バックテストでエラーが発生しました");
    } finally {
      btn.disabled = false; btn.textContent = "検証を実行";
    }
  }

  function renderBacktestResult(res) {
    el("backtest-results").style.display = "block";
    el("bt-return").textContent = pct(res.total_return);
    el("bt-mdd").textContent = pct(res.max_drawdown);
    const g = Engine.geoMonthly(res);
    el("bt-avgmonth").textContent = pct(g);
    el("bt-acc").textContent = res.metrics ? (res.metrics.accuracy * 100).toFixed(1) + "%" : "-";

    const color = res.total_return >= 0 ? "#e15241" : "#5b84c4";
    el("bt-curve").innerHTML = sparklineSvg(res.curve.map((p) => p.equity), color);

    const target = state.cfg.target_monthly_return;
    const hits = res.monthly.filter((m) => m.ret >= target).length;
    el("bt-summary-text").textContent =
      `期間 ${res.start}〜${res.end} / 取引${res.n_trades}回（決済${res.n_sells}回 / 勝率${Number.isFinite(res.win_rate) ? (res.win_rate * 100).toFixed(1) + "%" : "-"}） / `
      + `全銘柄バイ&ホールド比較 ${pct(res.bh_return)} / 目標月${(target * 100).toFixed(1)}%達成 ${hits}/${res.monthly.length}ヶ月`;

    const monthlyBox = el("bt-monthly");
    monthlyBox.innerHTML = "";
    for (const m of res.monthly) {
      const row = h("div", "month-row");
      const cls = m.ret >= 0 ? "up" : "down";
      row.innerHTML = `<span class="month-ym">${m.ym}</span>
        <span class="month-ret ${cls}">${pct(m.ret)}${m.ret >= target ? '<span class="month-badge">達成</span>' : ""}</span>`;
      monthlyBox.appendChild(row);
    }

    const tbody = document.querySelector("#bt-trades tbody");
    tbody.innerHTML = "";
    for (const t of res.trades.slice(-15).reverse()) {
      tbody.appendChild(tradeRow(t));
    }
  }

  function tradeRow(t) {
    const tr = document.createElement("tr");
    const pnlTxt = t.pnl === null || t.pnl === undefined ? "-" : (t.pnl >= 0 ? "+" : "") + Math.round(t.pnl).toLocaleString("ja-JP");
    tr.innerHTML = `<td>${t.date}</td><td class="${t.side === "BUY" ? "side-buy" : "side-sell"}">${t.side}</td>
      <td>${t.ticker}</td><td class="num">${t.shares}</td>
      <td class="num">${t.price.toLocaleString("ja-JP", { maximumFractionDigits: 1 })}</td>
      <td class="num">${pnlTxt}</td>`;
    return tr;
  }

  // ---------------- 資産画面 ----------------
  async function renderPortfolioView() {
    const pf = state.portfolio;
    const prices = {};
    for (const t of Object.keys(pf.positions)) {
      try { prices[t] = await Store.fetchLatestClose(state.cfg, t); } catch (e) { /* 無視して取得値なしで表示 */ }
    }
    const equity = Engine.portfolioEquity(pf, prices);
    el("pf-equity").textContent = yen(equity);
    const delta = equity / (pf.initial_capital || equity) - 1;
    const de = el("pf-delta");
    de.textContent = `初期資金比 ${pct(delta)}`;
    de.className = "delta num " + (delta >= 0 ? "up" : "down");

    const posBox = el("pf-positions");
    posBox.innerHTML = "";
    const tickers = Object.keys(pf.positions);
    if (!tickers.length) {
      posBox.innerHTML = '<div class="empty-note">保有銘柄なし</div>';
    } else {
      for (const t of tickers) {
        const p = pf.positions[t];
        const px = prices[t];
        const pnl = Number.isFinite(px) ? (px - p.entry) * p.shares : null;
        const row = h("div", "signal-row");
        row.innerHTML = `<div class="signal-left"><div>
            <div class="signal-ticker">${t}</div>
            <div class="signal-reason">${p.shares}株 取得@${p.entry.toLocaleString("ja-JP", { maximumFractionDigits: 1 })}</div>
          </div></div>
          <div class="signal-right">
            <div class="num" style="color:${pnl === null ? "var(--muted)" : pnl >= 0 ? "var(--shu)" : "var(--ai)"};font-weight:700;">
              ${pnl === null ? "現在値不明" : (pnl >= 0 ? "+" : "") + Math.round(pnl).toLocaleString("ja-JP") + "円"}
            </div>
            <div class="signal-price">現在値 ${Number.isFinite(px) ? px.toLocaleString("ja-JP", { maximumFractionDigits: 1 }) : "-"}</div>
          </div>`;
        posBox.appendChild(row);
      }
    }

    const tbody = document.querySelector("#pf-trades tbody");
    tbody.innerHTML = "";
    for (const t of pf.trades.slice(-20).reverse()) tbody.appendChild(tradeRow(t));
  }

  el("btn-refresh-portfolio").addEventListener("click", () => { toast("現在値を再取得しています..."); renderPortfolioView(); });

  // ---------------- 設定画面 ----------------
  function fillSettingsForm(cfg) {
    el("set-tickers").value = cfg.tickers.join(",");
    el("set-tickers-status").textContent = `現在 ${cfg.tickers.length} 銘柄（10〜30個の範囲で保存できます）`;
    el("f-min-price").value = cfg.filters.min_price;
    el("f-max-price").value = cfg.filters.max_price;
    el("f-min-vol").value = cfg.filters.min_avg_volume;
    el("f-max-volat").value = cfg.filters.max_volatility;
    el("s-buy").value = cfg.buy_threshold;
    el("s-sell").value = cfg.sell_threshold;
    el("s-sl").value = cfg.stop_loss_pct;
    el("s-tp").value = cfg.take_profit_pct;
    el("s-trail").value = cfg.trailing_stop_pct;
    el("s-maxpos").value = cfg.max_positions;
    el("s-unit").value = cfg.unit_size;
    el("s-fee").value = cfg.fee_rate;
    el("s-horizon").value = cfg.label_horizon;
    el("s-voltarget").value = cfg.vol_target;
    el("s-target").value = cfg.target_monthly_return;
    el("s-period").value = cfg.history_period;
    el("s-capital").value = cfg.initial_capital;
    el("s-proxy").value = cfg.proxy_url;
  }

  el("set-tickers").addEventListener("input", () => {
    const list = el("set-tickers").value.split(",");
    const v = Engine.validateTickers(list.filter((x) => x.trim() !== ""));
    el("set-tickers-status").textContent = v.ok ? v.msg : "⚠ " + v.msg;
  });

  async function doSaveSettings() {
    const cfg = Engine.deepCopy(state.cfg);
    const tickerList = el("set-tickers").value.split(",").filter((x) => x.trim() !== "");
    const v = Engine.validateTickers(tickerList);
    if (!v.ok) { toast("保存できません: " + v.msg); return; }
    cfg.tickers = v.cleaned;
    cfg.filters.min_price = parseFloat(el("f-min-price").value) || 0;
    cfg.filters.max_price = parseFloat(el("f-max-price").value) || 1e12;
    cfg.filters.min_avg_volume = parseFloat(el("f-min-vol").value) || 0;
    cfg.filters.max_volatility = parseFloat(el("f-max-volat").value) || 0.06;
    cfg.buy_threshold = Math.min(0.99, Math.max(0.5, parseFloat(el("s-buy").value)));
    cfg.sell_threshold = Math.min(0.5, Math.max(0.01, parseFloat(el("s-sell").value)));
    cfg.stop_loss_pct = Math.max(0, parseFloat(el("s-sl").value) || 0);
    cfg.take_profit_pct = Math.max(0, parseFloat(el("s-tp").value) || 0);
    cfg.trailing_stop_pct = Math.max(0, parseFloat(el("s-trail").value) || 0);
    cfg.max_positions = Math.max(1, parseInt(el("s-maxpos").value) || 1);
    cfg.unit_size = Math.max(1, parseInt(el("s-unit").value) || 1);
    cfg.fee_rate = Math.max(0, parseFloat(el("s-fee").value) || 0);
    cfg.label_horizon = Math.min(10, Math.max(1, parseInt(el("s-horizon").value) || 3));
    cfg.vol_target = Math.min(0.2, Math.max(0.001, parseFloat(el("s-voltarget").value) || 0.02));
    cfg.target_monthly_return = Math.max(0, parseFloat(el("s-target").value) || 0.03);
    cfg.history_period = el("s-period").value;
    cfg.initial_capital = Math.max(10000, parseFloat(el("s-capital").value) || 1000000);
    cfg.proxy_url = el("s-proxy").value.trim();

    const result = await Store.saveConfig(cfg);
    if (!result.ok) { toast("保存できません: " + result.msg); return; }
    state.cfg = cfg;
    toast("設定を保存しました");
  }

  function doResetPortfolio() {
    showConfirmPanel(el("btn-reset-portfolio").closest(".btn-row"),
      "現在の仮想ポートフォリオ（現金・保有・取引履歴）をすべて消去し、初期資金からやり直します。よろしいですか？",
      async () => {
        state.portfolio = await Store.resetPortfolio(state.cfg);
        toast("ポートフォリオを初期化しました");
        renderTodayHero(state.portfolio, {});
        el("btn-execute").disabled = true;
      });
  }

  // ---------------- 初期化 ----------------
  async function init() {
    state.cfg = await Store.loadConfig();
    state.portfolio = await Store.loadPortfolio(state.cfg);
    fillSettingsForm(state.cfg);
    renderTodayHero(state.portfolio, {});
    el("today-signals").innerHTML = '<div class="empty-note">「データ取得」を押すとシグナルが表示されます。</div>';

    el("btn-fetch").addEventListener("click", doFetch);
    el("btn-execute").addEventListener("click", doExecute);
    el("btn-run-backtest").addEventListener("click", doRunBacktest);
    el("btn-save-settings").addEventListener("click", doSaveSettings);
    el("btn-reset-portfolio").addEventListener("click", doResetPortfolio);

    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("sw.js").catch(() => {});
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
