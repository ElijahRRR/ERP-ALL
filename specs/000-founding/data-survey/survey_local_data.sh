#!/bin/bash
# 本地数据调研一键脚本（在 Mac 项目根目录运行）：
#   bash erp-core/specs/011-r0-requirements/data-survey/survey_local_data.sh
# 产物写入 erp-core/specs/011-r0-requirements/data-survey/out/ ，检查无敏感内容后 commit+push。
# 任何一项失败不影响其余（每条独立 || 提示）。

set -u
OUT="erp-core/specs/011-r0-requirements/data-survey/out"
mkdir -p "$OUT/lark"

echo "══ §1 PostgreSQL 三库 ══"
for db in uspto walmart_cleanup erp_core; do
  echo "── $db ──"
  pg_dump -d "$db" --schema-only > "$OUT/pg_${db}_schema.sql" 2>/dev/null || echo "  ✗ $db schema dump 失败（库不存在?）"
  psql -d "$db" -A -F'|' -c "SELECT relname AS 表, n_live_tup AS 行数 FROM pg_stat_user_tables ORDER BY n_live_tup DESC;" \
    > "$OUT/pg_${db}_rowcounts.txt" 2>/dev/null || echo "  ✗ $db 行数统计失败"
done

echo "── uspto 样例 ──"
for t in blacklist_brands brand_nice_class status_code_mapping; do
  psql -d uspto -A -F'|' -c "SELECT * FROM $t LIMIT 20;" > "$OUT/pg_uspto_sample_${t}.txt" 2>/dev/null || echo "  ✗ $t"
done
for t in tro_cases matched_companies; do
  psql -d uspto -A -F'|' -c "SELECT * FROM $t LIMIT 5;"  > "$OUT/pg_uspto_sample_${t}.txt" 2>/dev/null || echo "  ✗ $t（可能无数据，正常）"
done

echo "── walmart_cleanup 样例与分布 ──"
psql -d walmart_cleanup -A -F'|' -c "SELECT category, count(*) FROM error_items GROUP BY 1 ORDER BY 2 DESC;" \
  > "$OUT/pg_cleanup_category_dist.txt" 2>/dev/null || echo "  ✗ category 分布"
psql -d walmart_cleanup -A -F'|' -c "SELECT * FROM error_items LIMIT 10;" \
  > "$OUT/pg_cleanup_sample.txt" 2>/dev/null || echo "  ✗ error_items 样例"

echo "══ §2 SQLite ══"
sqlite_survey() {  # $1=路径 $2=输出前缀 $3=可选附加SQL
  local f="$1" p="$2"
  if [ -f "$f" ]; then
    sqlite3 "$f" '.schema' > "$OUT/sqlite_${p}_schema.sql"
    sqlite3 "$f" "SELECT name FROM sqlite_master WHERE type='table';" | while read -r t; do
      echo "$t|$(sqlite3 "$f" "SELECT count(*) FROM \"$t\";")" >> "$OUT/sqlite_${p}_rowcounts.txt"
    done
    [ -n "${3:-}" ] && sqlite3 "$f" "$3" > "$OUT/sqlite_${p}_extra.txt"
    echo "  ✓ $p"
  else
    echo "  ✗ 未找到 $f（请改路径后重跑该项）"
  fi
}
sqlite_survey "沃尔玛UPC生成器/upc_history.db" "upc_history" \
  "SELECT walmart_status, count(*) FROM upcs GROUP BY 1;" 2>/dev/null || \
  sqlite_survey "沃尔玛UPC生成器/upc_history.db" "upc_history" ""
sqlite_survey "walmart_settlement.db" "settlement" ""
sqlite_survey "auto_listing/state/retry_state.sqlite" "retry_state" ""
# TRO merged.db —— 按你本机实际路径改这一行：
TRO_DB="$HOME/Projects/tro-scraper-matrix/merged.db"
sqlite_survey "$TRO_DB" "tro_merged" "SELECT source, count(*) FROM cases GROUP BY 1;" || true
# 若 tro 表名不是 cases，看 schema 后手工补一条分布查询即可。

# settlement / tro 样例（表名以 schema 输出为准，失败可忽略后手工补）
[ -f walmart_settlement.db ] && sqlite3 -header walmart_settlement.db \
  "SELECT * FROM $(sqlite3 walmart_settlement.db "SELECT name FROM sqlite_master WHERE type='table' LIMIT 1;") LIMIT 5;" \
  > "$OUT/sqlite_settlement_sample.txt" 2>/dev/null

echo "══ §3 飞书表 ══"
echo "  （lark 部分请按 README §三 用 lark-cli / lark_io 导出 CSV 到 $OUT/lark/，"
echo "   每张表：表头行 + 前 10 行 + 总行数；定价表要前 3 行表头。）"

echo "══ 完成 ══"
echo "请人工检查 $OUT 内容（尤其样例文件）确认无需打码后，git add + commit + push。"
