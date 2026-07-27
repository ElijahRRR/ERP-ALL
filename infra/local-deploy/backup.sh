#!/usr/bin/env bash
# ERP-ALL 每日备份（D-Q52 红线）：pg_dump 自定义格式 → 本地保留 14 天 → 可选 rclone 异地
# 用法：bash infra/local-deploy/backup.sh   （建议挂 cron 每日 02:30）
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-$HOME/erp-backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
COMPOSE="docker compose -f $(dirname "$0")/../docker-compose.yml"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="$BACKUP_DIR/erp_all_${STAMP}.dump"

# RS-02a 起 compose 的口令由 infra/.env 注入；文件不在时 compose 会报一句难懂的
# 「required variable ... is missing a value」，而这是每日备份（D-Q52 红线）的入口，
# 静默失败代价太大——在这里换成能直接照做的提示。
ENV_FILE="$(dirname "$0")/../.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "[backup] ✗ 缺 $ENV_FILE —— 先 cp infra/.env.example infra/.env 并填强随机值" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
echo "[backup] pg_dump → $OUT"
$COMPOSE exec -T db pg_dump -U postgres -Fc erp_all > "$OUT"

SIZE=$(wc -c < "$OUT")
if [ "$SIZE" -lt 10000 ]; then
  echo "[backup] ✗ 备份文件异常偏小（${SIZE}B），视为失败" >&2
  exit 1
fi
echo "[backup] ✓ 完成（$(du -h "$OUT" | cut -f1)）"

# 异地：配置了 RCLONE_REMOTE 才执行（如 aliyun-oss:erp-backups）
if [ -n "${RCLONE_REMOTE:-}" ]; then
  echo "[backup] rclone → $RCLONE_REMOTE"
  rclone copy "$OUT" "$RCLONE_REMOTE" --no-traverse
  echo "[backup] ✓ 异地已上传"
else
  echo "[backup] ⚠ 未配置 RCLONE_REMOTE，本次无异地副本（D-Q52 要求尽快配置）"
fi

find "$BACKUP_DIR" -name 'erp_all_*.dump' -mtime "+$KEEP_DAYS" -delete
echo "[backup] 本地保留 ${KEEP_DAYS} 天内的备份"
