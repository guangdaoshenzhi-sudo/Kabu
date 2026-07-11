# -*- coding: utf-8 -*-
"""portfolio.py — 仮想ポートフォリオ・売買執行・今日のシグナル。"""
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd

from . import config as C
from .logutil import get_logger

log = get_logger(__name__)


def new_portfolio(cfg):
    return {
        "cash": float(cfg["initial_capital"]),
        "initial_capital": float(cfg["initial_capital"]),
        "positions": {},
        "trades": [],
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def portfolio_equity(pf, prices):
    total = float(pf["cash"])
    for t, p in pf["positions"].items():
        px = prices.get(t, p["entry"])
        total += p["shares"] * float(px)
    return total


def size_mult_from_vol(vol20, cfg):
    vt = float(cfg.get("vol_target", 0.02))
    try:
        v = float(vol20)
    except Exception:
        return 1.0
    if not np.isfinite(v) or v <= 0 or vt <= 0:
        return 1.0
    return float(min(1.5, max(0.5, vt / v)))


def exec_buy(pf, ticker, price, cfg, prices, note, when, size_mult=1.0):
    if ticker in pf["positions"]:
        return None
    if len(pf["positions"]) >= int(cfg["max_positions"]):
        return None
    if not np.isfinite(price) or price <= 0:
        return None
    unit = max(1, int(cfg["unit_size"]))
    fee_rate = float(cfg["fee_rate"])
    equity = portfolio_equity(pf, prices)
    budget = min(pf["cash"], equity / int(cfg["max_positions"]) * float(size_mult))
    shares = int(budget // (price * (1 + fee_rate)))
    shares = (shares // unit) * unit
    if shares <= 0:
        return None
    cost = shares * price
    fee = cost * fee_rate
    pf["cash"] -= cost + fee
    pf["positions"][ticker] = {"shares": int(shares), "entry": float(price), "peak": float(price)}
    trade = {"date": when, "side": "BUY", "ticker": ticker, "shares": int(shares),
             "price": round(float(price), 2), "fee": round(fee, 2), "pnl": None, "note": note}
    pf["trades"].append(trade)
    return trade


def exec_sell(pf, ticker, price, cfg, note, when):
    pos = pf["positions"].get(ticker)
    if pos is None or not np.isfinite(price) or price <= 0:
        return None
    fee_rate = float(cfg["fee_rate"])
    shares = int(pos["shares"])
    proceeds = shares * price
    fee = proceeds * fee_rate
    pnl = (price - pos["entry"]) * shares - fee
    pf["cash"] += proceeds - fee
    del pf["positions"][ticker]
    trade = {"date": when, "side": "SELL", "ticker": ticker, "shares": shares,
             "price": round(float(price), 2), "fee": round(fee, 2),
             "pnl": round(float(pnl), 2), "note": note}
    pf["trades"].append(trade)
    return trade


def load_portfolio(cfg, path=None):
    path = path or C.PORTFOLIO_PATH
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                pf = json.load(f)
            for key in ("cash", "positions", "trades"):
                if key not in pf:
                    raise ValueError("形式不正")
            return pf
        except Exception as e:
            log.warning("ポートフォリオ読込失敗のため新規作成: %s", e)
    return new_portfolio(cfg)


def save_portfolio(pf, path=None):
    path = path or C.PORTFOLIO_PATH
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(pf, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        log.error("ポートフォリオ保存失敗: %s", e)
        print(f"ポートフォリオ保存に失敗しました: {e}")
        return False


# ------------------------------------------------------------
# 今日のシグナル
# ------------------------------------------------------------
def compute_live_signals(all_df, features, cfg, pf):
    """全履歴で学習し、各銘柄の最新日の特徴量から上昇確率とシグナルを出す。"""
    from .features import passes_filters
    from .model import make_model, predict_up_proba

    labeled = all_df.dropna(subset=list(features) + ["target_up"])
    if len(labeled) < 500:
        raise RuntimeError("学習データが不足しています。期間を延ばすか銘柄を増やしてください。")
    model, mname = make_model(cfg)
    model.fit(labeled[features], labeled["target_up"].astype(int))
    log.info("ライブ用モデル学習 (%s, %d行, 特徴量%d)", mname, len(labeled), len(features))

    sl = float(cfg.get("stop_loss_pct", 0.0))
    tp = float(cfg.get("take_profit_pct", 0.0))
    tr = float(cfg.get("trailing_stop_pct", 0.0))

    signals = []
    data_date = None
    for ticker in sorted(all_df["ticker"].unique()):
        sub = all_df[all_df["ticker"] == ticker].dropna(subset=features)
        if sub.empty:
            signals.append({"ticker": ticker, "signal": "対象外", "reason": "データ不足",
                            "prob": float("nan"), "close": float("nan"), "size_mult": 1.0})
            continue
        row = sub.iloc[-1]
        d = sub.index[-1]
        data_date = d if data_date is None else max(data_date, d)
        X = row[list(features)].astype(float).to_frame().T
        prob = float(predict_up_proba(model, X)[0])
        close = float(row["close"])
        held = ticker in pf["positions"]

        if held:
            pos = pf["positions"][ticker]
            entry = float(pos["entry"])
            pos["peak"] = max(float(pos.get("peak", entry)), close)
            peak = float(pos["peak"])
            if sl > 0 and close <= entry * (1 - sl):
                sig, reason = "SELL", "損切りライン到達"
            elif tp > 0 and close >= entry * (1 + tp):
                sig, reason = "SELL", "利確ライン到達"
            elif tr > 0 and close <= peak * (1 - tr):
                sig, reason = "SELL", "トレーリングストップ到達"
            elif prob <= cfg["sell_threshold"]:
                sig, reason = "SELL", f"上昇確率{prob:.2f}が閾値以下"
            else:
                sig, reason = "HOLD", "保有継続"
        else:
            if not passes_filters(row, cfg["filters"]):
                sig, reason = "対象外", "絞り込み条件を満たさない"
            elif prob >= cfg["buy_threshold"]:
                sig, reason = "BUY候補", f"上昇確率{prob:.2f}"
            else:
                sig, reason = "様子見", f"上昇確率{prob:.2f}"
        signals.append({"ticker": ticker, "signal": sig, "reason": reason, "prob": prob,
                        "close": close, "size_mult": size_mult_from_vol(row["vol20"], cfg)})
    return signals, data_date


def print_signals(signals, data_date, pf, use_labels=""):
    print()
    d = pd.Timestamp(data_date).date() if data_date is not None else "不明"
    print(f"━━━━━━ 今日のシグナル（データ最終日: {d}{use_labels}）━━━━━━")
    if data_date is not None:
        age = (pd.Timestamp.today().normalize() - pd.Timestamp(data_date).normalize()).days
        if age >= 5:
            print(f"[注意] データが{age}日前と古めです。市場休場か、データ更新をお試しください。")
    order = {"SELL": 0, "BUY候補": 1, "HOLD": 2, "様子見": 3, "対象外": 4}
    for s in sorted(signals, key=lambda x: (order.get(x["signal"], 9), -(x["prob"] if np.isfinite(x["prob"]) else -1))):
        prob = "  -  " if not np.isfinite(s["prob"]) else f"{s['prob']:.2f}"
        close = "   -   " if not np.isfinite(s["close"]) else f"{s['close']:,.1f}"
        mark = "●" if s["ticker"] in pf["positions"] else " "
        print(f" {mark} {s['ticker']:<10} 終値{close:>10}  上昇確率{prob}  {s['signal']:<5} {s['reason']}")
    print(" （●=仮想保有中）これはシミュレーション用の参考情報で、投資助言ではありません。")


def plan_orders(signals, cfg, pf):
    prices = {s["ticker"]: s["close"] for s in signals if np.isfinite(s["close"])}
    orders = []
    for s in signals:
        if s["signal"] == "SELL" and s["ticker"] in pf["positions"]:
            orders.append(("SELL", s["ticker"], s["reason"], 1.0))
    selling = {t for _, t, _, _ in orders}
    slots = int(cfg["max_positions"]) - (len(pf["positions"]) - len(selling))
    buys = sorted((s for s in signals if s["signal"] == "BUY候補"), key=lambda x: -x["prob"])
    for s in buys[: max(0, slots)]:
        orders.append(("BUY", s["ticker"], s["reason"], float(s.get("size_mult", 1.0))))
    return orders, prices


def apply_orders(pf, orders, prices, cfg, when=None):
    when = when or datetime.now().strftime("%Y-%m-%d %H:%M")
    done = []
    for side, t, note, mult in sorted(orders, key=lambda x: 0 if x[0] == "SELL" else 1):
        price = prices.get(t)
        if price is None:
            continue
        tr = (exec_sell(pf, t, float(price), cfg, note, when) if side == "SELL"
              else exec_buy(pf, t, float(price), cfg, prices, note, when, size_mult=mult))
        if tr:
            done.append(tr)
    return done


def print_portfolio(pf, prices=None):
    prices = prices or {}
    print()
    print("━━━━━━ 仮想ポートフォリオ ━━━━━━")
    print(f"現金: {C.yen(pf['cash'])}")
    if pf["positions"]:
        for t, p in pf["positions"].items():
            px = prices.get(t)
            if px is not None and np.isfinite(px):
                pnl = (px - p["entry"]) * p["shares"]
                print(f"  {t:<10} {p['shares']}株  取得@{p['entry']:,.1f} → 現在@{px:,.1f}  評価損益 {pnl:+,.0f}円")
            else:
                print(f"  {t:<10} {p['shares']}株  取得@{p['entry']:,.1f}（現在値 不明）")
    else:
        print("  保有銘柄なし")
    equity = portfolio_equity(pf, prices)
    init = float(pf.get("initial_capital", equity))
    print(f"総資産(評価額): {C.yen(equity)}  （初期資金比 {equity/init*100-100:+.2f}%）")
    if pf["trades"]:
        print("── 取引履歴（直近5件）──")
        for t in pf["trades"][-5:]:
            pnl = "" if t["pnl"] is None else f"  損益{t['pnl']:+,.0f}円"
            print(f"  {t['date']} {t['side']:<4} {t['ticker']:<10} {t['shares']}株 @{t['price']:,.1f}{pnl}")
