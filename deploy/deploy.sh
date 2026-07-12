#!/usr/bin/env bash
#
# deploy.sh — aitrader を XServer の ~/aitrader/ へ SSH+rsync で配置する(junkyfly と同方式)
#
# 使い方:
#   ./deploy/deploy.sh            # デプロイ実行
#   ./deploy/deploy.sh --check    # rsync のドライラン(転送内容の確認のみ)
#
# 重要:
#   - デプロイ設定は .env の DEPLOY_ 変数(gitignore 済み)。秘密鍵は junkyfly と共用。
#   - デプロイ先は必ず aitrader ディレクトリのみ。public_html には絶対に書き込まない。
#   - サーバー側の .env / .venv / *.db / *.log は rsync の除外で保護される(--delete でも消えない)。
#   - デプロイ後にサーバー上で --report を実行し、import と DB が壊れていないか確認する。
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# --- .env からデプロイ設定を読み込む(DEPLOY_ 変数だけを取り込む) ---
if [[ ! -f .env ]]; then
  echo "エラー: .env が見つかりません($PROJECT_ROOT/.env)" >&2
  exit 1
fi
load_deploy_env() {
  local line key val
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    case "$line" in
      *DEPLOY_*=*) : ;;
      *) continue ;;
    esac
    line="${line#"${line%%[![:space:]]*}"}"
    case "$line" in
      DEPLOY_[A-Za-z0-9_]*=*) : ;;
      *) continue ;;
    esac
    key="${line%%=*}"
    val="${line#*=}"
    if [[ ${#val} -ge 2 && "$val" == \"*\" ]]; then val="${val#\"}"; val="${val%\"}"; fi
    if [[ ${#val} -ge 2 && "$val" == \'*\' ]]; then val="${val#\'}"; val="${val%\'}"; fi
    export "$key=$val"
  done < .env
}
load_deploy_env

: "${DEPLOY_SSH_HOST:?DEPLOY_SSH_HOST が未設定です}"
: "${DEPLOY_SSH_PORT:?DEPLOY_SSH_PORT が未設定です}"
: "${DEPLOY_SSH_USER:?DEPLOY_SSH_USER が未設定です}"
: "${DEPLOY_SSH_KEY:?DEPLOY_SSH_KEY が未設定です}"
: "${DEPLOY_REMOTE_DIR:?DEPLOY_REMOTE_DIR が未設定です}"

# 鍵パスの先頭 ~ を展開(junkyfly リポジトリの鍵を参照するため)
SSH_KEY="${DEPLOY_SSH_KEY/#\~/$HOME}"
if [[ ! -f "$SSH_KEY" ]]; then
  echo "エラー: SSH鍵が見つかりません: $SSH_KEY" >&2
  exit 1
fi

# --- 安全確認: デプロイ先は aitrader のみ。public_html への誤爆は必ず止める ---
case "$DEPLOY_REMOTE_DIR" in
  *public_html*)
    echo "エラー: DEPLOY_REMOTE_DIR に public_html が含まれています。中断します。" >&2
    exit 1
    ;;
  aitrader/|*/aitrader/) : ;;
  *)
    echo "エラー: DEPLOY_REMOTE_DIR が 'aitrader/' で終わっていません。誤配置防止のため中断します。" >&2
    echo "  DEPLOY_REMOTE_DIR=$DEPLOY_REMOTE_DIR" >&2
    exit 1
    ;;
esac

# --- rsync オプション(--check ならドライラン) ---
RSYNC_FLAGS=(-az --delete)
MODE="デプロイ"
if [[ "${1:-}" == "--check" ]]; then
  RSYNC_FLAGS+=(-nv)
  MODE="ドライラン(確認のみ)"
fi

# --- デプロイするコミットをスタンプ(サーバー側で cat .deploy_version で確認できる) ---
{
  git rev-parse --short HEAD 2>/dev/null || echo "unknown"
  git diff --quiet 2>/dev/null || echo "(uncommitted changes)"
  date "+%Y-%m-%dT%H:%M:%S%z"
} > .deploy_version

echo "== aitrader deploy [$MODE] =="
echo "  ローカル : $PROJECT_ROOT/"
echo "  リモート : $DEPLOY_SSH_USER@$DEPLOY_SSH_HOST:$DEPLOY_REMOTE_DIR (port $DEPLOY_SSH_PORT)"
echo "  バージョン: $(head -1 .deploy_version)"
echo

# サーバー側の .env / .venv / DB / ログは除外(= --delete からも保護される)
rsync "${RSYNC_FLAGS[@]}" \
  --exclude .git --exclude .venv --exclude __pycache__ \
  --exclude '.env*' --exclude '*.db' --exclude '*.log' \
  --exclude .claude --exclude .DS_Store --exclude 'deploy/keys' \
  -e "ssh -i $SSH_KEY -p $DEPLOY_SSH_PORT -o StrictHostKeyChecking=accept-new" \
  "$PROJECT_ROOT"/ \
  "$DEPLOY_SSH_USER@$DEPLOY_SSH_HOST:$DEPLOY_REMOTE_DIR"

if [[ "$MODE" == デプロイ ]]; then
  echo
  echo "-- スモークテスト(サーバー上で --report 実行) --"
  ssh -i "$SSH_KEY" -p "$DEPLOY_SSH_PORT" -o StrictHostKeyChecking=accept-new \
    "$DEPLOY_SSH_USER@$DEPLOY_SSH_HOST" \
    "cd $DEPLOY_REMOTE_DIR && .venv/bin/python -m aitrader --report && echo && echo 'deployed version:' && cat .deploy_version"
  echo
  echo "完了: 次の毎時7分の cron から新コードで実行されます"
fi
