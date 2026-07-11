# -*- coding: utf-8 -*-
"""sentiment.py — ニュース見出しのセンチメント数値化。
既定は依存ゼロの金融語彙辞書方式（日英対応）。transformers+torch がインストール
されていれば FinBERT 等のモデルに自動で切り替わる（sentiment_backend="auto"）。
FinBERT(ProsusAI/finbert) は英語ニュース向けである点に注意。
"""
import math

import numpy as np
import pandas as pd

from .logutil import get_logger

log = get_logger(__name__)

# 金融ニュース向けの簡易極性語彙（日本語＋英語）
POS_WORDS = [
    "増益", "最高益", "上方修正", "増配", "黒字", "好調", "好決算", "過去最高", "上振れ",
    "受注増", "提携", "自社株買い", "増収", "回復", "拡大", "採用", "承認", "達成",
    "beat", "beats", "raise", "raised", "record", "profit", "growth", "surge", "strong",
    "upgrade", "buyback", "dividend increase", "outperform", "expand", "approval", "wins",
]
NEG_WORDS = [
    "減益", "赤字", "下方修正", "減配", "不祥事", "提訴", "リコール", "延期", "下振れ",
    "業績悪化", "低迷", "減収", "停止", "リストラ", "撤退", "違反", "捜査", "急落",
    "miss", "misses", "cut", "loss", "lawsuit", "recall", "plunge", "weak", "downgrade",
    "fraud", "probe", "decline", "layoff", "suspend", "halt", "warning", "bankruptcy",
]


class LexiconBackend:
    """辞書方式: (陽性語数 - 陰性語数) / (陽性語数 + 陰性語数) を [-1,1] で返す。"""
    name = "lexicon（金融語彙辞書）"

    def score_texts(self, texts):
        out = []
        for text in texts:
            t = str(text).lower()
            pos = sum(t.count(w.lower()) for w in POS_WORDS)
            neg = sum(t.count(w.lower()) for w in NEG_WORDS)
            out.append(0.0 if pos + neg == 0 else (pos - neg) / (pos + neg))
        return out


class FinbertBackend:
    """transformers の感情分類モデル（既定 ProsusAI/finbert）。positive→+p, negative→-p, neutral→0。"""

    def __init__(self, model_name):
        from transformers import pipeline  # ImportError は呼び出し側で処理
        self.name = f"FinBERT系（{model_name}）"
        self._pipe = pipeline("text-classification", model=model_name, truncation=True)

    def score_texts(self, texts):
        out = []
        batch = [str(t)[:512] for t in texts]
        results = self._pipe(batch)
        for r in results:
            label = str(r.get("label", "")).lower()
            p = float(r.get("score", 0.0))
            if "pos" in label:
                out.append(p)
            elif "neg" in label:
                out.append(-p)
            else:
                out.append(0.0)
        return out


def get_backend(cfg):
    """設定に応じてバックエンドを返す。FinBERT不可なら辞書方式に自動フォールバック。"""
    choice = (cfg.get("sentiment_backend") or "auto").lower()
    if choice in ("auto", "finbert"):
        try:
            backend = FinbertBackend(cfg.get("sentiment_model", "ProsusAI/finbert"))
            log.info("感情分析バックエンド: %s", backend.name)
            return backend
        except Exception as e:
            if choice == "finbert":
                log.warning("FinBERTを初期化できないため辞書方式を使用: %s", e)
            else:
                log.info("transformers未導入のため辞書方式を使用")
    backend = LexiconBackend()
    log.info("感情分析バックエンド: %s", backend.name)
    return backend


def build_daily_sentiment(news_df, backend, echo=None):
    """(date,ticker,title)のニュース一覧 → 銘柄×営業日ごとの平均スコアと件数。"""
    if news_df is None or news_df.empty:
        return pd.DataFrame(columns=["date", "ticker", "score", "count"])
    df = news_df.copy()
    try:
        df["score"] = backend.score_texts(df["title"].tolist())
    except Exception as e:
        log.warning("感情スコア計算に失敗したため辞書方式で再試行: %s", e)
        df["score"] = LexiconBackend().score_texts(df["title"].tolist())
    daily = (
        df.groupby([pd.to_datetime(df["date"]).dt.normalize(), "ticker"])["score"]
        .agg(["mean", "count"]).reset_index()
    )
    daily.columns = ["date", "ticker", "score", "count"]
    if echo:
        echo(f"センチメント集計: {len(df)}件 → {len(daily)}銘柄日")
    log.info("センチメント集計 %d件 → %d銘柄日", len(df), len(daily))
    return daily


def attach_sentiment(all_df, daily_sent, decay_days=3.0):
    """全銘柄データセットにセンチメント特徴量を付与する。
    ニュースが無い日は「直近スコアの指数減衰」で補完し、has_news フラグで区別する。"""
    all_df = all_df.copy()
    all_df["sent"] = 0.0
    all_df["sent_ma5"] = 0.0
    all_df["news_cnt"] = 0.0
    all_df["has_news"] = 0.0
    if daily_sent is None or daily_sent.empty:
        return all_df
    key = {}
    for d, t, s, c in daily_sent[["date", "ticker", "score", "count"]].itertuples(index=False):
        key[(pd.Timestamp(d).normalize(), str(t))] = (float(s), float(c))
    k = math.exp(-1.0 / max(0.5, float(decay_days)))
    parts = []
    for ticker, sub in all_df.groupby("ticker", sort=False):
        sub = sub.sort_index().copy()
        cur = 0.0
        sents, cnts, flags = [], [], []
        for d in sub.index:
            hit = key.get((pd.Timestamp(d).normalize(), ticker))
            if hit is not None:
                cur = hit[0]
                cnts.append(float(np.log1p(hit[1])))
                flags.append(1.0)
            else:
                cur *= k
                cnts.append(0.0)
                flags.append(0.0)
            sents.append(cur)
        sub["sent"] = sents
        sub["news_cnt"] = cnts
        sub["has_news"] = flags
        sub["sent_ma5"] = pd.Series(sents, index=sub.index).rolling(5, min_periods=1).mean().values
        parts.append(sub)
    return pd.concat(parts).sort_index()
