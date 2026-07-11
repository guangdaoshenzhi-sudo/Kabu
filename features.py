# -*- coding: utf-8 -*-
"""features.py — 特徴量の定義と生成。価格系／センチメント系／マクロ系を分離して管理。"""
import numpy as np
import pandas as pd

from .logutil import get_logger
from .sentiment import attach_sentiment

log = get_logger(__name__)

PRICE_FEATURES = [
    "ret1", "ret5", "ret10", "ret20",
    "sma5_ratio", "sma25_ratio",
    "rsi14", "vol20", "vol_ratio", "range_pct",
    "macd", "bb_z", "gap", "rel_ret5",
]
SENT_FEATURES = ["sent", "sent_ma5", "news_cnt", "has_news"]
MACRO_FEATURES = ["usdjpy_r5", "usdjpy_r20", "wti_r20", "vix", "vix_chg5", "rate", "rate_chg20", "cpi_yoy"]

# バックテスト比較に使う4モデル（key, 表示名, 特徴量リスト）
VARIANTS = [
    ("baseline", "従来（価格系のみ）", PRICE_FEATURES),
    ("sentiment", "＋ニュースセンチメント", PRICE_FEATURES + SENT_FEATURES),
    ("macro", "＋マクロ経済指標", PRICE_FEATURES + MACRO_FEATURES),
    ("both", "＋両方", PRICE_FEATURES + SENT_FEATURES + MACRO_FEATURES),
]


def active_features(cfg):
    """今日のシグナル用: 設定のON/OFFに応じた特徴量リスト。"""
    feats = list(PRICE_FEATURES)
    if cfg.get("use_sentiment"):
        feats += SENT_FEATURES
    if cfg.get("use_macro"):
        feats += MACRO_FEATURES
    return feats


def pct_change_n(series, n):
    return series / series.shift(n) - 1.0


def rsi14(close, n=14):
    diff = close.diff()
    up = diff.clip(lower=0.0)
    down = (-diff).clip(lower=0.0)
    avg_up = up.rolling(n).mean()
    avg_down = down.rolling(n).mean()
    denom = avg_up + avg_down
    out = 100.0 * avg_up / denom
    out[denom == 0] = 50.0
    return out


def build_price_features(df, horizon=3):
    """1銘柄のOHLCVから価格系特徴量と目的変数を作る（v2と同一ロジック）。"""
    c, v = df["Close"], df["Volume"]
    out = pd.DataFrame(index=df.index)
    out["ret1"] = pct_change_n(c, 1)
    out["ret5"] = pct_change_n(c, 5)
    out["ret10"] = pct_change_n(c, 10)
    out["ret20"] = pct_change_n(c, 20)
    sma5 = c.rolling(5).mean()
    sma25 = c.rolling(25).mean()
    out["sma5_ratio"] = c / sma5 - 1.0
    out["sma25_ratio"] = c / sma25 - 1.0
    out["rsi14"] = rsi14(c)
    out["vol20"] = out["ret1"].rolling(20).std()
    vavg = v.rolling(20).mean()
    out["vavg20"] = vavg
    ratio = v / vavg.replace(0, np.nan) - 1.0
    out["vol_ratio"] = ratio.fillna(0.0)
    out["range_pct"] = (df["High"] - df["Low"]) / c
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    macd_sig = macd_line.ewm(span=9, adjust=False).mean()
    out["macd"] = (macd_line - macd_sig) / c
    sma20 = c.rolling(20).mean()
    std20 = c.rolling(20).std()
    out["bb_z"] = (c - sma20) / std20.replace(0, np.nan)
    out["gap"] = df["Open"] / c.shift(1) - 1.0
    out["open"] = df["Open"]
    out["close"] = c
    fwd = c.shift(-horizon) / c - 1.0
    out["target_up"] = np.where(fwd > 0, 1.0, 0.0)
    out.loc[fwd.isna(), "target_up"] = np.nan
    return out


def make_price_dataset(data, horizon=3):
    frames = []
    for t, df in data.items():
        try:
            f = build_price_features(df, horizon=horizon)
            f["ticker"] = t
            frames.append(f)
        except Exception as e:
            log.warning("特徴量計算失敗のため除外 %s: %s", t, e)
    if not frames:
        raise RuntimeError("特徴量を計算できた銘柄がありません。")
    all_df = pd.concat(frames).sort_index()
    all_df["rel_ret5"] = all_df["ret5"] - all_df.groupby(level=0)["ret5"].transform("mean")
    return all_df


# ------------------------------------------------------------
# マクロ特徴量
# ------------------------------------------------------------
def macro_feature_frame(raw):
    """取得済み系列(raw)を特徴量に変換した日次DataFrameへ。
    ・為替/原油は変化率（水準は非定常のため）
    ・VIX/政策金利は水準＋変化
    ・CPIは前年比(YoY)を「1ヶ月シフト」して使用（公表ラグによる未来情報の混入を防ぐ）
    """
    cols = {}
    if "usdjpy" in raw:
        s = raw["usdjpy"]["series"]
        cols["usdjpy_r5"] = pct_change_n(s, 5)
        cols["usdjpy_r20"] = pct_change_n(s, 20)
    if "wti" in raw:
        cols["wti_r20"] = pct_change_n(raw["wti"]["series"], 20)
    if "vix" in raw:
        s = raw["vix"]["series"]
        cols["vix"] = s
        cols["vix_chg5"] = s.diff(5)
    if "policy_rate" in raw:
        s = raw["policy_rate"]["series"]
        if raw["policy_rate"].get("monthly"):
            s = s.shift(1)  # 月次系列は1期ずらして公表ラグを考慮
        cols["rate"] = s
        cols["rate_chg20"] = s.diff(20 if not raw["policy_rate"].get("monthly") else 1)
    if "cpi" in raw:
        m = raw["cpi"]["series"]
        yoy = (m / m.shift(12) - 1.0).shift(1)  # 前年比を1ヶ月遅らせて使用
        cols["cpi_yoy"] = yoy
    if not cols:
        return pd.DataFrame()
    df = pd.DataFrame(cols).sort_index()
    df = df.ffill()  # 月次→日次は前方補完（過去の値のみ参照するので安全）
    return df


def attach_macro(all_df, macro_df):
    """全銘柄データセットにマクロ特徴量を日付で結合。取得できなかった列は中立値0で埋める。"""
    all_df = all_df.copy()
    dates = pd.DatetimeIndex(sorted(pd.unique(all_df.index)))
    if macro_df is None or macro_df.empty:
        for c in MACRO_FEATURES:
            all_df[c] = 0.0
        log.info("マクロ特徴量なし: 全て0埋め")
        return all_df
    aligned = macro_df.reindex(macro_df.index.union(dates)).ffill().reindex(dates)
    missing = []
    for c in MACRO_FEATURES:
        if c in aligned.columns:
            col = aligned[c]
            fill = float(col.median()) if col.notna().any() else 0.0
            mapper = col.fillna(fill)
            vals = pd.to_numeric(all_df.index.map(mapper), errors="coerce")
            all_df[c] = pd.Series(vals, index=all_df.index).fillna(fill)
        else:
            all_df[c] = 0.0
            missing.append(c)
    if missing:
        log.warning("取得できなかったマクロ特徴量を0埋め: %s", ", ".join(missing))
    return all_df


def build_full_dataset(price_data, cfg, daily_sent=None, macro_raw=None):
    """価格＋センチメント＋マクロの全特徴量を持つデータセットを構築する。"""
    all_df = make_price_dataset(price_data, horizon=int(cfg["label_horizon"]))
    all_df = attach_sentiment(all_df, daily_sent, decay_days=cfg.get("sentiment_decay_days", 3.0))
    macro_df = macro_feature_frame(macro_raw or {})
    all_df = attach_macro(all_df, macro_df)
    return all_df, macro_df


def passes_filters(row, filters):
    try:
        close = float(row.close)
        vavg = float(row.vavg20)
        vol = float(row.vol20)
    except Exception:
        return False
    if not (np.isfinite(close) and np.isfinite(vavg) and np.isfinite(vol)):
        return False
    if close < filters["min_price"] or close > filters["max_price"]:
        return False
    if vavg < filters["min_avg_volume"]:
        return False
    if vol > filters["max_volatility"]:
        return False
    return True
