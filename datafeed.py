# -*- coding: utf-8 -*-
"""datafeed.py — 外部データ取得を一手に担う層。
株価(yfinance)・ニュース(yfinance news + ユーザーCSV)・マクロ指標(FRED/yfinance)。
すべて日次キャッシュと失敗時のスキップ／警告を備える。
"""
import glob
import io
import os
import time
import urllib.request
from datetime import date

import numpy as np
import pandas as pd

from . import config as C
from .logutil import get_logger

log = get_logger(__name__)


def _import_yf():
    try:
        import yfinance as yf
        return yf
    except ImportError:
        raise RuntimeError("yfinance が見つかりません。`pip install yfinance` を実行してください。")


# ------------------------------------------------------------
# 株価
# ------------------------------------------------------------
def _clean_ohlcv(df):
    if df is None or len(df) == 0:
        raise ValueError("データが空です")
    df = df.copy()
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    need = ["Open", "High", "Low", "Close", "Volume"]
    for c in need:
        if c not in df.columns:
            raise ValueError(f"必要な列 {c} がありません")
    df = df[need].apply(pd.to_numeric, errors="coerce")
    df = df.dropna(subset=["Close"])
    df = df[df["Close"] > 0]
    if len(df) < 60:
        raise ValueError(f"データが短すぎます（{len(df)}日分）")
    return df.sort_index()


def _cache_path(name):
    safe = str(name).replace("/", "_").replace("\\", "_").replace("^", "_").replace("=", "_")
    return os.path.join(C.CACHE_DIR, safe + ".csv")


def _cache_fresh(path):
    try:
        return os.path.exists(path) and date.fromtimestamp(os.path.getmtime(path)) == date.today()
    except Exception:
        return False


def fetch_history(ticker, period="3y", force=False):
    """1銘柄の日足OHLCV。同日中はキャッシュ再利用。"""
    C.ensure_dirs()
    path = _cache_path(f"{ticker}__{period}")
    if (not force) and _cache_fresh(path):
        try:
            return _clean_ohlcv(pd.read_csv(path, index_col=0, parse_dates=True))
        except Exception as e:
            log.info("キャッシュ破損のため再取得 %s: %s", ticker, e)
    yf = _import_yf()
    last_err = None
    for attempt in range(3):
        try:
            df = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=True)
            df = _clean_ohlcv(df)
            try:
                df.to_csv(path)
            except Exception:
                pass
            log.info("株価取得OK %s (%d日)", ticker, len(df))
            return df
        except Exception as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{ticker}: 取得失敗（{last_err}）")


def fetch_all_prices(cfg, force=False, echo=print):
    data, failed = {}, []
    tickers = cfg["tickers"]
    echo(f"{len(tickers)}銘柄の株価を取得します（期間: {cfg['history_period']}）...")
    for i, t in enumerate(tickers, 1):
        try:
            df = fetch_history(t, cfg["history_period"], force=force)
            data[t] = df
            echo(f"  [{i:>2}/{len(tickers)}] {t}: OK（{len(df)}日 / 最終 {df.index[-1].date()}）")
        except Exception as e:
            failed.append(t)
            log.warning("株価取得失敗 %s: %s", t, e)
            echo(f"  [{i:>2}/{len(tickers)}] {t}: 失敗 → スキップ（{e}）")
    if not data:
        raise RuntimeError("1銘柄も取得できませんでした。通信状態と `pip install -U yfinance` を確認してください。")
    if len(data) < C.MIN_TICKERS:
        echo(f"[注意] 取得成功が{len(data)}銘柄と少なめです。結果の信頼性が下がります。")
    return data, failed


# ------------------------------------------------------------
# ニュース
#   store.csv に取得のたび蓄積していく（無料APIは直近分しか取れないため、
#   運用しながら履歴を貯める設計）。過去分は news/import/*.csv で持ち込める。
# ------------------------------------------------------------
NEWS_COLUMNS = ["date", "ticker", "title"]


def _news_store_path():
    return os.path.join(C.NEWS_DIR, "store.csv")


def load_news_store():
    path = _news_store_path()
    frames = []
    if os.path.exists(path):
        try:
            frames.append(pd.read_csv(path))
        except Exception as e:
            log.warning("ニュースストア読込失敗: %s", e)
    for f in glob.glob(os.path.join(C.NEWS_IMPORT_DIR, "*.csv")):
        try:
            df = pd.read_csv(f)
            if "text" in df.columns:  # 本文列があれば見出しに連結して感情分析に使う
                df["title"] = df["title"].astype(str) + " " + df["text"].astype(str)
            frames.append(df[[c for c in NEWS_COLUMNS if c in df.columns]])
        except Exception as e:
            log.warning("ニュースCSV読込失敗 %s: %s", f, e)
    if not frames:
        return pd.DataFrame(columns=NEWS_COLUMNS)
    df = pd.concat(frames, ignore_index=True)
    for c in NEWS_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    df = df[NEWS_COLUMNS].dropna()
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["title"] = df["title"].astype(str).str.strip()
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    df = df.dropna(subset=["date"])
    df = df[df["title"] != ""]
    df = df.drop_duplicates(subset=NEWS_COLUMNS)
    return df.sort_values("date").reset_index(drop=True)


def _save_news_store(df):
    C.ensure_dirs()
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out.to_csv(_news_store_path(), index=False)


def _parse_yf_news_items(ticker, items):
    rows = []
    for it in items or []:
        try:
            content = it.get("content", it) if isinstance(it, dict) else {}
            title = (content.get("title") if isinstance(content, dict) else None) or it.get("title")
            ts = it.get("providerPublishTime")
            if ts:
                d = pd.Timestamp(int(ts), unit="s")
            else:
                pub = None
                if isinstance(content, dict):
                    pub = content.get("pubDate") or content.get("displayTime")
                d = pd.Timestamp(pub) if pub else None
            if d is not None and getattr(d, "tzinfo", None) is not None:
                d = d.tz_localize(None)
            if title and d is not None and d == d:
                rows.append({"date": d.normalize(), "ticker": ticker, "title": str(title).strip()})
        except Exception:
            continue  # 1件の形式不正は無視
    return rows


def fetch_news(cfg, echo=print):
    """yfinance のニュース（直近分）を取得し、ストアに追記。取り込んだ新規件数を返す。"""
    yf = _import_yf()
    store = load_news_store()
    new_rows = []
    echo(f"{len(cfg['tickers'])}銘柄のニュースを取得します...")
    for i, t in enumerate(cfg["tickers"], 1):
        try:
            items = yf.Ticker(t).news
            rows = _parse_yf_news_items(t, items)
            new_rows.extend(rows)
            echo(f"  [{i:>2}/{len(cfg['tickers'])}] {t}: {len(rows)}件")
        except Exception as e:
            log.warning("ニュース取得失敗 %s: %s", t, e)
            echo(f"  [{i:>2}/{len(cfg['tickers'])}] {t}: 失敗（{e}）")
    if not new_rows:
        echo("新しいニュースはありませんでした。")
        return 0, store
    add = pd.DataFrame(new_rows)
    merged = pd.concat([store, add], ignore_index=True).drop_duplicates(subset=NEWS_COLUMNS)
    added = len(merged) - len(store)
    _save_news_store(merged)
    log.info("ニュース %d件を新規保存（累計%d件）", added, len(merged))
    echo(f"新規{added}件を保存しました（累計{len(merged)}件）。")
    return added, merged.sort_values("date").reset_index(drop=True)


# ------------------------------------------------------------
# マクロ経済指標
# ------------------------------------------------------------
def fetch_fred_series(series_id, force=False):
    """FREDのCSVエンドポイント（APIキー不要）から系列を取得。"""
    C.ensure_dirs()
    path = _cache_path(f"fred_{series_id}")
    if (not force) and _cache_fresh(path):
        try:
            s = pd.read_csv(path, index_col=0, parse_dates=True).iloc[:, 0]
            if len(s) > 0:
                return s
        except Exception:
            pass
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    df = pd.read_csv(io.StringIO(text))
    if df.shape[1] < 2:
        raise ValueError(f"FRED応答の形式が不正です: {series_id}")
    dates = pd.to_datetime(df.iloc[:, 0], errors="coerce")
    vals = pd.to_numeric(df.iloc[:, 1].replace(".", np.nan), errors="coerce")
    s = pd.Series(vals.values, index=dates, name=series_id).dropna().sort_index()
    if len(s) == 0:
        raise ValueError(f"FREDデータが空です: {series_id}")
    try:
        s.to_frame().to_csv(path)
    except Exception:
        pass
    log.info("FRED取得OK %s (%d点)", series_id, len(s))
    return s


def fetch_macro(cfg, force=False, echo=print):
    """cfg['macro'] に従い各系列を取得。失敗した系列はスキップして警告。
    戻り値: {name: {"series": pd.Series, "monthly": bool}}"""
    raw = {}
    echo("マクロ経済指標を取得します...")
    for name, spec in (cfg.get("macro") or {}).items():
        try:
            if spec.get("src") == "fred":
                s = fetch_fred_series(spec["symbol"], force=force)
            else:
                s = fetch_history(spec["symbol"], cfg.get("history_period", "3y"), force=force)["Close"]
            raw[name] = {"series": s, "monthly": bool(spec.get("monthly", False))}
            echo(f"  {name} ({spec['symbol']}): OK（{len(s)}点 / 最終 {s.index[-1].date()}）")
        except Exception as e:
            log.warning("マクロ取得失敗 %s(%s): %s", name, spec.get("symbol"), e)
            echo(f"  {name} ({spec.get('symbol')}): 失敗 → この指標なしで続行（{e}）")
    if not raw:
        echo("[注意] マクロ指標を1つも取得できませんでした。マクロ特徴量は中立値(0)で埋めます。")
    return raw
