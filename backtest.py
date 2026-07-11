# -*- coding: utf-8 -*-
"""backtest.py — 仮想売買シミュレーション・成績指標・4モデル比較。"""
import numpy as np
import pandas as pd

from .features import VARIANTS, passes_filters
from .logutil import get_logger
from .model import metrics_from_probs, walkforward_probs
from .portfolio import exec_buy, exec_sell, new_portfolio, portfolio_equity, size_mult_from_vol

log = get_logger(__name__)


def simulate(test_df, cfg):
    """prob列つきデータで仮想売買を再現。判定は当日終値、約定は翌営業日の始値。"""
    by_date = {d: g for d, g in test_df.groupby(level=0)}
    dates = sorted(by_date.keys())
    if not dates:
        raise RuntimeError("シミュレーション期間のデータがありません。")

    pf = new_portfolio(cfg)
    pending = []  # (side, ticker, note, size_mult)
    last_price = {}
    curve = []
    filters = cfg["filters"]
    sl = float(cfg.get("stop_loss_pct", 0.0))
    tp = float(cfg.get("take_profit_pct", 0.0))
    tr = float(cfg.get("trailing_stop_pct", 0.0))

    for d in dates:
        rows = {r.ticker: r for r in by_date[d].itertuples()}
        when = str(pd.Timestamp(d).date())

        for side, t, note, mult in sorted(pending, key=lambda x: 0 if x[0] == "SELL" else 1):
            r = rows.get(t)
            if r is None:
                continue
            price = float(r.open)
            if side == "SELL":
                exec_sell(pf, t, price, cfg, note, when)
            else:
                exec_buy(pf, t, price, cfg, last_price, note, when, size_mult=mult)
        pending = []

        for t, r in rows.items():
            c = float(r.close)
            if np.isfinite(c) and c > 0:
                last_price[t] = c
                pos = pf["positions"].get(t)
                if pos is not None:
                    pos["peak"] = max(float(pos.get("peak", pos["entry"])), c)

        for t, pos in list(pf["positions"].items()):
            r = rows.get(t)
            if r is None:
                continue
            c = float(r.close)
            peak = float(pos.get("peak", pos["entry"]))
            if sl > 0 and c <= pos["entry"] * (1 - sl):
                pending.append(("SELL", t, "損切り", 1.0))
            elif tp > 0 and c >= pos["entry"] * (1 + tp):
                pending.append(("SELL", t, "利確", 1.0))
            elif tr > 0 and c <= peak * (1 - tr):
                pending.append(("SELL", t, "トレーリングストップ", 1.0))
            elif float(r.prob) <= cfg["sell_threshold"]:
                pending.append(("SELL", t, f"上昇確率{float(r.prob):.2f}", 1.0))
        selling = {t for s, t, _, _ in pending if s == "SELL"}

        slots = int(cfg["max_positions"]) - (len(pf["positions"]) - len(selling))
        cands = []
        for t, r in rows.items():
            if t in pf["positions"] or t in selling:
                continue
            p = float(r.prob)
            if p >= cfg["buy_threshold"] and passes_filters(r, filters):
                cands.append((p, t, size_mult_from_vol(r.vol20, cfg)))
        cands.sort(key=lambda x: -x[0])
        for p, t, mult in cands[: max(0, slots)]:
            pending.append(("BUY", t, f"上昇確率{p:.2f}", mult))

        curve.append((pd.Timestamp(d), portfolio_equity(pf, last_price)))

    bh_rets = []
    for r in by_date[dates[0]].itertuples():
        c0 = float(r.close)
        c1 = last_price.get(r.ticker)
        if c1 is not None and np.isfinite(c0) and c0 > 0:
            bh_rets.append(c1 / c0 - 1.0)
    bh_return = float(np.mean(bh_rets)) if bh_rets else float("nan")

    equities = [e for _, e in curve]
    peak, mdd = -1e18, 0.0
    for e in equities:
        peak = max(peak, e)
        if peak > 0:
            mdd = min(mdd, e / peak - 1.0)
    sells = [t for t in pf["trades"] if t["side"] == "SELL"]
    wins = sum(1 for t in sells if (t["pnl"] or 0) > 0)

    return {
        "curve": curve,
        "trades": pf["trades"],
        "final_equity": equities[-1],
        "initial_capital": float(cfg["initial_capital"]),
        "total_return": equities[-1] / float(cfg["initial_capital"]) - 1.0,
        "bh_return": bh_return,
        "max_drawdown": mdd,
        "n_trades": len(pf["trades"]),
        "n_sells": len(sells),
        "win_rate": (wins / len(sells)) if sells else float("nan"),
        "start": str(curve[0][0].date()),
        "end": str(curve[-1][0].date()),
        "open_positions": pf["positions"],
    }


def monthly_returns(curve, initial):
    rows = []
    if not curve:
        return rows
    prev = float(initial)
    cur = None
    last_eq = float(initial)
    for d, e in curve:
        key = (d.year, d.month)
        if cur is None:
            cur = key
        if key != cur:
            rows.append((cur, last_eq / prev - 1.0))
            prev = last_eq
            cur = key
        last_eq = float(e)
    rows.append((cur, last_eq / prev - 1.0))
    return rows


def perf_metrics(res):
    """比較用の成績指標: 年率リターン・Sharpe・最大DD・勝率・売買回数など。"""
    eq = np.array([e for _, e in res["curve"]], dtype=float)
    n_days = len(eq)
    rets = np.diff(eq) / eq[:-1] if n_days > 1 else np.array([])
    sharpe = float("nan")
    if len(rets) > 1 and np.std(rets) > 0:
        sharpe = float(np.mean(rets) / np.std(rets) * np.sqrt(252))
    ann = float("nan")
    if n_days > 1:
        ann = float((res["final_equity"] / res["initial_capital"]) ** (252.0 / n_days) - 1.0)
    months = res.get("monthly") or monthly_returns(res["curve"], res["initial_capital"])
    geo_m = (res["final_equity"] / res["initial_capital"]) ** (1.0 / max(1, len(months))) - 1.0
    return {
        "final_equity": res["final_equity"],
        "total_return": res["total_return"],
        "annual_return": ann,
        "sharpe": sharpe,
        "max_drawdown": res["max_drawdown"],
        "win_rate": res["win_rate"],
        "n_trades": res["n_trades"],
        "geo_monthly": geo_m,
    }


def run_variant_comparison(all_df, cfg, echo=print):
    """従来／＋センチメント／＋マクロ／＋両方 の4モデルを同一条件で比較する。"""
    results = {}
    for key, label, feats in VARIANTS:
        echo(f"\n── {label} ──")
        try:
            def prog(b, n, ntr):
                echo(f"  再学習 {b}/{n}（学習{ntr}行）")
            probs_df = walkforward_probs(all_df, feats, cfg, on_progress=prog)
            res = simulate(probs_df, cfg)
            res["monthly"] = monthly_returns(res["curve"], res["initial_capital"])
            res["metrics"] = metrics_from_probs(probs_df)
            res["perf"] = perf_metrics(res)
            res["label"] = label
            res["features"] = feats
            results[key] = res
            m = res["metrics"]
            echo(f"  的中率 {m['accuracy']*100:.1f}% / リターン {res['total_return']*100:+.2f}%")
        except Exception as e:
            log.error("比較バックテスト失敗 (%s): %s", key, e)
            echo(f"  [エラー] このモデルはスキップします: {e}")
    if not results:
        raise RuntimeError("どのモデルも実行できませんでした。")
    return results


def print_comparison_table(results, cfg, echo=print):
    target = float(cfg.get("target_monthly_return", 0.03))
    echo("\n━━━━━━ 4モデル比較（同一期間・同一売買ルール） ━━━━━━")
    header = f"{'モデル':<14} {'最終資産':>12} {'年率ﾘﾀｰﾝ':>9} {'Sharpe':>7} {'最大DD':>8} {'勝率':>7} {'売買回数':>7} {'平均月利':>9}"
    echo(header)
    echo("─" * len(header))
    for key, label, _ in VARIANTS:
        if key not in results:
            continue
        p = results[key]["perf"]
        wr = "-" if not np.isfinite(p["win_rate"]) else f"{p['win_rate']*100:.1f}%"
        sh = "-" if not np.isfinite(p["sharpe"]) else f"{p['sharpe']:.2f}"
        echo(f"{label:<14} {p['final_equity']:>11,.0f}円 {p['annual_return']*100:>+8.1f}% {sh:>7} "
             f"{p['max_drawdown']*100:>7.1f}% {wr:>7} {p['n_trades']:>7} {p['geo_monthly']*100:>+8.2f}%")
    echo(f"（参考: 目標月利 {target*100:.1f}% / バイ&ホールド {results[list(results)[0]]['bh_return']*100:+.1f}%）")
    echo("※ 差が小さい場合は「その特徴量に有意な追加情報が無かった」という正直な結果です。")
