FROM python:3.12-slim
WORKDIR /site
RUN useradd -r -u 10001 -m appuser && mkdir -p /site/data && chown -R appuser:appuser /site
COPY requirements.txt /site/requirements.txt
RUN pip install --no-cache-dir -r /site/requirements.txt
# Ship the whole app as ONE archive to avoid GitHub UI flattening or misplaced folders.
COPY app_bundle.zip /site/app_bundle.zip
RUN python -m zipfile -e /site/app_bundle.zip /site \
 && test -f /site/app/main.py \
 && test -f /site/app/ingest.py \
 && test -f /site/app/reports.py \
 && test -f /site/app/templates/login.html \
 && python -m compileall -q /site/app \
 && rm -f /site/app_bundle.zip
RUN chown -R appuser:appuser /site
USER appuser
ENV PYTHONUNBUFFERED=1 DATA_DIR=/site/data APP_ENV=production
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*' "]
