FROM python:3.13-slim

# curl 是脚本抓行情/日线的实际执行工具；tzdata 不能省——缺了时区，
# datetime.now() 会退回 UTC，跨零点时"今天"会算错一天。
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    PORT=8000 \
    ZSXQ_OUT=/out

WORKDIR /app

# 只带代码。语料/名录/行情缓存都从 /data 只读挂载进来，避免镜像里焊死一份旧数据。
COPY *.py ./
COPY web ./web
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir -p /out

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=4s --start-period=10s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/healthz" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
