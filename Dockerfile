FROM python:3.12-bookworm
ENV PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && playwright install --with-deps chromium
RUN curl -fsSL https://install.speedtest.net/app/cli/ookla-speedtest-1.2.0-linux-x86_64.tgz | tar xz -C /usr/local/bin speedtest
RUN apt-get update && apt-get install -y --no-install-recommends iputils-ping && rm -rf /var/lib/apt/lists/*
COPY . .
RUN useradd -m -u 10001 speedpanel && mkdir -p /data && chown -R speedpanel /data /app
USER speedpanel
EXPOSE 8095
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8095/health',timeout=3)"
CMD ["gunicorn","--bind","0.0.0.0:8095","--workers","1","--threads","4","--timeout","600","app:app"]
