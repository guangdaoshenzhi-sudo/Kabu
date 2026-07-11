/*
 * engine.js
 * 株式ペーパートレード・シミュレーターの「頭脳」部分。
 * DOM に依存しない純粋なロジックなので、ブラウザからも Node.js からも読み込める。
 * (Node からはユニットテスト / セルフテスト用に読み込む)
 */
(function (root, factory) {
  const mod = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = mod;
  } else {
    root.Engine = mod;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ------------------------------------------------------------
  // 定数・既定設定
  // ------------------------------------------------------------
  const MIN_TICKERS = 10;
  const MAX_TICKERS = 30;

  const FEATURES = [
    "ret1", "ret5", "ret10", "ret20",
    "sma5_ratio", "sma25_ratio",
    "rsi14", "vol20", "vol_ratio", "range_pct",
    "macd", "bb_z", "gap", "rel_ret5",
  ];

  const DEFAULT_TICKERS = [
    "7203.T", "6758.T", "9984.T", "6861.T", "8306.T",
    "9432.T", "6501.T", "7974.T", "4063.T", "6098.T",
    "8035.T", "9983.T", "4502.T", "6902.T", "7267.T",
    "8058.T", "8001.T", "4568.T", "6367.T", "9433.T",
  ];

  const DEFAULT_CONFIG = {
    tickers: DEFAULT_TICKERS.slice(),
    initial_capital: 1000000,
    max_positions: 5,
    unit_size: 1,
    fee_rate: 0.0005,
    buy_threshold: 0.55,
    sell_threshold: 0.45,
    stop_loss_pct: 0.05,
    take_profit_pct: 0.0,
    trailing_stop_pct: 0.08,
    label_horizon: 3,
    vol_target: 0.02,
    target_monthly_return: 0.03,
    history_period: "3y", // "1y"|"2y"|"3y"|"5y"
    retrain_every: 21,
    filters: {
      min_price: 0,
      max_price: 1e12,
      min_avg_volume: 100000,
      max_volatility: 0.06,
    },
    // データ取得先(CORSの都合上プロキシ経由)。設定画面から変更可能。
    proxy_url: "https://corsproxy.io/?url=",
    yahoo_base: "https://query1.finance.yahoo.com/v8/finance/chart/",
  };

  function deepCopy(o) { return JSON.parse(JSON.stringify(o)); }

  function mergeDefaults(cfg) {
    const merged = deepCopy(DEFAULT_CONFIG);
    if (cfg && typeof cfg === "object") {
      for (const k of Object.keys(cfg)) {
        if (k === "filters" && cfg[k] && typeof cfg[k] === "object") {
          Object.assign(merged.filters, cfg[k]);
        } else {
          merged[k] = cfg[k];
        }
      }
    }
    return merged;
  }

  function validateTickers(list) {
    if (!Array.isArray(list)) return { ok: false, msg: "銘柄リストの形式が不正です。", cleaned: [] };
    const seen = new Set();
    const cleaned = [];
    for (let t of list) {
      t = String(t).trim().toUpperCase();
      if (t && !seen.has(t)) { seen.add(t); cleaned.push(t); }
    }
    const n = cleaned.length;
    if (n < MIN_TICKERS) return { ok: false, msg: `銘柄は${MIN_TICKERS}個以上必要です（現在${n}個）。`, cleaned };
    if (n > MAX_TICKERS) return { ok: false, msg: `銘柄は${MAX_TICKERS}個以下にしてください（現在${n}個）。`, cleaned };
    return { ok: true, msg: `OK（${n}銘柄）`, cleaned };
  }

  // ------------------------------------------------------------
  // 数値ユーティリティ（rolling系はすべて「有効値が揃わない場所は null」方式）
  // ------------------------------------------------------------
  function isNum(x) { return typeof x === "number" && Number.isFinite(x); }

  function pctChangeN(arr, n) {
    const out = new Array(arr.length).fill(null);
    for (let i = n; i < arr.length; i++) {
      const base = arr[i - n];
      if (isNum(arr[i]) && isNum(base) && base !== 0) out[i] = arr[i] / base - 1.0;
    }
    return out;
  }

  function rollingMean(arr, n) {
    const out = new Array(arr.length).fill(null);
    let sum = 0, count = 0;
    const window = [];
    for (let i = 0; i < arr.length; i++) {
      const v = arr[i];
      window.push(v);
      if (isNum(v)) { sum += v; count++; }
      if (window.length > n) {
        const removed = window.shift();
        if (isNum(removed)) { sum -= removed; count--; }
      }
      if (window.length === n && count === n) out[i] = sum / n;
    }
    return out;
  }

  function rollingStd(arr, n) {
    const out = new Array(arr.length).fill(null);
    for (let i = 0; i < arr.length; i++) {
      if (i + 1 < n) continue;
      const win = arr.slice(i - n + 1, i + 1);
      if (win.some((v) => !isNum(v))) continue;
      const m = win.reduce((a, b) => a + b, 0) / n;
      const varr = win.reduce((a, b) => a + (b - m) * (b - m), 0) / n;
      out[i] = Math.sqrt(varr);
    }
    return out;
  }

  function ema(arr, span) {
    const out = new Array(arr.length).fill(null);
    const alpha = 2 / (span + 1);
    let prev = null;
    for (let i = 0; i < arr.length; i++) {
      const v = arr[i];
      if (!isNum(v)) { out[i] = prev; continue; }
      prev = prev === null ? v : alpha * v + (1 - alpha) * prev;
      out[i] = prev;
    }
    return out;
  }

  function rsi14(close, n) {
    n = n || 14;
    const diff = new Array(close.length).fill(null);
    for (let i = 1; i < close.length; i++) {
      if (isNum(close[i]) && isNum(close[i - 1])) diff[i] = close[i] - close[i - 1];
    }
    const up = diff.map((d) => (d === null ? null : Math.max(d, 0)));
    const down = diff.map((d) => (d === null ? null : Math.max(-d, 0)));
    const avgUp = rollingMean(up, n);
    const avgDown = rollingMean(down, n);
    const out = new Array(close.length).fill(null);
    for (let i = 0; i < close.length; i++) {
      if (avgUp[i] === null || avgDown[i] === null) continue;
      const denom = avgUp[i] + avgDown[i];
      out[i] = denom === 0 ? 50.0 : (100.0 * avgUp[i]) / denom;
    }
    return out;
  }

  // ------------------------------------------------------------
  // OHLCV データ整形
  // ------------------------------------------------------------
  // ohlcv = {dates:[ISOString...], open:[], high:[], low:[], close:[], volume:[]} (時系列昇順)
  function cleanOhlcv(ohlcv) {
    if (!ohlcv || !Array.isArray(ohlcv.close) || ohlcv.close.length === 0) {
      throw new Error("データが空です");
    }
    const n = ohlcv.close.length;
    for (const k of ["dates", "open", "high", "low", "close", "volume"]) {
      if (!Array.isArray(ohlcv[k]) || ohlcv[k].length !== n) {
        throw new Error(`データ形式が不正です（${k}）`);
      }
    }
    // 終値が欠損/0以下の行を除去
    const idx = [];
    for (let i = 0; i < n; i++) {
      if (isNum(ohlcv.close[i]) && ohlcv.close[i] > 0) idx.push(i);
    }
    const pick = (arr) => idx.map((i) => arr[i]);
    const cleaned = {
      dates: pick(ohlcv.dates), open: pick(ohlcv.open), high: pick(ohlcv.high),
      low: pick(ohlcv.low), close: pick(ohlcv.close), volume: pick(ohlcv.volume),
    };
    if (cleaned.close.length < 60) {
      throw new Error(`データが短すぎます（${cleaned.close.length}日分）`);
    }
    return cleaned;
  }

  // ------------------------------------------------------------
  // 特徴量
  // ------------------------------------------------------------
  function buildFeatures(ohlcv, horizon) {
    const c = ohlcv.close, o = ohlcv.open, h = ohlcv.high, l = ohlcv.low, v = ohlcv.volume;
    const n = c.length;
    const ret1 = pctChangeN(c, 1);
    const ret5 = pctChangeN(c, 5);
    const ret10 = pctChangeN(c, 10);
    const ret20 = pctChangeN(c, 20);
    const sma5 = rollingMean(c, 5);
    const sma25 = rollingMean(c, 25);
    const sma20 = rollingMean(c, 20);
    const std20 = rollingStd(c, 20);
    const vavg20 = rollingMean(v, 20);
    const vol20 = rollingStd(ret1, 20);
    const ema12 = ema(c, 12), ema26 = ema(c, 26);
    const macdLine = new Array(n).fill(null);
    for (let i = 0; i < n; i++) if (isNum(ema12[i]) && isNum(ema26[i])) macdLine[i] = ema12[i] - ema26[i];
    const macdSig = ema(macdLine, 9);

    const rows = [];
    for (let i = 0; i < n; i++) {
      const row = { date: ohlcv.dates[i], close: c[i], open: o[i], vavg20: vavg20[i], vol20: vol20[i] };
      row.ret1 = ret1[i];
      row.ret5 = ret5[i];
      row.ret10 = ret10[i];
      row.ret20 = ret20[i];
      row.sma5_ratio = isNum(sma5[i]) && sma5[i] !== 0 ? c[i] / sma5[i] - 1 : null;
      row.sma25_ratio = isNum(sma25[i]) && sma25[i] !== 0 ? c[i] / sma25[i] - 1 : null;
      row.rsi14 = null; // 下で一括計算して代入
      row.vol_ratio = isNum(vavg20[i]) && vavg20[i] !== 0 ? v[i] / vavg20[i] - 1 : (isNum(vavg20[i]) ? 0 : null);
      row.range_pct = isNum(c[i]) && c[i] !== 0 ? (h[i] - l[i]) / c[i] : null;
      row.macd = isNum(macdLine[i]) && isNum(macdSig[i]) && isNum(c[i]) && c[i] !== 0
        ? (macdLine[i] - macdSig[i]) / c[i] : null;
      row.bb_z = isNum(std20[i]) && std20[i] !== 0 && isNum(sma20[i]) ? (c[i] - sma20[i]) / std20[i] : null;
      row.gap = i > 0 && isNum(c[i - 1]) && c[i - 1] !== 0 ? o[i] / c[i - 1] - 1 : null;
      // 目的変数: horizon営業日先の終値が上がったか
      if (i + horizon < n && isNum(c[i]) && c[i] !== 0 && isNum(c[i + horizon])) {
        row.target_up = c[i + horizon] / c[i] - 1 > 0 ? 1 : 0;
      } else {
        row.target_up = null;
      }
      rows.push(row);
    }
    const rsiArr = rsi14(c, 14);
    for (let i = 0; i < n; i++) rows[i].rsi14 = rsiArr[i];
    return rows;
  }

  function rowIsUsable(row) {
    for (const f of FEATURES) {
      if (f === "rel_ret5") continue; // 後で計算
      if (!isNum(row[f])) return false;
    }
    return true;
  }

  // dataByTicker = { ticker: ohlcv, ... } -> 全銘柄・全日付を結合した行の配列（date昇順）
  function makeDataset(dataByTicker, horizon) {
    const allRows = [];
    for (const ticker of Object.keys(dataByTicker)) {
      let cleaned;
      try {
        cleaned = cleanOhlcv(dataByTicker[ticker]);
      } catch (e) {
        continue; // 除外
      }
      const feats = buildFeatures(cleaned, horizon);
      for (const r of feats) { r.ticker = ticker; allRows.push(r); }
    }
    if (allRows.length === 0) throw new Error("特徴量を計算できた銘柄がありません。");

    // クロスセクション特徴量: その日の全銘柄平均に対する5日リターンの差
    const byDate = new Map();
    for (const r of allRows) {
      if (!byDate.has(r.date)) byDate.set(r.date, []);
      byDate.get(r.date).push(r);
    }
    for (const [, rows] of byDate) {
      const vals = rows.map((r) => r.ret5).filter(isNum);
      const mean = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
      for (const r of rows) r.rel_ret5 = isNum(r.ret5) && mean !== null ? r.ret5 - mean : null;
    }
    allRows.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
    return allRows;
  }

  function passesFilters(row, filters) {
    if (!isNum(row.close) || !isNum(row.vavg20) || !isNum(row.vol20)) return false;
    if (row.close < filters.min_price || row.close > filters.max_price) return false;
    if (row.vavg20 < filters.min_avg_volume) return false;
    if (row.vol20 > filters.max_volatility) return false;
    return true;
  }

  // ------------------------------------------------------------
  // ロジスティック回帰（標準化＋L2正則化つきバッチ勾配降下）
  // ------------------------------------------------------------
  class LogisticModel {
    constructor() { this.mean = null; this.std = null; this.w = null; this.b = 0; }

    fit(X, y, opts) {
      opts = opts || {};
      const epochs = opts.epochs || 300;
      const lr = opts.lr || 0.3;
      const l2 = opts.l2 || 0.001;
      const n = X.length, d = X[0].length;
      this.mean = new Array(d).fill(0);
      this.std = new Array(d).fill(1);
      for (let j = 0; j < d; j++) {
        let s = 0;
        for (let i = 0; i < n; i++) s += X[i][j];
        this.mean[j] = s / n;
      }
      for (let j = 0; j < d; j++) {
        let s = 0;
        for (let i = 0; i < n; i++) s += (X[i][j] - this.mean[j]) ** 2;
        this.std[j] = Math.sqrt(s / n) || 1;
      }
      const Xs = X.map((row) => row.map((v, j) => (v - this.mean[j]) / this.std[j]));
      this.w = new Array(d).fill(0);
      this.b = 0;
      for (let e = 0; e < epochs; e++) {
        const gradW = new Array(d).fill(0);
        let gradB = 0;
        for (let i = 0; i < n; i++) {
          let z = this.b;
          for (let j = 0; j < d; j++) z += this.w[j] * Xs[i][j];
          const p = 1 / (1 + Math.exp(-z));
          const err = p - y[i];
          for (let j = 0; j < d; j++) gradW[j] += err * Xs[i][j];
          gradB += err;
        }
        for (let j = 0; j < d; j++) {
          this.w[j] -= lr * (gradW[j] / n + l2 * this.w[j]);
        }
        this.b -= lr * (gradB / n);
      }
      return this;
    }

    predictProbaOne(x) {
      let z = this.b;
      for (let j = 0; j < x.length; j++) z += this.w[j] * ((x[j] - this.mean[j]) / this.std[j]);
      return 1 / (1 + Math.exp(-z));
    }

    predictProba(X) { return X.map((x) => this.predictProbaOne(x)); }
  }

  function toFeatureVector(row) { return FEATURES.map((f) => row[f]); }

  // ------------------------------------------------------------
  // ウォークフォワード検証
  // ------------------------------------------------------------
  function computeWalkforwardProbs(allRows, cfg, onProgress) {
    const horizon = cfg.label_horizon;
    const retrainEvery = cfg.retrain_every || 21;
    const usable = allRows.filter(rowIsUsable);
    const labeled = usable.filter((r) => isNum(r.target_up));
    const dates = Array.from(new Set(usable.map((r) => r.date))).sort();
    const warmup = Math.min(250, Math.max(120, Math.floor(dates.length * 0.45)));
    if (dates.length < warmup + 40) {
      throw new Error(`データ期間が短すぎます（${dates.length}営業日）。データ期間を3y以上にしてください。`);
    }
    const dateIndex = new Map(dates.map((d, i) => [d, i]));
    const usableByDate = new Map();
    for (const r of usable) {
      if (!usableByDate.has(r.date)) usableByDate.set(r.date, []);
      usableByDate.get(r.date).push(r);
    }

    const results = [];
    let blockN = 0;
    const nBlocks = Math.ceil((dates.length - warmup) / retrainEvery);
    for (let start = warmup; start < dates.length; start += retrainEvery) {
      blockN++;
      const end = Math.min(start + retrainEvery, dates.length) - 1;
      const cutoffIdx = Math.max(0, start - horizon);
      const cutoff = dates[cutoffIdx];
      const train = labeled.filter((r) => r.date < cutoff);
      if (train.length < 300) throw new Error("学習データが不足しています。期間を延ばすか銘柄を増やしてください。");
      const X = train.map(toFeatureVector);
      const y = train.map((r) => r.target_up);
      const model = new LogisticModel().fit(X, y);
      for (let di = start; di <= end; di++) {
        const rows = usableByDate.get(dates[di]) || [];
        if (!rows.length) continue;
        const probs = model.predictProba(rows.map(toFeatureVector));
        rows.forEach((r, k) => { r.prob = probs[k]; });
        results.push(...rows);
      }
      if (onProgress) onProgress(blockN, nBlocks, train.length);
    }
    results.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
    return results;
  }

  function metricsFromProbs(rows) {
    const lab = rows.filter((r) => isNum(r.target_up) && isNum(r.prob));
    if (!lab.length) return null;
    let correct = 0, ups = 0;
    for (const r of lab) {
      const pred = r.prob >= 0.5 ? 1 : 0;
      if (pred === r.target_up) correct++;
      if (r.target_up === 1) ups++;
    }
    const acc = correct / lab.length;
    const upRate = ups / lab.length;
    const baseline = Math.max(upRate, 1 - upRate);
    const datesSorted = lab.map((r) => r.date).sort();
    return {
      accuracy: acc, baseline, n: lab.length,
      start: datesSorted[0], end: datesSorted[datesSorted.length - 1],
    };
  }

  // ------------------------------------------------------------
  // 仮想ポートフォリオ
  // ------------------------------------------------------------
  function newPortfolio(cfg) {
    return {
      cash: cfg.initial_capital, initial_capital: cfg.initial_capital,
      positions: {}, trades: [], created: new Date().toISOString(),
    };
  }

  function portfolioEquity(pf, prices) {
    let total = pf.cash;
    for (const t of Object.keys(pf.positions)) {
      const pos = pf.positions[t];
      const px = isNum(prices[t]) ? prices[t] : pos.entry;
      total += pos.shares * px;
    }
    return total;
  }

  function sizeMultFromVol(vol20, cfg) {
    const vt = cfg.vol_target || 0.02;
    if (!isNum(vol20) || vol20 <= 0 || vt <= 0) return 1.0;
    return Math.min(1.5, Math.max(0.5, vt / vol20));
  }

  function execBuy(pf, ticker, price, cfg, prices, note, when, sizeMult) {
    sizeMult = sizeMult || 1.0;
    if (pf.positions[ticker]) return null;
    if (Object.keys(pf.positions).length >= cfg.max_positions) return null;
    if (!isNum(price) || price <= 0) return null;
    const unit = Math.max(1, cfg.unit_size);
    const feeRate = cfg.fee_rate;
    const equity = portfolioEquity(pf, prices);
    const budget = Math.min(pf.cash, (equity / cfg.max_positions) * sizeMult);
    let shares = Math.floor(budget / (price * (1 + feeRate)));
    shares = Math.floor(shares / unit) * unit;
    if (shares <= 0) return null;
    const cost = shares * price;
    const fee = cost * feeRate;
    pf.cash -= cost + fee;
    pf.positions[ticker] = { shares, entry: price, peak: price };
    const trade = { date: when, side: "BUY", ticker, shares, price, fee, pnl: null, note };
    pf.trades.push(trade);
    return trade;
  }

  function execSell(pf, ticker, price, cfg, note, when) {
    const pos = pf.positions[ticker];
    if (!pos || !isNum(price) || price <= 0) return null;
    const feeRate = cfg.fee_rate;
    const proceeds = pos.shares * price;
    const fee = proceeds * feeRate;
    const pnl = (price - pos.entry) * pos.shares - fee;
    pf.cash += proceeds - fee;
    delete pf.positions[ticker];
    const trade = { date: when, side: "SELL", ticker, shares: pos.shares, price, fee, pnl, note };
    pf.trades.push(trade);
    return trade;
  }

  // ------------------------------------------------------------
  // 仮想売買シミュレーション（バックテスト共通処理）
  // ------------------------------------------------------------
  function simulate(rowsWithProb, cfg) {
    const byDate = new Map();
    for (const r of rowsWithProb) {
      if (!byDate.has(r.date)) byDate.set(r.date, []);
      byDate.get(r.date).push(r);
    }
    const dates = Array.from(byDate.keys()).sort();
    if (!dates.length) throw new Error("シミュレーション期間のデータがありません。");

    const pf = newPortfolio(cfg);
    let pending = [];
    const lastPrice = {};
    const curve = [];
    const filters = cfg.filters;
    const sl = cfg.stop_loss_pct || 0, tp = cfg.take_profit_pct || 0, tr = cfg.trailing_stop_pct || 0;

    for (const d of dates) {
      const rows = new Map(byDate.get(d).map((r) => [r.ticker, r]));

      // 1) 前日注文を今日の始値で執行（売り→買い）
      const sells = pending.filter((o) => o.side === "SELL");
      const buys = pending.filter((o) => o.side === "BUY");
      for (const o of sells) {
        const r = rows.get(o.ticker);
        if (!r) continue;
        execSell(pf, o.ticker, r.open, cfg, o.note, d);
      }
      for (const o of buys) {
        const r = rows.get(o.ticker);
        if (!r) continue;
        execBuy(pf, o.ticker, r.open, cfg, lastPrice, o.note, d, o.sizeMult);
      }
      pending = [];

      // 2) 終値・高値(トレーリング用)更新
      for (const [t, r] of rows) {
        if (isNum(r.close) && r.close > 0) {
          lastPrice[t] = r.close;
          const pos = pf.positions[t];
          if (pos) pos.peak = Math.max(pos.peak, r.close);
        }
      }

      // 3) 明日の注文を決定
      const selling = new Set();
      for (const t of Object.keys(pf.positions)) {
        const r = rows.get(t);
        if (!r) continue;
        const pos = pf.positions[t];
        const c = r.close;
        if (sl > 0 && c <= pos.entry * (1 - sl)) { pending.push({ side: "SELL", ticker: t, note: "損切り" }); selling.add(t); }
        else if (tp > 0 && c >= pos.entry * (1 + tp)) { pending.push({ side: "SELL", ticker: t, note: "利確" }); selling.add(t); }
        else if (tr > 0 && c <= pos.peak * (1 - tr)) { pending.push({ side: "SELL", ticker: t, note: "トレーリングストップ" }); selling.add(t); }
        else if (r.prob <= cfg.sell_threshold) { pending.push({ side: "SELL", ticker: t, note: `上昇確率${r.prob.toFixed(2)}` }); selling.add(t); }
      }
      const slots = cfg.max_positions - (Object.keys(pf.positions).length - selling.size);
      const cands = [];
      for (const [t, r] of rows) {
        if (pf.positions[t] || selling.has(t)) continue;
        if (r.prob >= cfg.buy_threshold && passesFilters(r, filters)) {
          cands.push({ prob: r.prob, ticker: t, sizeMult: sizeMultFromVol(r.vol20, cfg) });
        }
      }
      cands.sort((a, b) => b.prob - a.prob);
      for (const c of cands.slice(0, Math.max(0, slots))) {
        pending.push({ side: "BUY", ticker: c.ticker, note: `上昇確率${c.prob.toFixed(2)}`, sizeMult: c.sizeMult });
      }

      curve.push({ date: d, equity: portfolioEquity(pf, lastPrice) });
    }

    // ベンチマーク: 初日均等投資バイ&ホールド
    const firstRows = byDate.get(dates[0]);
    const bhRets = [];
    for (const r of firstRows) {
      const c0 = r.close, c1 = lastPrice[r.ticker];
      if (isNum(c0) && c0 > 0 && isNum(c1)) bhRets.push(c1 / c0 - 1);
    }
    const bhReturn = bhRets.length ? bhRets.reduce((a, b) => a + b, 0) / bhRets.length : NaN;

    let peak = -Infinity, mdd = 0;
    for (const p of curve) { peak = Math.max(peak, p.equity); if (peak > 0) mdd = Math.min(mdd, p.equity / peak - 1); }
    const sells = pf.trades.filter((t) => t.side === "SELL");
    const wins = sells.filter((t) => (t.pnl || 0) > 0).length;

    return {
      curve, trades: pf.trades,
      final_equity: curve[curve.length - 1].equity,
      initial_capital: cfg.initial_capital,
      total_return: curve[curve.length - 1].equity / cfg.initial_capital - 1,
      bh_return: bhReturn,
      max_drawdown: mdd,
      n_trades: pf.trades.length,
      n_sells: sells.length,
      win_rate: sells.length ? wins / sells.length : NaN,
      start: dates[0], end: dates[dates.length - 1],
      open_positions: pf.positions,
    };
  }

  function monthlyReturns(curve, initial) {
    const rows = [];
    if (!curve.length) return rows;
    let prev = initial, lastEq = initial, cur = null;
    for (const p of curve) {
      const ym = p.date.slice(0, 7); // "YYYY-MM"
      if (cur === null) cur = ym;
      if (ym !== cur) {
        rows.push({ ym: cur, ret: lastEq / prev - 1 });
        prev = lastEq; cur = ym;
      }
      lastEq = p.equity;
    }
    rows.push({ ym: cur, ret: lastEq / prev - 1 });
    return rows;
  }

  function geoMonthly(res) {
    const months = res.monthly || monthlyReturns(res.curve, res.initial_capital);
    const n = Math.max(1, months.length);
    return Math.pow(res.final_equity / res.initial_capital, 1 / n) - 1;
  }

  function runBacktest(dataByTicker, cfg, onProgress) {
    const allRows = makeDataset(dataByTicker, cfg.label_horizon);
    const rowsWithProb = computeWalkforwardProbs(allRows, cfg, onProgress);
    const res = simulate(rowsWithProb, cfg);
    res.metrics = metricsFromProbs(rowsWithProb);
    res.monthly = monthlyReturns(res.curve, res.initial_capital);
    return res;
  }

  // ------------------------------------------------------------
  // 今日のシグナル
  // ------------------------------------------------------------
  function computeLiveSignals(dataByTicker, cfg, pf) {
    const allRows = makeDataset(dataByTicker, cfg.label_horizon);
    const labeled = allRows.filter((r) => rowIsUsable(r) && isNum(r.target_up));
    if (labeled.length < 500) throw new Error("学習データが不足しています。期間を延ばすか銘柄を増やしてください。");
    const model = new LogisticModel().fit(labeled.map(toFeatureVector), labeled.map((r) => r.target_up));

    const sl = cfg.stop_loss_pct || 0, tp = cfg.take_profit_pct || 0, tr = cfg.trailing_stop_pct || 0;
    const byTicker = new Map();
    for (const r of allRows) {
      if (!byTicker.has(r.ticker)) byTicker.set(r.ticker, []);
      byTicker.get(r.ticker).push(r);
    }
    const signals = [];
    let dataDate = null;
    for (const ticker of Array.from(byTicker.keys()).sort()) {
      const rows = byTicker.get(ticker).filter(rowIsUsable);
      if (!rows.length) {
        signals.push({ ticker, signal: "対象外", reason: "データ不足", prob: NaN, close: NaN, size_mult: 1.0 });
        continue;
      }
      const row = rows[rows.length - 1];
      dataDate = dataDate === null || row.date > dataDate ? row.date : dataDate;
      const prob = model.predictProbaOne(toFeatureVector(row));
      const close = row.close;
      const held = !!pf.positions[ticker];
      let sig, reason;
      if (held) {
        const pos = pf.positions[ticker];
        pos.peak = Math.max(pos.peak || pos.entry, close);
        if (sl > 0 && close <= pos.entry * (1 - sl)) { sig = "SELL"; reason = "損切りライン到達"; }
        else if (tp > 0 && close >= pos.entry * (1 + tp)) { sig = "SELL"; reason = "利確ライン到達"; }
        else if (tr > 0 && close <= pos.peak * (1 - tr)) { sig = "SELL"; reason = "トレーリングストップ到達"; }
        else if (prob <= cfg.sell_threshold) { sig = "SELL"; reason = `上昇確率${prob.toFixed(2)}が閾値以下`; }
        else { sig = "HOLD"; reason = "保有継続"; }
      } else {
        if (!passesFilters(row, cfg.filters)) { sig = "対象外"; reason = "絞り込み条件を満たさない"; }
        else if (prob >= cfg.buy_threshold) { sig = "BUY候補"; reason = `上昇確率${prob.toFixed(2)}`; }
        else { sig = "様子見"; reason = `上昇確率${prob.toFixed(2)}`; }
      }
      signals.push({ ticker, signal: sig, reason, prob, close, size_mult: sizeMultFromVol(row.vol20, cfg) });
    }
    return { signals, dataDate };
  }

  function planOrders(signals, cfg, pf) {
    const prices = {};
    for (const s of signals) if (isNum(s.close)) prices[s.ticker] = s.close;
    const orders = [];
    for (const s of signals) {
      if (s.signal === "SELL" && pf.positions[s.ticker]) orders.push({ side: "SELL", ticker: s.ticker, note: s.reason, sizeMult: 1.0 });
    }
    const selling = new Set(orders.map((o) => o.ticker));
    const slots = cfg.max_positions - (Object.keys(pf.positions).length - selling.size);
    const buys = signals.filter((s) => s.signal === "BUY候補").sort((a, b) => b.prob - a.prob);
    for (const s of buys.slice(0, Math.max(0, slots))) {
      orders.push({ side: "BUY", ticker: s.ticker, note: s.reason, sizeMult: s.size_mult });
    }
    return { orders, prices };
  }

  function applyOrders(pf, orders, prices, cfg, when) {
    when = when || new Date().toISOString();
    const done = [];
    const ordered = orders.slice().sort((a, b) => (a.side === "SELL" ? -1 : 1) - (b.side === "SELL" ? -1 : 1));
    for (const o of ordered) {
      const price = prices[o.ticker];
      if (!isNum(price)) continue;
      const tr = o.side === "SELL"
        ? execSell(pf, o.ticker, price, cfg, o.note, when)
        : execBuy(pf, o.ticker, price, cfg, prices, o.note, when, o.sizeMult);
      if (tr) done.push(tr);
    }
    return done;
  }

  // ------------------------------------------------------------
  // 合成データ（オフライン・セルフテスト用。Yahoo風のparallel array形式）
  // ------------------------------------------------------------
  function syntheticData(nTickers, days, seed) {
    nTickers = nTickers || 12; days = days || 550; seed = seed || 7;
    let s = seed;
    function rnd() { s = (s * 1103515245 + 12345) & 0x7fffffff; return s / 0x7fffffff; }
    function gauss() {
      const u1 = Math.max(rnd(), 1e-9), u2 = rnd();
      return Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
    }
    const dates = [];
    const start = new Date();
    start.setDate(start.getDate() - days * 1.45); // 週末を考慮して営業日換算
    let d = new Date(start);
    while (dates.length < days) {
      if (d.getDay() !== 0 && d.getDay() !== 6) dates.push(d.toISOString().slice(0, 10));
      d.setDate(d.getDate() + 1);
    }
    const data = {};
    for (let ti = 0; ti < nTickers; ti++) {
      const drift = 0.0003 + gauss() * 0.0002;
      const close = new Array(days), open = new Array(days), high = new Array(days), low = new Array(days), vol = new Array(days);
      let logp = Math.log(1000);
      let prevRet = 0;
      for (let i = 0; i < days; i++) {
        const noise = gauss() * 0.015;
        const ret = drift - 0.15 * prevRet + noise;
        prevRet = ret;
        logp += ret;
        close[i] = Math.exp(logp);
        open[i] = close[i] * (1 + gauss() * 0.004);
        const spread = Math.abs(gauss() * 0.004);
        high[i] = Math.max(open[i], close[i]) * (1 + spread);
        low[i] = Math.min(open[i], close[i]) * (1 - spread);
        vol[i] = Math.floor(200000 + rnd() * 1800000);
      }
      data[`TEST${String(ti).padStart(2, "0")}.T`] = { dates, open, high, low, close, volume: vol };
    }
    return data;
  }

  return {
    MIN_TICKERS, MAX_TICKERS, FEATURES, DEFAULT_CONFIG, DEFAULT_TICKERS,
    deepCopy, mergeDefaults, validateTickers,
    cleanOhlcv, buildFeatures, makeDataset, passesFilters, rowIsUsable,
    LogisticModel, toFeatureVector,
    computeWalkforwardProbs, metricsFromProbs, runBacktest, simulate,
    monthlyReturns, geoMonthly,
    newPortfolio, portfolioEquity, execBuy, execSell, sizeMultFromVol,
    computeLiveSignals, planOrders, applyOrders,
    syntheticData,
  };
});
