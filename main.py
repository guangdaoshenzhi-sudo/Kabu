#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
株式売買シミュレーター v3（ペーパートレード専用 / 実際のお金は一切使いません）
ニュースセンチメント＋マクロ経済指標に対応したモジュール分割版。

使い方:
    python main.py            ... メニューを起動
    python main.py selftest   ... 通信なしで全機能の自己診断
"""
import sys
import traceback

from stocksim import config as C
from stocksim import datafeed, plots
from stocksim.backtest import print_comparison_table, run_variant_comparison
from stocksim.features import PRICE_FEATURES, SENT_FEATURES, MACRO_FEATURES, active_features, build_full_dataset
from stocksim.logutil import get_logger
from stocksim.model import feature_importance, model_name
from stocksim.portfolio import (apply_orders, compute_live_signals, load_portfolio,
                                new_portfolio, plan_orders, print_portfolio,
                                print_signals, save_portfolio)
from stocksim.sentiment import build_daily_sentiment, get_backend

log = get_logger("main")


# ------------------------------------------------------------
# データ準備の共通処理
# ------------------------------------------------------------
def prepare_dataset(cfg, force=False, need_sent=True, need_macro=True):
    """株価＋（必要なら）ニュース・マクロを読み込み、統合データセットを返す。"""
    prices, _ = datafeed.fetch_all_prices(cfg, force=force)
    daily_sent = None
    macro_raw = None
    if need_sent:
        news = datafeed.load_news_store()
        if news.empty:
            print("[情報] 蓄積されたニュースがありません。メニュー1で取得するか、news/import/ にCSVを置いてください。")
            print("       （センチメント特徴量は中立値0で埋めて続行します）")
        else:
            backend = get_backend(cfg)
            daily_sent = build_daily_sentiment(news, backend, echo=print)
    if need_macro:
        try:
            macro_raw = datafeed.fetch_macro(cfg, force=force)
        except Exception as e:
            log.warning("マクロ取得で例外: %s", e)
            print(f"[注意] マクロ指標の取得に失敗しました（{e}）。中立値0で続行します。")
    all_df, macro_df = build_full_dataset(prices, cfg, daily_sent=daily_sent, macro_raw=macro_raw)
    return all_df, macro_df


# ------------------------------------------------------------
# メニュー処理
# ------------------------------------------------------------
def menu_update_data(cfg):
    datafeed.fetch_all_prices(cfg, force=True)
    try:
        datafeed.fetch_news(cfg)
    except Exception as e:
        print(f"[注意] ニュース取得に失敗しました: {e}")
    try:
        datafeed.fetch_macro(cfg, force=True)
    except Exception as e:
        print(f"[注意] マクロ取得に失敗しました: {e}")
    print("データ更新が完了しました。")


def menu_comparison_backtest(cfg):
    all_df, macro_df = prepare_dataset(cfg)
    print(f"\n使用モデル: {model_name(cfg)} / 再学習間隔: {cfg['retrain_every']}営業日")
    results = run_variant_comparison(all_df, cfg)
    print_comparison_table(results, cfg)

    print("\nグラフを生成しています...")
    paths = [
        plots.plot_equity_comparison(results),
        plots.plot_sentiment(all_df),
        plots.plot_macro(macro_df),
    ]
    try:
        imp = feature_importance(all_df, PRICE_FEATURES + SENT_FEATURES + MACRO_FEATURES, cfg)
        paths.append(plots.plot_feature_importance(imp))
        print("── 特徴量重要度 上位10（Permutation Importance / ＋両方モデル基準）──")
        for name, v in imp[:10]:
            print(f"  {name:<14} {v:+.4f}")
    except Exception as e:
        print(f"[注意] 重要度計算に失敗しました: {e}")
    ok_paths = [p for p in paths if p]
    if ok_paths:
        print(f"\nグラフ{len(ok_paths)}枚を保存しました → {C.CHARTS_DIR}/")
        for p in ok_paths:
            print(f"  {p}")
    print("\n[読み方] センチメントはニュース履歴が貯まるほど効きます。差が小さいのは正常な結果です。")


def menu_signals(cfg, execute=False):
    need_sent = bool(cfg.get("use_sentiment"))
    need_macro = bool(cfg.get("use_macro"))
    all_df, _ = prepare_dataset(cfg, need_sent=need_sent, need_macro=need_macro)
    feats = active_features(cfg)
    pf = load_portfolio(cfg)
    signals, data_date = compute_live_signals(all_df, feats, cfg, pf)
    if pf["positions"]:
        save_portfolio(pf)  # トレーリング用の高値更新を保存
    labels = f" / センチメント{'ON' if need_sent else 'OFF'}・マクロ{'ON' if need_macro else 'OFF'}"
    print_signals(signals, data_date, pf, use_labels=labels)
    orders, prices = plan_orders(signals, cfg, pf)
    if not execute:
        if orders:
            print("\n実行予定の仮想注文（メニュー4で執行できます）:")
            for side, t, note, _ in orders:
                print(f"  {side:<4} {t}  ({note})")
        return
    if not orders:
        print("\n本日執行する仮想注文はありません。")
        return
    print("\n以下の仮想注文を最新終値で執行します:")
    for side, t, note, _ in orders:
        px = prices.get(t)
        px_s = f"@{px:,.1f}" if px is not None else "@不明"
        print(f"  {side:<4} {t} {px_s}  ({note})")
    if input("実行しますか？（yes と入力）> ").strip().lower() != "yes":
        print("キャンセルしました。")
        return
    done = apply_orders(pf, orders, prices, cfg)
    save_portfolio(pf)
    print(f"{len(done)}件を約定しました（仮想）。")
    print_portfolio(pf, prices)


def menu_portfolio(cfg):
    pf = load_portfolio(cfg)
    prices = {}
    for t in pf["positions"]:
        try:
            df = datafeed.fetch_history(t, cfg["history_period"])
            prices[t] = float(df["Close"].iloc[-1])
        except Exception as e:
            print(f"[注意] {t} の現在値取得に失敗: {e}")
    print_portfolio(pf, prices)


def settings_menu(cfg):
    while True:
        print("\n──── 設定 ────")
        print(" 1) 銘柄リストの編集")
        print(" 2) 絞り込み条件（価格・出来高・ボラティリティ）")
        print(" 3) 売買ルール（閾値・損切り・トレーリング・目標月利など）")
        print(" 4) 分析オプション（センチメント/マクロのON・OFF、バックエンド）")
        print(" 5) 初期資金")
        print(" 6) 仮想ポートフォリオをリセット")
        print(" 7) 現在の設定を表示")
        print(" 0) 戻る（保存）")
        c = input("番号を選択 > ").strip()
        if c == "1":
            print(f"\n現在の銘柄（{len(cfg['tickers'])}個）:\n  " + ", ".join(cfg["tickers"]))
            s = input(f"カンマ区切りで新リストを入力（{C.MIN_TICKERS}〜{C.MAX_TICKERS}個 / 空Enterで変更なし）\n> ").strip()
            if s:
                ok, msg, cleaned = C.validate_tickers(s.split(","))
                if ok:
                    cfg["tickers"] = cleaned
                    print(f"更新しました。{msg}")
                else:
                    print(f"[変更不可] {msg}")
        elif c == "2":
            f = cfg["filters"]
            f["min_price"] = C.ask_float("  最低株価", f["min_price"])
            f["max_price"] = C.ask_float("  最高株価", f["max_price"])
            f["min_avg_volume"] = C.ask_float("  20日平均出来高の下限", f["min_avg_volume"])
            f["max_volatility"] = C.ask_float("  ボラティリティ上限（例0.06）", f["max_volatility"])
            if f["min_price"] > f["max_price"]:
                f["min_price"], f["max_price"] = f["max_price"], f["min_price"]
        elif c == "3":
            cfg["buy_threshold"] = min(max(C.ask_float("  買い閾値(0.5〜1.0)", cfg["buy_threshold"]), 0.50), 0.99)
            cfg["sell_threshold"] = min(max(C.ask_float("  売り閾値(0.0〜0.5)", cfg["sell_threshold"]), 0.01), 0.50)
            cfg["stop_loss_pct"] = max(0.0, C.ask_float("  損切り幅（0で無効）", cfg["stop_loss_pct"]))
            cfg["take_profit_pct"] = max(0.0, C.ask_float("  固定利確幅（0で無効）", cfg["take_profit_pct"]))
            cfg["trailing_stop_pct"] = max(0.0, C.ask_float("  トレーリング幅（0で無効）", cfg["trailing_stop_pct"]))
            cfg["max_positions"] = max(1, C.ask_int("  同時保有の最大銘柄数", cfg["max_positions"]))
            cfg["unit_size"] = max(1, C.ask_int("  売買単位", cfg["unit_size"]))
            cfg["fee_rate"] = max(0.0, C.ask_float("  片道手数料率", cfg["fee_rate"]))
            cfg["label_horizon"] = min(10, max(1, C.ask_int("  予測ホライズン(営業日)", cfg["label_horizon"])))
            cfg["target_monthly_return"] = max(0.0, C.ask_float("  目標月利（例0.03）", cfg["target_monthly_return"]))
            cfg["retrain_every"] = max(5, C.ask_int("  再学習間隔(営業日)", cfg["retrain_every"]))
            hp = input(f"  データ期間 1y/2y/3y/5y/10y [{cfg['history_period']}]: ").strip().lower()
            if hp in ("1y", "2y", "3y", "5y", "10y"):
                cfg["history_period"] = hp
        elif c == "4":
            cfg["use_sentiment"] = C.ask_yesno("  シグナルにニュースセンチメントを使う", cfg["use_sentiment"])
            cfg["use_macro"] = C.ask_yesno("  シグナルにマクロ経済指標を使う", cfg["use_macro"])
            b = input(f"  感情分析バックエンド auto/lexicon/finbert [{cfg['sentiment_backend']}]: ").strip().lower()
            if b in ("auto", "lexicon", "finbert"):
                cfg["sentiment_backend"] = b
            print("  ※ finbert には transformers+torch のインストールが必要です（PC推奨・英語ニュース向け）。")
        elif c == "5":
            cfg["initial_capital"] = max(10000.0, C.ask_float("  初期資金（円）", cfg["initial_capital"]))
            print("  ※ ポートフォリオのリセット後から反映されます。")
        elif c == "6":
            if input("本当にリセットしますか？（yes と入力）> ").strip().lower() == "yes":
                save_portfolio(new_portfolio(cfg))
                print("仮想ポートフォリオを初期化しました。")
        elif c == "7":
            import json
            print(json.dumps(cfg, ensure_ascii=False, indent=2))
        elif c == "0":
            if C.save_config(cfg):
                print("設定を保存しました。")
            return
        else:
            print("0〜7で選んでください。")


def main_menu():
    C.ensure_dirs()
    cfg = C.load_config()
    print("════════════════════════════════════════════")
    print(" 株式売買シミュレーター v3（機械学習＋ニュース＋マクロ / ペーパートレード専用）")
    print(" ※ 実際のお金・実際の発注は一切使いません")
    print("════════════════════════════════════════════")
    while True:
        print(f"\n銘柄数: {len(cfg['tickers'])} / 初期資金: {C.yen(cfg['initial_capital'])} "
              f"/ 目標月利: {cfg['target_monthly_return']*100:.1f}% "
              f"/ センチメント: {'ON' if cfg['use_sentiment'] else 'OFF'} "
              f"/ マクロ: {'ON' if cfg['use_macro'] else 'OFF'}")
        print(" 1) データ更新（株価・ニュース・マクロ指標）")
        print(" 2) 比較バックテスト（従来/＋センチメント/＋マクロ/＋両方 ＋グラフ出力）")
        print(" 3) 今日のシグナルを見る")
        print(" 4) シグナルに従って仮想売買を実行")
        print(" 5) 仮想ポートフォリオを確認")
        print(" 6) 設定")
        print(" 7) セルフテスト（通信なしの動作確認）")
        print(" 0) 終了")
        try:
            c = input("番号を選択 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n終了します。")
            return
        try:
            if c == "1":
                menu_update_data(cfg)
            elif c == "2":
                menu_comparison_backtest(cfg)
            elif c == "3":
                menu_signals(cfg, execute=False)
            elif c == "4":
                menu_signals(cfg, execute=True)
            elif c == "5":
                menu_portfolio(cfg)
            elif c == "6":
                settings_menu(cfg)
            elif c == "7":
                from stocksim.selftest import run_selftest
                run_selftest()
            elif c == "0":
                print("終了します。お疲れさまでした。")
                return
            else:
                print("0〜7で選んでください。")
        except KeyboardInterrupt:
            print("\n（中断しました。メニューに戻ります）")
        except Exception as e:
            log.error("メニュー処理でエラー: %s", e, exc_info=True)
            print(f"\n[エラー] {e}")
            traceback.print_exc()
            print("メニューに戻ります。app.log に詳細を記録しました。")


def main():
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("selftest", "--selftest", "test"):
        from stocksim.selftest import run_selftest
        sys.exit(0 if run_selftest() else 1)
    main_menu()


if __name__ == "__main__":
    main()
