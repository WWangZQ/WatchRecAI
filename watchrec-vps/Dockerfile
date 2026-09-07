FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home watchrec
COPY config.py server.py ./
RUN mkdir /data && chown watchrec:watchrec /data
USER watchrec
ENV HOST=0.0.0.0 PORT=8765 UPLOAD_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8765
CMD ["python", "server.py"]
