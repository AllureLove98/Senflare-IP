# ===== Senflare-IP 镜像 =====
# Cloudflare优选IP采集器 - 开箱即用
# 构建: docker build -t allurelove98/senflare-ip:latest .
FROM python:3.12-slim

# 时区与必要工具（git 用于 entrypoint.sh 的结果推送）
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
    tzdata \
    procps \
    && rm -rf /var/lib/apt/lists/* \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone

WORKDIR /app

# 先装依赖，充分利用构建缓存
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝程序文件（不内置 config.json：全部配置必须由外部挂载提供，缺失时程序启动即报错退出）
COPY IPtest.py entrypoint.sh ./

RUN chmod +x /app/entrypoint.sh

# 健康检查：进程存在即可
HEALTHCHECK --interval=300s --timeout=10s --retries=3 \
    CMD pgrep -f "IPtest.py|entrypoint.sh" > /dev/null || exit 1

# 容器默认不执行（由 entrypoint.sh 循环调度）
ENTRYPOINT ["/app/entrypoint.sh"]
