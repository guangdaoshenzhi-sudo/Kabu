/*
 * data.js
 * ・IndexedDB を使った設定/ポートフォリオ/株価キャッシュの永続化
 * ・Yahoo Finance の chart API を CORS プロキシ経由で取得
 * ブラウザ専用（Node からは読み込まない）
 */
(function () {
  "use strict";

  const DB_NAME = "stocksim_v2";
  const DB_VERSION = 1;
  const STORES = ["kv", "cache"]; // kv: 設定/ポートフォリオ, cache: 株価データ

  function openDb() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        for (const s of STORES) if (!db.objectStoreNames.contains(s)) db.createObjectStore(s);
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async function idbGet(store, key) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(store, "readonly");
      const req = tx.objectStore(store).get(key);
      req.onsuccess = () => resolve(req.result === undefined ? null : req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async function idbSet(store, key, value) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(store, "readwrite");
      tx.objectStore(store).put(value, key);
      tx.oncomplete = () => resolve(true);
      tx.onerror = () => reject(tx.error);
    });
  }

  async function idbDelete(store, key) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(store, "readwrite");
      tx.objectStore(store).delete(key);
      tx.oncomplete = () => resolve(true);
      tx.onerror = () => reject(tx.error);
    });
  }

  // ------------------------------------------------------------
  // 設定・ポートフォリオの永続化
  // ------------------------------------------------------------
  async function loadConfig() {
    const raw = await idbGet("kv", "config");
    const cfg = window.Engine.mergeDefaults(raw || {});
    const v = window.Engine.validateTickers(cfg.tickers);
    cfg.tickers = v.ok ? v.cleaned : window.Engine.DEFAULT_TICKERS.slice();
    return cfg;
  }
  async function saveConfig(cfg) {
    const v = window.Engine.validateTickers(cfg.tickers);
    if (!v.ok) return { ok: false, msg: v.msg };
    cfg.tickers = v.cleaned;
    await idbSet("kv", "config", cfg);
    return { ok: true };
  }

  async function loadPortfolio(cfg) {
    const pf = await idbGet("kv", "portfolio");
    if (pf && pf.cash !== undefined && pf.positions && pf.trades) return pf;
    return window.Engine.newPortfolio(cfg);
  }
  async function savePortfolio(pf) { await idbSet("kv", "portfolio", pf); }
  async function resetPortfolio(cfg) {
    const pf = window.Engine.newPortfolio(cfg);
    await savePortfolio(pf);
    return pf;
  }

  // ------------------------------------------------------------
  // 株価データ取得（Yahoo Finance chart API を CORS プロキシ経由で）
  // ------------------------------------------------------------
  const PERIOD_TO_RANGE = { "1y": "1y", "2y": "2y", "3y": "3y", "5y": "5y", "10y": "10y" };

  function buildYahooUrl(cfg, ticker) {
    const range = PERIOD_TO_RANGE[cfg.history_period] || "3y";
    const target = `${cfg.yahoo_base}${encodeURIComponent(ticker)}?range=${range}&interval=1d`;
    if (!cfg.proxy_url) return target;
    // corsproxy.io 系("...?url=")、codetabs 系("...?quest=")の双方に対応
    if (cfg.proxy_url.includes("quest=") || cfg.proxy_url.endsWith("quest=")) {
      return cfg.proxy_url + encodeURIComponent(target);
    }
    return cfg.proxy_url + encodeURIComponent(target);
  }

  function parseYahooChart(json, ticker) {
    const res = json && json.chart && json.chart.result && json.chart.result[0];
    if (!res) {
      const errMsg = json && json.chart && json.chart.error && json.chart.error.description;
      throw new Error(errMsg || "データ形式が不正です");
    }
    const ts = res.timestamp;
    const q = res.indicators && res.indicators.quote && res.indicators.quote[0];
    if (!ts || !q) throw new Error("価格データがありません");
    const dates = ts.map((t) => new Date(t * 1000).toISOString().slice(0, 10));
    return { dates, open: q.open, high: q.high, low: q.low, close: q.close, volume: q.volume };
  }

  async function fetchOne(cfg, ticker, { signal } = {}) {
    const url = buildYahooUrl(cfg, ticker);
    let lastErr = null;
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const resp = await fetch(url, { signal });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const json = await resp.json();
        const ohlcv = parseYahooChart(json, ticker);
        return window.Engine.cleanOhlcv(ohlcv);
      } catch (e) {
        lastErr = e;
        await new Promise((r) => setTimeout(r, 600 * (attempt + 1)));
      }
    }
    throw new Error(`${ticker}: 取得失敗（${lastErr && lastErr.message}）`);
  }

  function todayStr() { return new Date().toISOString().slice(0, 10); }

  async function fetchAll(cfg, { force = false, onProgress } = {}) {
    const data = {}; const failed = [];
    const tickers = cfg.tickers;
    for (let i = 0; i < tickers.length; i++) {
      const t = tickers[i];
      try {
        if (!force) {
          const cached = await idbGet("cache", t + "__" + cfg.history_period);
          if (cached && cached.fetchedDate === todayStr()) {
            data[t] = cached.ohlcv;
            if (onProgress) onProgress(i + 1, tickers.length, t, "cache");
            continue;
          }
        }
        const ohlcv = await fetchOne(cfg, t);
        data[t] = ohlcv;
        await idbSet("cache", t + "__" + cfg.history_period, { ohlcv, fetchedDate: todayStr() });
        if (onProgress) onProgress(i + 1, tickers.length, t, "ok");
      } catch (e) {
        failed.push({ ticker: t, error: e.message });
        if (onProgress) onProgress(i + 1, tickers.length, t, "fail", e.message);
      }
    }
    if (Object.keys(data).length === 0) {
      throw new Error("1銘柄も取得できませんでした。通信状態、またはCORSプロキシの設定を確認してください。");
    }
    return { data, failed };
  }

  async function fetchLatestClose(cfg, ticker) {
    const cached = await idbGet("cache", ticker + "__" + cfg.history_period);
    if (cached && cached.ohlcv) {
      const c = cached.ohlcv.close;
      return c[c.length - 1];
    }
    const ohlcv = await fetchOne(cfg, ticker);
    return ohlcv.close[ohlcv.close.length - 1];
  }

  window.Store = {
    loadConfig, saveConfig, loadPortfolio, savePortfolio, resetPortfolio,
    fetchAll, fetchOne, fetchLatestClose, idbGet, idbSet, idbDelete,
  };
})();
