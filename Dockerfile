FROM python:3.11.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg

WORKDIR /app


COPY . .

RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt


ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERD 1