# -*- coding: utf-8 -*-
"""plots.py — グラフをPNGとして charts/ に保存する。
凡例・軸ラベルは文字化け（豆腐）防止のため英字表記。日本語フォントが確実に
存在する環境なら matplotlib の rcParams でフォントを指定して差し替え可能。
"""
import os

import numpy as np
import pandas as pd

from . import config as C
from .logutil import get_logger

log = get_logger(__name__)

VARIANT_LABELS_EN = {
    "baseline": "Baseline (price only)",
    "sentiment": "+ News sentiment",
    "macro": "+ Macro indicators",
    "both": "+ Both",
}
VARIANT_COLORS = {
    "baseline": "#8b90a3",
    "sentiment": "#e15241",
    "macro": "#5b84c4",
    "both": "#c9a227",
}


def _plt():
    import matplotlib
    matplotlib.use("Agg")  # 画面のない環境（サーバー/スマホ）でも保存できる
    import matplotlib.pyplot as plt
    return plt


def _save(fig, filename):
    C.ensure_dirs()
    path = os.path.join(C.CHARTS_DIR, filename)
    fig.savefig(path, dpi=140, bbox_inches="tight", facecolor="white")
    log.info("グラフ保存: %s", path)
    return path


def plot_sentiment(all_df, filename="sentiment.png"):
    """市場平均センチメントの推移とニュース件数。"""
    try:
        plt = _plt()
        daily = all_df.groupby(level=0).agg(sent=("sent", "mean"), news=("has_news", "sum"))
        fig, ax1 = plt.subplots(figsize=(9, 3.6))
        ax1.plot(daily.index, daily["sent"], color="#e15241", lw=1.2, label="Sentiment (market avg)")
        ax1.axhline(0, color="#999", lw=0.8, ls="--")
        ax1.set_ylabel("Sentiment score")
        ax1.set_ylim(-1.05, 1.05)
        ax2 = ax1.twinx()
        ax2.bar(daily.index, daily["news"], color="#5b84c4", alpha=0.35, width=1.0, label="News count")
        ax2.set_ylabel("News count / day")
        ax1.set_title("News sentiment over time")
        fig.autofmt_xdate()
        return _save(fig, filename)
    except Exception as e:
        log.warning("センチメントグラフ作成失敗: %s", e)
        return None


def plot_macro(macro_df, filename="macro.png"):
    """マクロ特徴量の推移（列ごとの小さなサブプロット）。"""
    try:
        if macro_df is None or macro_df.empty:
            return None
        plt = _plt()
        cols = [c for c in macro_df.columns if macro_df[c].notna().any()]
        if not cols:
            return None
        n = len(cols)
        rows = (n + 1) // 2
        fig, axes = plt.subplots(rows, 2, figsize=(10, 2.2 * rows), squeeze=False)
        for i, c in enumerate(cols):
            ax = axes[i // 2][i % 2]
            ax.plot(macro_df.index, macro_df[c], color="#35455f", lw=1.0)
            ax.set_title(c, fontsize=9)
            ax.tick_params(labelsize=7)
        for j in range(n, rows * 2):
            axes[j // 2][j % 2].axis("off")
        fig.suptitle("Macro indicators (transformed features)", fontsize=11)
        fig.tight_layout()
        return _save(fig, filename)
    except Exception as e:
        log.warning("マクログラフ作成失敗: %s", e)
        return None


def plot_equity_comparison(results, filename="comparison.png"):
    """4モデルの累積資産曲線を重ねて比較（初期資金=100で正規化）。"""
    try:
        plt = _plt()
        fig, ax = plt.subplots(figsize=(9, 4.2))
        for key, res in results.items():
            curve = res["curve"]
            init = res["initial_capital"]
            xs = [d for d, _ in curve]
            ys = [e / init * 100 for _, e in curve]
            ax.plot(xs, ys, lw=1.4, label=VARIANT_LABELS_EN.get(key, key),
                    color=VARIANT_COLORS.get(key, None))
        ax.axhline(100, color="#999", lw=0.8, ls="--")
        ax.set_ylabel("Equity (start = 100)")
        ax.set_title("Cumulative equity: model comparison")
        ax.legend(fontsize=8)
        fig.autofmt_xdate()
        return _save(fig, filename)
    except Exception as e:
        log.warning("比較グラフ作成失敗: %s", e)
        return None


def plot_feature_importance(pairs, filename="feature_importance.png", top=20):
    """Permutation Importance の横棒グラフ。"""
    try:
        plt = _plt()
        pairs = list(pairs)[:top][::-1]
        names = [p[0] for p in pairs]
        vals = [p[1] for p in pairs]
        colors = ["#e15241" if n in ("sent", "sent_ma5", "news_cnt", "has_news")
                  else "#5b84c4" if n in ("usdjpy_r5", "usdjpy_r20", "wti_r20", "vix", "vix_chg5", "rate", "rate_chg20", "cpi_yoy")
                  else "#8b90a3" for n in names]
        fig, ax = plt.subplots(figsize=(7, max(3, 0.28 * len(pairs))))
        ax.barh(names, vals, color=colors)
        ax.axvline(0, color="#999", lw=0.8)
        ax.set_title("Permutation feature importance (accuracy drop)")
        ax.tick_params(labelsize=8)
        fig.tight_layout()
        return _save(fig, filename)
    except Exception as e:
        log.warning("重要度グラフ作成失敗: %s", e)
        return None
