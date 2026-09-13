FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    RJSHEETAL_DATA=/data

COPY site.py .
COPY static/ static/

RUN mkdir -p /data && chmod -R a+rwx /data

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=4)"

CMD ["python3", "site.py"]