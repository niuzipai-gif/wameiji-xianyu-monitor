# WAMEIJI-XIANYU 监控平台 Docker 镜像
# 基于 Python 3.11 slim，只运行 API、计算、导出和备份。
# 默认走 docker.m.daocloud.io（中国大陆可直连），需要换源时改 ARG BASE_IMAGE 即可

ARG BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim-bookworm
FROM ${BASE_IMAGE}

# 容器内时区与编码
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai \
    RUNNING_IN_DOCKER=true \
    SERVER_PORT=9890 \
    CD_DB_PATH=/app/data/cd_monitor.db

# 系统依赖：tini (PID 1) 与时区数据。
# apt 走阿里云镜像（中国大陆可直连）
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null \
 || sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null \
 || true \
 && apt-get update \
 && apt-get install -y --no-install-recommends --fix-missing \
        tini \
        tzdata \
 && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
 && echo $TZ > /etc/timezone \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖（缓存友好）
COPY pyproject.toml ./
COPY README.md ./
COPY src ./src

# pip 走清华源（中国大陆可直连）
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple \
 && pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn \
 && pip install --no-cache-dir -e .

# 复制前端静态资源、配置、prompts 等
COPY web ./web
COPY prompts ./prompts
COPY config.example.yaml ./config.example.yaml
COPY .env.example ./.env.example

# 创建运行时数据目录（实际挂载到宿主机）。不包含浏览器状态目录。
RUN mkdir -p /app/data /app/data/snapshots /app/data/screenshots /app/data/images /app/dist

EXPOSE 9890

# tini 当 PID 1，回收僵尸进程
ENTRYPOINT ["tini", "--"]

# 默认只启动 web 服务。真实浏览器采集仅可在宿主机的授权浏览器中执行。
CMD ["sh", "-c", "exec cd-monitor web --db ${CD_DB_PATH} --host 0.0.0.0 --port ${SERVER_PORT}"]

