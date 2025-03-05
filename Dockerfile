FROM python:3.11.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --cache-dir /root/.cache/pip --upgrade pip \
 && pip install --cache-dir /root/.cache/pip -r requirements.txt

COPY . .

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
