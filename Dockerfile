FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py admin.py .
COPY public/ ./public/
RUN useradd --uid 10001 --create-home ciudad && mkdir -p /data && chown ciudad:ciudad /data
USER ciudad
ENV DATA_DIR=/data PORT=8080 PYTHONUNBUFFERED=1
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/api/health',timeout=3)"
CMD ["python", "server.py"]
