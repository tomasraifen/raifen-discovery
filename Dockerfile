FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY config/ ./config/
COPY cli.py .

ENV HOST=0.0.0.0
ENV PORT=8000
ENV DB_PATH=/data/discovery.db
ENV UPLOADS_DIR=/data/adjuntos

EXPOSE 8000

CMD ["sh", "-c", "python cli.py init-db && python cli.py serve"]
