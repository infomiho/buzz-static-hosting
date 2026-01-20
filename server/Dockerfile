FROM python:3.12-slim

WORKDIR /app

COPY server.py landing.html ./

RUN mkdir -p /data/sites

VOLUME /data

EXPOSE 8080

CMD ["python", "server.py", "--port", "8080"]
