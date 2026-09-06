# WAMEIJI-XIANYU 监控平台 Docker 镜像
# 基于 Python 3.11 slim，与 ai-goofish-monitor 的运行时对齐
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
    CD_DB_PATH=/var/lib/cd_monitor/cd_monitor.db \
    # Playwright 走 npmmirror（中国大陆可直连，绕开 azureedge）
    PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright \
    PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright

# 系统依赖：tini (PID 1)、tzdata、playwright 运行所需 libzbar0 / libnss3 / libxss1 等
# apt 走阿里云镜像（中国大陆可直连）
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null \
 || sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null \
 || true \
 && apt-get update \
 && apt-get install -y --no-install-recommends --fix-missing \
        tini \
        tzdata \
        libzbar0 \
        libnss3 \
        libnspr4 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libcups2 \
        libdrm2 \
        libxkbcommon0 \
        libxcomposite1 \
        libxdamage1 \
        libxrandr2 \
        libgbm1 \
        libpango-1.0-0 \
        libcairo2 \
        libasound2 \
        libxshmfence1 \
        libxss1 \
        fonts-wqy-microhei \
        libxfixes3 \
        libxext6 \
        libxi6 \
        libxcursor1 \
        libxtst6 \
        libx11-xcb1 \
        libxrender1 \
        libxcb1 \
        fonts-wqy-zenhei \
        tesseract-ocr \
 && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
 && echo $TZ > /etc/timezone \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖（缓存友好）
COPY pyproject.toml ./
COPY src ./src

# pip 走清华源（中国大陆可直连）
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple \
 && pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn \
 && pip install --no-cache-dir \
        "python-dotenv>=1.0" \
        "openai>=1.40" \
        "fastapi>=0.110" \
        "uvicorn[standard]>=0.27" \
        "pydantic>=2.6" \
        "pydantic-settings>=2.2" \
        "requests>=2.31" \
        "jinja2>=3.1" \
        "aiofiles>=23.2" \
        "httpx>=0.27" \
        "apscheduler>=3.10" \
        "playwright>=1.48" \
        "Pillow>=10.0" \
        "pytesseract>=0.3.10" \
        "imagehash>=4.3" \
        "PyWavelets>=1.4" \
        "pyzbar>=0.1.9" \
        "qrcode>=7.4" \
        "python-socks>=2.0"

# 把项目装成可编辑包，让 `cd-monitor` / `cd-monitor-web` 入口可用
RUN pip install --no-cache-dir -e .

# 安装 playwright chromium 浏览器（仅 chromium 节省空间，镜像已切到 npmmirror）
RUN playwright install chromium \
 && mkdir -p /usr/share/tesseract-ocr/4.00/tessdata/ \
 && curl -fsSL -o /usr/share/tesseract-ocr/4.00/tessdata/jpn.traineddata https://github.com/tesseract-ocr/tessdata_fast/raw/main/jpn.traineddata || true \
 && curl -fsSL -o /usr/share/tesseract-ocr/4.00/tessdata/chi_sim.traineddata https://github.com/tesseract-ocr/tessdata_fast/raw/main/chi_sim.traineddata || true

# 复制前端静态资源、配置、prompts 等
COPY web ./web
# 把 data 下的 runtime scripts(multi_source_scraper.py / run_one_cycle.py / match_real.py 等)
# 也 baked 进镜像;运行时持久化数据通过 named volume 覆盖子目录
COPY data ./data
COPY prompts ./prompts
COPY config.example.yaml ./config.example.yaml
COPY .env.example ./.env.example

# 创建运行时数据目录（实际挂载到宿主机）
RUN mkdir -p /var/lib/cd_monitor \
             /app/data /app/data/snapshots /app/data/screenshots /app/data/images \
             /app/state /app/logs /app/jsonl /app/price_history /app/dist

EXPOSE 9890

# tini 当 PID 1，回收僵尸进程
ENTRYPOINT ["tini", "--"]

# 默认只启动 web 服务。真实浏览器采集需在宿主机使用授权 profile，
# 再通过 snapshot/evaluate 入口写入本地或挂载数据库。
CMD ["sh", "-c", "exec cd-monitor web --db ${CD_DB_PATH} --host 0.0.0.0 --port ${SERVER_PORT}"]

