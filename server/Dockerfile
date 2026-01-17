FROM python:3.12-slim

WORKDIR /app

COPY server.py .

RUN mkdir -p /data/sites

VOLUME /data

EXPOSE 8080

CMD ["python", "server.py", "--port", "8080"]
