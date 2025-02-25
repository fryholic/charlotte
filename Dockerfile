FROM python:3.11.10-slim-buster

RUN apt-get update && apt-get install -y \
    ffmpeg

WORKDIR /app


COPY . /home/kjh030529/Pycharm/Mizore_yolo_server/bot

RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

COPY . .


ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERD 1