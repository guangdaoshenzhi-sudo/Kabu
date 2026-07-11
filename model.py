# -*- coding: utf-8 -*-
"""model.py — モデル生成・ウォークフォワード予測・的中率・特徴量重要度。"""
import math

import numpy as np
import pandas as pd

from .logutil import get_logger

log = get_logger(__name__)

_model_name_cache = None


def make_model(cfg=None):
    """(model, name) を返す。優先順: LightGBM → HistGradientBoosting → RandomForest。"""
    global _model_name_cache
    backend = (cfg or {}).get("model_backend", "auto")
    if backend in ("auto", "lightgbm"):
        try:
            from lightgbm import LGBMClassifier
            m = LGBMClassifier(
                n_estimators=300, learning_rate=0.05, num_leaves=31,
                min_child_samples=40, subsample=0.9, colsample_bytree=0.9,
                reg_lambda=1.0, random_state=42, verbose=-1, n_jobs=1,
            )
            _model_name_cache = "LightGBM"
            return m, _model_name_cache
        except ImportError:
            if backend == "lightgbm":
                log.warning("lightgbm未導入のためHistGradientBoostingを使用")
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        m = HistGradientBoostingClassifier(
            max_iter=300, learning_rate=0.06, max_leaf_nodes=31,
            min_samples_leaf=40, l2_regularization=1.0, random_state=42,
        )
        _model_name_cache = "HistGradientBoosting(sklearn)"
        return m, _model_name_cache
    except ImportError:
        from sklearn.ensemble import RandomForestClassifier
        m = RandomForestClassifier(
            n_estimators=250, max_depth=6, min_samples_leaf=20,
            random_state=42, n_jobs=1,
        )
        _model_name_cache = "RandomForest(sklearn)"
        return m, _model_name_cache


def model_name(cfg=None):
    if _model_name_cache is None:
        make_model(cfg)
    return _model_name_cache


def predict_up_proba(model, X):
    classes = list(model.classes_)
    if len(classes) == 1:
        return np.full(len(X), float(classes[0]))
    up_idx = classes.index(1)
    return model.predict_proba(X)[:, up_idx]


def walkforward_probs(all_df, features, cfg, on_progress=None):
    """ウォークフォワード検証: retrain_everyごとに「その時点までのデータのみ」で再学習。
    ラベルがhorizon日先を見るため、学習データはhorizon日分手前で打ち切る（リーク防止）。"""
    horizon = int(cfg["label_horizon"])
    retrain_every = int(cfg.get("retrain_every", 21))
    usable = all_df.dropna(subset=features)
    labeled = usable.dropna(subset=["target_up"])
    dates = sorted(usable.index.unique())
    warmup = min(250, max(120, int(len(dates) * 0.45)))
    if len(dates) < warmup + 40:
        raise RuntimeError(f"データ期間が短すぎます（{len(dates)}営業日）。history_periodを3y以上にしてください。")
    n_blocks = math.ceil((len(dates) - warmup) / retrain_every)
    parts = []
    b = 0
    for start in range(warmup, len(dates), retrain_every):
        b += 1
        end = min(start + retrain_every, len(dates)) - 1
        cutoff = dates[max(0, start - horizon)]
        train = labeled[labeled.index < cutoff]
        if len(train) < 300:
            raise RuntimeError("学習データが不足しています。期間を延ばすか銘柄を増やしてください。")
        model, _ = make_model(cfg)
        model.fit(train[features], train["target_up"].astype(int))
        block = usable[(usable.index >= dates[start]) & (usable.index <= dates[end])].copy()
        if block.empty:
            continue
        block["prob"] = predict_up_proba(model, block[features])
        parts.append(block)
        if on_progress:
            on_progress(b, n_blocks, len(train))
    if not parts:
        raise RuntimeError("予測対象期間を作れませんでした。")
    return pd.concat(parts).sort_index()


def metrics_from_probs(test_df):
    lab = test_df.dropna(subset=["target_up"])
    if lab.empty:
        return None
    y = lab["target_up"].astype(int).to_numpy()
    pred = (lab["prob"].to_numpy() >= 0.5).astype(int)
    acc = float((pred == y).mean())
    base = float(max(y.mean(), 1 - y.mean()))
    return {
        "accuracy": acc, "baseline": base, "n": int(len(lab)),
        "start": str(pd.Timestamp(lab.index.min()).date()),
        "end": str(pd.Timestamp(lab.index.max()).date()),
    }


def feature_importance(all_df, features, cfg, max_rows=2500, n_repeats=3):
    """Permutation Importance（モデル非依存で公平な重要度）。
    時系列で前75%を学習、後25%で各特徴量をシャッフルした際の精度低下を測る。"""
    from sklearn.inspection import permutation_importance
    d = all_df.dropna(subset=list(features) + ["target_up"])
    dates = sorted(d.index.unique())
    if len(dates) < 100:
        raise RuntimeError("重要度計算にはデータが不足しています。")
    split = dates[int(len(dates) * 0.75)]
    train = d[d.index < split]
    test = d[d.index >= split]
    if len(test) > max_rows:
        test = test.sample(max_rows, random_state=42)
    model, name = make_model(cfg)
    model.fit(train[features], train["target_up"].astype(int))
    r = permutation_importance(
        model, test[features], test["target_up"].astype(int),
        n_repeats=n_repeats, random_state=42,
    )
    pairs = sorted(zip(features, r.importances_mean), key=lambda x: -x[1])
    log.info("特徴量重要度を計算（モデル=%s, 検証%d行）", name, len(test))
    return pairs
