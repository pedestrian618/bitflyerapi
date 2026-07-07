# bitflyerapi

bitFlyer Lightning API のPythonラッパー + **AI協議会方式のビットコイン自動売買ボット (aitrader)**

## install
```
$ pip install git+https://github.com/pedestrian618/bitflyerapi
```

---

## aitrader — AI協議会自動売買ボット

複数の「人格」を持った生成AI(Claude)がそれぞれ相場を分析し、
重み付き投票で売買タイミングを合議決定するボットです。

### 仕組み

```
bitFlyer公開API ──▶ 相場スナップショット(1分足・SMA・RSI・板状態など)
                          │
                          ▼
            ┌──────── AI協議会(並列で意見聴取) ────────┐
            │ 慎重派リスク管理者・堅田   (重み1.5)      │
            │ トレンドフォロワー・波多野 (重み1.0)      │
            │ 逆張りコントラリアン・逆瀬川(重み1.0)     │
            │ 短期筋スキャルパー・疾風   (重み0.8)      │
            │ マクロ分析官・大局        (重み1.2)      │
            └──────────────┬──────────────────────────┘
                           │ 各自 BUY/SELL/HOLD + 確信度(0〜1)
                           ▼
              重み付き投票で集約(合意条件を満たさなければHOLD)
                           │
                           ▼
              リスクチェック → 成行注文(デフォルトはドライラン)
```

- 各ペルソナの票は `重み × 確信度` でスコア化されます
- 合意条件(デフォルト: スコア比55%以上 かつ 賛成3名以上)を満たさない限り**HOLD**
- クールダウン・最大ポジション・残高チェックなどのリスク管理を通過した場合のみ発注

### セットアップ

```bash
pip install anthropic requests
export ANTHROPIC_API_KEY=sk-ant-...   # Claude APIキー
```

実売買する場合のみ(**デフォルトはドライラン=実注文なし**):

```bash
export BITFLYER_API_KEY=...
export BITFLYER_API_SECRET=...
export AITRADER_DRY_RUN=0
```

### 実行

```bash
python -m aitrader --once   # 1サイクルだけ実行(動作確認向け)
python -m aitrader          # ループ実行(デフォルト15分間隔)
```

### 主な環境変数

| 変数 | デフォルト | 説明 |
|---|---|---|
| `AITRADER_DRY_RUN` | `1` | `0`で実注文を送信 |
| `AITRADER_PRODUCT_CODE` | `BTC_JPY` | 取引銘柄 |
| `AITRADER_MODEL` | `claude-opus-4-8` | 使用するClaudeモデル |
| `AITRADER_ORDER_SIZE_BTC` | `0.001` | 1回の注文量(BTC) |
| `AITRADER_MAX_POSITION_BTC` | `0.01` | 最大保有量(BTC) |
| `AITRADER_MIN_JPY_BALANCE` | `10000` | BUYに必要な最低JPY残高 |
| `AITRADER_INTERVAL_SEC` | `900` | 判定サイクル間隔(秒) |
| `AITRADER_COOLDOWN_SEC` | `1800` | 連続発注を防ぐクールダウン(秒) |
| `AITRADER_MIN_AGREE_VOTES` | `3` | 合意に必要な賛成人数 |
| `AITRADER_MIN_SCORE_RATIO` | `0.55` | 合意に必要なスコア比 |

### 注意事項

- **投資は自己責任です。** 本ボットは利益を保証するものではありません
- まずはドライランで協議会の判断ログを観察してから、少額で実売買を試してください
- Claude API利用料が1サイクルあたり数円〜数十円程度かかります(5ペルソナ並列呼び出し)
