FROM python:3.12-slim
WORKDIR /site
RUN useradd -r -u 10001 -m appuser && mkdir -p /site/data && chown -R appuser:appuser /site
COPY requirements.txt /site/
RUN pip install --no-cache-dir -r requirements.txt
COPY . /site/
RUN chown -R appuser:appuser /site
USER appuser
ENV PYTHONUNBUFFERED=1 DATA_DIR=/site/data APP_ENV=production
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
