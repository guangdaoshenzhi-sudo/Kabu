# -*- coding: utf-8 -*-
"""logutil.py — ログ設定。コンソールはWARNING以上、app.logにINFO以上を記録する。"""
import logging
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(BASE_DIR, "app.log")
_configured = False


def get_logger(name):
    global _configured
    if not _configured:
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%m-%d %H:%M:%S")
        sh = logging.StreamHandler()
        sh.setLevel(logging.WARNING)  # 画面はメニュー表示を優先し、警告以上のみ
        sh.setFormatter(fmt)
        root.addHandler(sh)
        try:
            fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
            fh.setLevel(logging.INFO)
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except Exception:
            pass  # ファイルに書けない環境でも動作は継続
        _configured = True
    return logging.getLogger(name)
