FROM python:3.11.11-slim

# 필요한 시스템 패키지 설치
RUN apt-get update && apt-get install -y \
    ffmpeg

WORKDIR /app

COPY requirements.txt /app/

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1