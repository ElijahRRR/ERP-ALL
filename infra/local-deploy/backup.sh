#!/usr/bin/env bash
# ERP-ALL 每日备份（D-Q52 红线）：pg_dump 自定义格式 → 本地保留 14 天 → 可选 rclone 异地
# 用法：bash infra/local-deploy/backup.sh   （建议挂 cron 每日 02:30）
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-$HOME/erp-backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
COMPOSE="docker compose -f $(dirname "$0")/../docker-compose.yml"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="$BACKUP_DIR/erp_all_${STAMP}.dump"

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
