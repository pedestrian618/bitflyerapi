# bitflyerapi

bitFlyer Lightning API のPythonラッパー + **AI協議会方式のビットコイン自動売買ボット (aitrader)**

## install
```
$ pip install git+https://github.com/pedestrian618/bitflyerapi
```

---

## aitrader — AI協議会自動売買ボット

複数の「人格」を持った生成AI(Claude / ChatGPT / Gemini の混成)がそれぞれ
相場を分析し、重み付き投票で売買タイミングを合議決定するボットです。

### 仕組み

```
bitFlyer公開API ──▶ 相場スナップショット
                     ├ 短期: 1分足・直近30分(SMA/RSI/騰落率)
                     └ 中期: 1時間足・最大72時間(ローカル蓄積から構築)
                          │              ▲
                          │   SQLiteに1分足を毎サイクル蓄積
                          │   (aitrader_history.db、数日で中期データが育つ)
                          ▼
            ┌──────────── AI協議会(並列で意見聴取) ────────────┐
            │ 慎重派リスク管理者・堅田 (1.0/売買時1.5) Claude 重量級 │
            │ トレンドフォロワー・波多野 (1.0) ChatGPT 重量級   │
            │ 逆張りコントラリアン・逆瀬川(1.0) Claude 軽量級   │
            │ 短期筋スキャルパー・疾風   (0.8) ChatGPT 軽量級   │
            │ マクロ分析官・大局        (1.2) Gemini 重量級     │
            │  ※プロバイダ障害時は他社の同ティアへ自動切替      │
            └──────────────┬──────────────────────────────────┘
                           │ 各自 BUY/SELL/HOLD + 確信度(0〜1)
                           ▼
              重み付き投票で集約(合意条件を満たさなければHOLD)
                           │
                           ▼
              リスクチェック → 成行注文(デフォルトはドライラン)
```

- 各ペルソナの票は `重み × 確信度` でスコア化されます
  (慎重派の堅田はBUY/SELLを主張したときだけ重みが1.0→1.5に上がります。
  慎重派が「動いてよい」と言うのは強いシグナルのため)
- 合意条件(デフォルト: スコア比55%以上 かつ 賛成3名以上)を満たさない限り**HOLD**
- クールダウン・最大ポジション・残高チェックなどのリスク管理を通過した場合のみ発注

### セットアップ

```bash
pip install anthropic openai google-genai requests

# LLM APIキー(設定したものだけが協議会に参加。最低1つでOK)
export ANTHROPIC_API_KEY=sk-ant-...   # Claude
export OPENAI_API_KEY=sk-...          # ChatGPT
export GEMINI_API_KEY=...             # Gemini
```

**マルチプロバイダとフェイルオーバー**:
各ペルソナには担当プロバイダとモデルティア(重量級/軽量級)が割り当てられています。
あるプロバイダのAPIが落ちている・レート制限・キー未設定などの場合、
**他プロバイダの同ティアモデルへ自動フェイルオーバー**します。
失敗したプロバイダは10分間回避され(サーキットブレーカー)、成功すれば即復帰します。
キーを1つしか設定しなければ、全ペルソナがそのプロバイダで動きます。

実売買する場合のみ(**デフォルトはドライラン=実注文なし**):

```bash
export BITFLYER_API_KEY=...
export BITFLYER_API_SECRET=...
export AITRADER_DRY_RUN=0
```

### 実行

```bash
python -m aitrader --once   # 1サイクルだけ実行(動作確認向け)
python -m aitrader          # ループ実行(デフォルト1時間間隔)
```

**中期データについて**: bitFlyerの公開APIはローソク足を提供しないため、
サイクルごとに取得した1分足を `aitrader_history.db`(SQLite)に蓄積し、
そこから1時間足(最大72本)を構築してペルソナに渡します。
起動直後は中期データが不完全な旨がプロンプトに明記され、
ペルソナは確信度を落として判断します。**2〜3日回すと中期指標が育ちます。**

### ダッシュボード(ブラウザで状況確認)

SSHせずにブラウザから稼働状況を確認できる静的HTMLダッシュボードを生成できます。
現在値・価格チャート(協議会の仮想売買マーカー付き)・最新の協議会の判断根拠・
判断履歴・仮想P&Lが1ページにまとまり、5分ごとに自動再読込されます。
更新が止まっている場合は警告バナーが出るので、cron停止の検知にも使えます。

```bash
export AITRADER_DASHBOARD_PATH=/home/USERNAME/example.com/public_html/aitrader-status/index.html
```

これを設定すると、サイクル(`--once` / ループ / `--collect`)のたびに
HTMLが再生成されます。手動で生成する場合は:

```bash
python -m aitrader --dashboard
```

**Basic認証を掛ける場合**(XServer): `deploy/htaccess.example` の手順どおり、
`openssl passwd -apr1` でハッシュを作って `.htpasswd` を public_html の外に置き、
公開ディレクトリに `.htaccess` を配置してください。認証なしで公開する場合は
`.htaccess` を置かず、推測されにくいディレクトリ名にしておくのが無難です。

生成されるHTMLは外部アセットなしの自己完結ファイルで、APIキー等の秘密情報は
一切含まれません(相場データと仮想売買の記録のみ)。

### 主な環境変数

| 変数 | デフォルト | 説明 |
|---|---|---|
| `AITRADER_DRY_RUN` | `1` | `0`で実注文を送信 |
| `AITRADER_PRODUCT_CODE` | `BTC_JPY` | 取引銘柄 |
| `AITRADER_CLAUDE_MODEL_HEAVY` | `claude-opus-4-8` | Claude重量級モデル |
| `AITRADER_CLAUDE_MODEL_LIGHT` | `claude-haiku-4-5` | Claude軽量級モデル |
| `AITRADER_OPENAI_MODEL_HEAVY` | `gpt-5.1` | ChatGPT重量級モデル |
| `AITRADER_OPENAI_MODEL_LIGHT` | `gpt-5-mini` | ChatGPT軽量級モデル |
| `AITRADER_GEMINI_MODEL_HEAVY` | `gemini-2.5-pro` | Gemini重量級モデル |
| `AITRADER_GEMINI_MODEL_LIGHT` | `gemini-2.5-flash` | Gemini軽量級モデル |
| `AITRADER_LLM_COOLDOWN_SEC` | `600` | 失敗プロバイダの回避時間(秒) |
| `AITRADER_ORDER_SIZE` | `0.001` | 1回の注文量(銘柄の基軸通貨単位。旧名 `AITRADER_ORDER_SIZE_BTC` も可) |
| `AITRADER_MAX_POSITION` | `0.01` | 最大保有量(同上。旧名 `AITRADER_MAX_POSITION_BTC` も可) |
| `AITRADER_MIN_JPY_BALANCE` | `10000` | BUYに必要な最低JPY残高 |
| `AITRADER_INTERVAL_SEC` | `3600` | 判定サイクル間隔(秒) |
| `AITRADER_COOLDOWN_SEC` | `1800` | 連続発注を防ぐクールダウン(秒) |
| `AITRADER_HISTORY_PATH` | `aitrader_history.db` | 1分足を蓄積するSQLiteのパス |
| `AITRADER_DASHBOARD_PATH` | (空=無効) | ダッシュボードHTMLの出力先パス |
| `AITRADER_DASHBOARD_LINKS` | (空=非表示) | 銘柄タブ(`BTC_JPY=./,ETH_JPY=./eth/` 形式。自銘柄がハイライト) |
| `AITRADER_MIN_AGREE_VOTES` | `3` | 合意に必要な賛成人数 |
| `AITRADER_MIN_SCORE_RATIO` | `0.55` | 合意に必要なスコア比 |

### 複数銘柄の並走(マルチインスタンス)

1銘柄 = 1インスタンスの構成で、コードは共通・環境変数だけで銘柄を切り替えます。
ペルソナのプロンプトは銘柄名が自動で差し替わり(`BTC_JPY`→「ビットコイン(BTC/JPY)」等)、
履歴DB・仮想P&L・ダッシュボードは銘柄ごとに独立します。

```bash
# 例: ETH_JPY をドライランで並走(BTCと成績比較する)
AITRADER_PRODUCT_CODE=ETH_JPY \
AITRADER_DRY_RUN=1 \
AITRADER_HISTORY_PATH=aitrader_history_eth.db \
AITRADER_ORDER_SIZE=0.01 \
AITRADER_MAX_POSITION=0.1 \
python -m aitrader --once
```

各インスタンスが自分のダッシュボードページを生成し、`AITRADER_DASHBOARD_LINKS` を
設定するとページ上部に銘柄タブが出て相互に行き来できます。cron設定の実例は
`deploy/cron.example` を参照してください。

注文サイズはbitFlyerの最小注文量(BTC 0.001 / ETH 0.01 / XRP 0.1)以上に
設定してください。板取引(Lightning現物)のある銘柄のみ対応です。

### 注意事項

- **投資は自己責任です。** 本ボットは利益を保証するものではありません
- まずはドライランで協議会の判断ログを観察してから、少額で実売買を試してください
- LLM API利用料が1サイクルあたり数円〜数十円かかります(5ペルソナ並列呼び出し、3社に分散)。
  コストを下げたいときは重量級モデルの環境変数を安価なモデルに差し替えてください
- モデル名は各社のリリースで変わります。デフォルトが古くなったら環境変数で最新のモデルIDに更新してください
