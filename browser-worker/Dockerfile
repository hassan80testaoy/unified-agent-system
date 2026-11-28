FROM mcr.microsoft.com/playwright/python:v1.51.0-noble

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    x11vnc \
    novnc \
    websockify \
    fluxbox \
    openssl \
    && rm -rf /var/lib/apt/lists/*

# Non-root browser runtime user (F5)
RUN useradd -m -u 1001 browser || true
WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY worker.py /app/worker.py
COPY interactive_worker.py /app/interactive_worker.py
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh && \
    mkdir -p /home/browser/profiles /home/browser/.vnc && \
    chown -R browser:browser /app /home/browser

USER browser
ENV DISPLAY=:99
ENV HOME=/home/browser

EXPOSE 6080 7080
ENTRYPOINT ["/app/entrypoint.sh"]
