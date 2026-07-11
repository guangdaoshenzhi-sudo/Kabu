# 株式売買シミュレーター v3（ニュースセンチメント＋マクロ経済指標対応）

ペーパートレード専用シミュレーターのモジュール分割版です。実際のお金・発注は一切扱いません。

## フォルダ構成

```
stocksim_v3/
├── main.py                 # エントリーポイント（メニューCLI）
└── stocksim/
    ├── config.py           # 設定・定数・入出力
    ├── logutil.py          # ログ（コンソール=警告以上、app.log=詳細）
    ├── datafeed.py         # データ取得（株価/ニュース/マクロ）＋キャッシュ
    ├── sentiment.py        # 感情分析（辞書方式が標準、FinBERTに自動切替可）
    ├── features.py         # 特徴量生成（価格14＋センチメント4＋マクロ8）
    ├── model.py            # モデル生成・ウォークフォワード・重要度
    ├── backtest.py         # シミュレーション・成績指標・4モデル比較
    ├── portfolio.py        # 仮想ポートフォリオ・シグナル・売買執行
    ├── plots.py            # グラフ4種のPNG出力（charts/へ保存）
    └── selftest.py         # 通信なしの自己診断（合成データで全機能検証）
```

実行時に `cache/`（株価・FRED）、`news/`（ニュース蓄積）、`charts/`（グラフ）、`app.log`（ログ）、`sim_config.json`、`portfolio.json` が main.py と同じ場所に作られます。

## インストール

必須: `pip install yfinance scikit-learn pandas numpy matplotlib`
任意: `pip install lightgbm`（あればモデルが自動でLightGBMに切替）、`pip install transformers torch`（あればFinBERTに切替。**PC推奨**。スマホのPydroid 3ではtorchが重すぎるため辞書方式のままを推奨）

実行: `python main.py` ／ 動作確認: `python main.py selftest`

## ① ニュースセンチメントについて（重要な現実）

- 取得元は yfinance のニュースAPI（無料）で、**取れるのは直近分のみ**です。取得のたびに `news/store.csv` へ蓄積される設計なので、**メニュー1を定期的に実行するほど履歴が貯まり、バックテストでセンチメントが効き始めます**。
- 過去のニュースを持っている場合は `news/import/` に CSV（列: `date,ticker,title` 任意で `text`）を置けば取り込まれます。
- 履歴が無い期間のバックテストでは、センチメント特徴量は中立値で埋められるため「＋センチメント」モデルは従来とほぼ同じ（またはノイズ分わずかに悪い）結果になります。**これは正常であり、効果を捏造しないための仕様です。**
- 感情分析は標準で金融語彙辞書方式（日英対応・依存ゼロ）。`sentiment_backend` を `finbert` にすると transformers のモデル（既定 ProsusAI/finbert、**英語ニュース向け**）を使います。日本語ニュース主体なら設定 `sentiment_model` を日本語の感情分類モデルに差し替えてください。

## ② マクロ経済指標について

- FRED はAPIキー不要のCSVエンドポイント経由で取得します。既定の系列: USD/JPY・WTI・VIX（yfinance）、米FF金利 DFF・米CPI CPIAUCSL（FRED）。
- 日本の指標に替える場合は `sim_config.json` の `macro` を編集します（例: 日本CPIは `JPNCPIALLMINMEI`、日本政策金利は `IRSTCB01JPM156N`、いずれも `"monthly": true`）。
- **未来情報の混入（リーク）対策**: 月次系列は1期シフトしてから前方補完します（CPIは発表が約1ヶ月遅れるため、当月値を当月に使うと未来を見てしまう）。取得に失敗した指標は警告のうえ中立値0で埋めて続行します。

## メニュー

1 データ更新（株価・ニュース・マクロを強制再取得。ニュース蓄積もここ）／ 2 比較バックテスト（従来・＋センチメント・＋マクロ・＋両方を同一条件で実行し、最終資産・年率・Sharpe・最大DD・勝率・売買回数・平均月利の表＋グラフ4種＋特徴量重要度を出力）／ 3 今日のシグナル（設定のON/OFFに応じた特徴量で判定）／ 4 仮想売買の実行 ／ 5 ポートフォリオ ／ 6 設定 ／ 7 セルフテスト

グラフは `charts/` にPNG保存: `comparison.png`（累積資産曲線の比較）、`sentiment.png`（センチメント推移＋ニュース件数）、`macro.png`（マクロ指標推移）、`feature_importance.png`（Permutation重要度、センチメント=赤/マクロ=青/価格=灰）。文字化け防止のためグラフ内は英字表記です。

## モデルについて

優先順位: LightGBM →（未導入なら）sklearn HistGradientBoosting →（旧環境なら）RandomForest。HistGradientBoostingはLightGBMと同系統のヒストグラム型勾配ブースティングで、性能差は通常わずかです。**LightGBMの導入は任意**で、未導入でも全機能が動きます。特徴量重要度はモデル非依存のPermutation Importance（検証期間で各特徴量をシャッフルした際の精度低下）を採用しています。

## 重要な注意

- 数営業日先の予測の的中率は良くて50%台前半で、ニュースやマクロを足しても劇的には変わりません。比較表で差が出ないのは「その特徴量に追加情報が無かった」という正直な結果です。
- 検証にはウォークフォワード方式（約1ヶ月ごとに過去データのみで再学習、ラベル分のエンバーゴつき）を使用し、未来情報の混入を防いでいます。
- 過去の成績は将来を保証しません。これは投資助言ではなく学習・検証用ツールです。
