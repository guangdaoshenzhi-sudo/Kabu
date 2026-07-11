# -*- coding: utf-8 -*-
"""config.py — 既定設定・設定の読み書き・小さな共通ユーティリティ。"""
import json
import os

from .logutil import get_logger

log = get_logger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "sim_config.json")
PORTFOLIO_PATH = os.path.join(BASE_DIR, "portfolio.json")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
NEWS_DIR = os.path.join(BASE_DIR, "news")
NEWS_IMPORT_DIR = os.path.join(NEWS_DIR, "import")
CHARTS_DIR = os.path.join(BASE_DIR, "charts")

MIN_TICKERS = 10
MAX_TICKERS = 30

DEFAULT_TICKERS = [
    "7203.T", "6758.T", "9984.T", "6861.T", "8306.T",
    "9432.T", "6501.T", "7974.T", "4063.T", "6098.T",
    "8035.T", "9983.T", "4502.T", "6902.T", "7267.T",
    "8058.T", "8001.T", "4568.T", "6367.T", "9433.T",
]

DEFAULT_CONFIG = {
    "tickers": DEFAULT_TICKERS,
    "initial_capital": 1_000_000,
    "max_positions": 5,
    "unit_size": 1,
    "fee_rate": 0.0005,
    "buy_threshold": 0.55,
    "sell_threshold": 0.45,
    "stop_loss_pct": 0.05,
    "take_profit_pct": 0.0,
    "trailing_stop_pct": 0.08,
    "label_horizon": 3,
    "vol_target": 0.02,
    "target_monthly_return": 0.03,
    "history_period": "3y",
    "retrain_every": 21,
    # --- 分析オプション ---
    "use_sentiment": True,          # 今日のシグナルでセンチメント特徴量を使う
    "use_macro": True,              # 今日のシグナルでマクロ特徴量を使う
    "sentiment_backend": "auto",    # auto: FinBERTがあれば使い、無ければ辞書方式
    "sentiment_model": "ProsusAI/finbert",  # transformers利用時のモデル名（英語ニュース向け）
    "sentiment_decay_days": 3.0,    # ニュースが無い日の減衰半減の目安
    "model_backend": "auto",        # auto: lightgbm→HistGradientBoosting→RandomForest
    # --- マクロ経済指標の取得元（FREDはAPIキー不要のCSVエンドポイントを使用） ---
    "macro": {
        "usdjpy":      {"src": "yfinance", "symbol": "JPY=X"},
        "wti":         {"src": "yfinance", "symbol": "CL=F"},
        "vix":         {"src": "yfinance", "symbol": "^VIX"},
        "policy_rate": {"src": "fred", "symbol": "DFF"},        # 米FF金利(日次)。日本なら例: IRSTCB01JPM156N(月次)
        "cpi":         {"src": "fred", "symbol": "CPIAUCSL", "monthly": True},  # 米CPI。日本なら例: JPNCPIALLMINMEI
    },
    "filters": {
        "min_price": 0.0,
        "max_price": 1e12,
        "min_avg_volume": 100000.0,
        "max_volatility": 0.06,
    },
}


# ------------------------------------------------------------
# 共通ユーティリティ
# ------------------------------------------------------------
def deep_copy_cfg(cfg):
    return json.loads(json.dumps(cfg))


def yen(x):
    try:
        return f"{x:,.0f}円"
    except Exception:
        return str(x)


def ask_float(prompt, default):
    s = input(f"{prompt} [{default}]: ").strip()
    if s == "":
        return float(default)
    try:
        return float(s)
    except ValueError:
        print("  数値として読めなかったので既定値を使います。")
        return float(default)


def ask_int(prompt, default):
    return int(round(ask_float(prompt, default)))


def ask_yesno(prompt, default):
    cur = "y" if default else "n"
    s = input(f"{prompt} (y/n) [{cur}]: ").strip().lower()
    if s == "":
        return bool(default)
    return s.startswith("y")


def ensure_dirs():
    for d in (CACHE_DIR, NEWS_DIR, NEWS_IMPORT_DIR, CHARTS_DIR):
        try:
            os.makedirs(d, exist_ok=True)
        except Exception as e:
            log.warning("ディレクトリ作成に失敗: %s (%s)", d, e)


# ------------------------------------------------------------
# 設定の読み書き
# ------------------------------------------------------------
def _merge_defaults(cfg):
    merged = deep_copy_cfg(DEFAULT_CONFIG)
    if isinstance(cfg, dict):
        for k, v in cfg.items():
            if k in ("filters", "macro") and isinstance(v, dict):
                merged[k].update(v)
            else:
                merged[k] = v
    return merged


def validate_tickers(tickers):
    if not isinstance(tickers, list):
        return False, "銘柄リストの形式が不正です。", []
    cleaned = []
    for t in tickers:
        t = str(t).strip().upper()
        if t and t not in cleaned:
            cleaned.append(t)
    n = len(cleaned)
    if n < MIN_TICKERS:
        return False, f"銘柄は{MIN_TICKERS}個以上必要です（現在{n}個）。", cleaned
    if n > MAX_TICKERS:
        return False, f"銘柄は{MAX_TICKERS}個以下にしてください（現在{n}個）。", cleaned
    return True, f"OK（{n}銘柄）", cleaned


def load_config(path=CONFIG_PATH):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = _merge_defaults(json.load(f))
        except Exception as e:
            log.warning("設定ファイル読込失敗のため既定値を使用: %s", e)
            print(f"設定ファイルの読み込みに失敗したため既定値を使います: {e}")
            cfg = _merge_defaults({})
    else:
        cfg = _merge_defaults({})
    ok, msg, cleaned = validate_tickers(cfg.get("tickers", []))
    cfg["tickers"] = cleaned if len(cleaned) >= MIN_TICKERS else list(DEFAULT_TICKERS)
    if not ok:
        log.warning("銘柄リスト補正: %s", msg)
    return cfg


def save_config(cfg, path=CONFIG_PATH):
    ok, msg, cleaned = validate_tickers(cfg.get("tickers", []))
    if not ok:
        print(f"[保存不可] {msg}")
        return False
    cfg["tickers"] = cleaned
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        log.info("設定を保存しました: %s", path)
        return True
    except Exception as e:
        log.error("設定保存失敗: %s", e)
        print(f"設定の保存に失敗しました: {e}")
        return False
