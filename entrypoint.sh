#!/bin/sh
set -e

# 语料与名录从 /data（宿主机目录，只读）软链进 /app——
# 这样宿主上同步完新帖，容器下一次点筛选立刻就能用上，不用重建镜像。
if [ -d /data ]; then
  for f in resolved.json universe.json alias.json scan_cache.json \
           klines.json ohlc.json events.json exit_rules.json entry_rules.json; do
    if [ -f "/data/$f" ] && [ ! -e "/app/$f" ]; then
      ln -s "/data/$f" "/app/$f"
    fi
  done
  echo "[entrypoint] 已挂载语料：$(ls -1 /app/*.json 2>/dev/null | wc -l | tr -d ' ') 个 json"
else
  echo "[entrypoint] !! 没有挂载 /data，服务会因为没有语料而算不出结果" >&2
fi

mkdir -p "$ZSXQ_OUT"
echo "[entrypoint] 输出目录 $ZSXQ_OUT　时区 $(date +%Z)　$(date '+%Y-%m-%d %H:%M:%S')"

exec python webapp.py
