FROM python:3.12-slim
WORKDIR /site
RUN apt-get update && apt-get install -y --no-install-recommends gosu \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -r -u 10001 -m appuser \
 && mkdir -p /site/data
COPY requirements.txt /site/requirements.txt
RUN pip install --no-cache-dir -r /site/requirements.txt
COPY app_bundle.zip /site/app_bundle.zip
RUN python -m zipfile -e /site/app_bundle.zip /site \
 && test -f /site/app/main.py \
 && test -f /site/app/ingest.py \
 && test -f /site/app/reports.py \
 && test -f /site/app/templates/login.html \
 && python -m compileall -q /site/app \
 && rm -f /site/app_bundle.zip \
 && chown -R appuser:appuser /site
ENV PYTHONUNBUFFERED=1 DATA_DIR=/site/data APP_ENV=production
EXPOSE 8000
# Railway volumes are mounted at runtime and can be owned by root.
# Fix only the mount directory owner, then drop privileges before running Python.
CMD ["sh", "-c", "chown appuser:appuser /site/data && exec gosu appuser uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*' "]
