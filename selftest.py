# -*- coding: utf-8 -*-
"""selftest.py — 通信なしで全機能を検証する自己診断。
合成の株価・ニュース・マクロ指標を生成し、パイプライン全体（特徴量→学習→
4モデル比較→グラフ→重要度→シグナル→仮想売買）を通す。
"""
import os
import traceback

import numpy as np
import pandas as pd

from . import config as C
from .backtest import perf_metrics, print_comparison_table, run_variant_comparison, simulate
from .features import (MACRO_FEATURES, PRICE_FEATURES, SENT_FEATURES,
                       build_full_dataset, macro_feature_frame)
from .logutil import get_logger
from .model import feature_importance, model_name, walkforward_probs
from .portfolio import (apply_orders, compute_live_signals, new_portfolio,
                        plan_orders, portfolio_equity)
from .sentiment import LexiconBackend, build_daily_sentiment

log = get_logger(__name__)


def synthetic_prices(n_tickers=12, days=550, seed=7):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=days)
    days = len(idx)  # 終端が週末の場合に1要素短くなることがあるため実長に合わせる
    data = {}
    for i in range(n_tickers):
        drift = rng.normal(0.0003, 0.0004)
        noise = rng.normal(0, 0.015, days)
        rets = np.zeros(days)
        for t in range(1, days):
            rets[t] = drift - 0.15 * rets[t - 1] + noise[t]
        close = 1000.0 * np.exp(np.cumsum(rets))
        open_ = close * (1 + rng.normal(0, 0.004, days))
        spread = np.abs(rng.normal(0, 0.004, days))
        high = np.maximum(open_, close) * (1 + spread)
        low = np.minimum(open_, close) * (1 - spread)
        vol = rng.integers(200000, 2000000, days).astype(float)
        data[f"TEST{i:02d}.T"] = pd.DataFrame(
            {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol}, index=idx)
    return data


def synthetic_news(data, seed=11):
    rng = np.random.default_rng(seed)
    pos = ["増益で過去最高益を更新", "上方修正を発表 record profit beat", "増配と自社株買いを発表 strong growth"]
    neg = ["減益で下方修正", "赤字転落 big loss", "リコール問題で提訴 lawsuit and recall"]
    rows = []
    for t, df in data.items():
        for d in df.index:
            if rng.random() < 0.05:
                pool = pos if rng.random() < 0.5 else neg
                rows.append({"date": pd.Timestamp(d).normalize(), "ticker": t,
                             "title": pool[int(rng.integers(0, len(pool)))]})
    return pd.DataFrame(rows)


def synthetic_macro(data, seed=13):
    rng = np.random.default_rng(seed)
    idx = next(iter(data.values())).index
    n = len(idx)
    def walk(start, sigma):
        return pd.Series(start * np.exp(np.cumsum(rng.normal(0, sigma, n))), index=idx)
    monthly_idx = pd.date_range(idx[0], idx[-1], freq="MS")
    cpi = pd.Series(100 * (1.02 ** (np.arange(len(monthly_idx)) / 12.0)), index=monthly_idx)
    return {
        "usdjpy": {"series": walk(150, 0.004), "monthly": False},
        "wti": {"series": walk(75, 0.015), "monthly": False},
        "vix": {"series": (walk(18, 0.03)).clip(8, 90), "monthly": False},
        "policy_rate": {"series": pd.Series(np.linspace(0.1, 0.5, n), index=idx), "monthly": False},
        "cpi": {"series": cpi, "monthly": True},
    }


def run_selftest():
    print("═════ セルフテスト開始（通信なし・合成データで全機能を検証）═════")
    print(f"  使用モデル: {model_name()}")
    try:
        cfg = C._merge_defaults({})
        cfg["retrain_every"] = 63  # テスト高速化

        # 1) 設定・銘柄数の検証
        ok, _, _ = C.validate_tickers(cfg["tickers"])
        ok9 = C.validate_tickers([f"T{i}" for i in range(9)])[0]
        ok31 = C.validate_tickers([f"T{i}" for i in range(31)])[0]
        assert ok and (not ok9) and (not ok31), "銘柄数10〜30の検証に失敗"
        print("  ✔ 1/8 設定と銘柄数(10〜30)の検証")

        # 2) センチメント: 辞書方式の符号が正しい
        be = LexiconBackend()
        sp, sn = be.score_texts(["増益で最高益 上方修正", "赤字転落で下方修正 loss"])
        assert sp > 0 > sn, f"辞書スコアの符号が不正: {sp}, {sn}"
        prices = synthetic_prices()
        news = synthetic_news(prices)
        daily = build_daily_sentiment(news, be)
        assert len(daily) > 0 and daily["score"].abs().max() <= 1.0
        print(f"  ✔ 2/8 センチメント数値化（{len(news)}件 → {len(daily)}銘柄日, 正:{sp:+.2f}/負:{sn:+.2f}）")

        # 3) マクロ特徴量（月次CPIの前方補完・公表ラグシフト含む）
        macro_raw = synthetic_macro(prices)
        macro_df = macro_feature_frame(macro_raw)
        for c in MACRO_FEATURES:
            assert c in macro_df.columns, f"マクロ特徴量 {c} が無い"
        assert macro_df["cpi_yoy"].notna().sum() > 0, "CPI YoYが計算されていない"
        print(f"  ✔ 3/8 マクロ特徴量変換（{len(macro_df.columns)}列 × {len(macro_df)}日）")

        # 4) 統合データセット
        all_df, macro_df2 = build_full_dataset(prices, cfg, daily_sent=daily, macro_raw=macro_raw)
        for c in PRICE_FEATURES + SENT_FEATURES + MACRO_FEATURES:
            assert c in all_df.columns, f"特徴量 {c} が無い"
        assert float(all_df["sent"].abs().max()) > 0, "センチメントが結合されていない"
        assert int(all_df["has_news"].sum()) > 0, "ニュース日フラグが立っていない"
        print(f"  ✔ 4/8 統合データセット構築（{len(all_df)}行 × 特徴量{len(PRICE_FEATURES+SENT_FEATURES+MACRO_FEATURES)}）")

        # 5) ウォークフォワード＋シミュレーション（単体）
        probs_df = walkforward_probs(all_df, PRICE_FEATURES, cfg)
        res = simulate(probs_df, cfg)
        p = perf_metrics(res)
        assert np.isfinite(res["final_equity"]) and res["final_equity"] > 0
        assert np.isfinite(p["annual_return"])
        print(f"  ✔ 5/8 ウォークフォワード検証（{res['start']}〜{res['end']} / Sharpe {p['sharpe']:.2f}）")

        # 6) 4モデル比較
        results = run_variant_comparison(all_df, cfg, echo=lambda *a: None)
        assert set(results.keys()) == {"baseline", "sentiment", "macro", "both"}, "4モデル全て実行されるはず"
        print_comparison_table(results, cfg)
        print("  ✔ 6/8 4モデル比較バックテスト")

        # 7) グラフ4種＋特徴量重要度
        from . import plots
        paths = [
            plots.plot_sentiment(all_df, "selftest_sentiment.png"),
            plots.plot_macro(macro_df2, "selftest_macro.png"),
            plots.plot_equity_comparison(results, "selftest_comparison.png"),
        ]
        imp = feature_importance(all_df, PRICE_FEATURES + SENT_FEATURES + MACRO_FEATURES, cfg,
                                 max_rows=1200, n_repeats=2)
        assert len(imp) > 0
        paths.append(plots.plot_feature_importance(imp, "selftest_importance.png"))
        for pth in paths:
            assert pth and os.path.exists(pth) and os.path.getsize(pth) > 0, f"グラフ未生成: {pth}"
        print(f"  ✔ 7/8 グラフ出力（{len(paths)}枚 → {C.CHARTS_DIR}/）＋特徴量重要度（1位: {imp[0][0]}）")

        # 8) 今日のシグナル → 仮想売買
        pf = new_portfolio(cfg)
        feats = PRICE_FEATURES + SENT_FEATURES + MACRO_FEATURES
        signals, data_date = compute_live_signals(all_df, feats, cfg, pf)
        assert len(signals) == len(prices)
        orders, prices_map = plan_orders(signals, cfg, pf)
        done = apply_orders(pf, orders, prices_map, cfg, when="selftest")
        assert pf["cash"] >= -1e-6
        eq = portfolio_equity(pf, prices_map)
        assert np.isfinite(eq) and eq > 0
        print(f"  ✔ 8/8 シグナル生成と仮想売買（注文{len(orders)}件 / 約定{len(done)}件）")

        print("\n✅ セルフテスト完了：全機能がエラーなく動作しました。")
        print("   実データの取得（yfinance/FRED）だけは通信が必要なため、メニュー1でお試しください。")
        return True
    except Exception:
        print("❌ セルフテスト失敗。以下のエラー内容を確認してください。")
        traceback.print_exc()
        return False
