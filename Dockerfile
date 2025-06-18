FROM python:3.11.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg

RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    libglib2.0-0 \
    libnss3 \
    libgconf-2-4 \
    libfontconfig1

WORKDIR /app

# 로컬 deezspot 디렉토리는 볼륨으로 마운트할 예정

COPY requirements.txt /app/
RUN pip install --cache-dir /root/.cache/pip --upgrade pip \
 && pip install --cache-dir /root/.cache/pip -r requirements.txt \
 && pip install --cache-dir /root/.cache/pip pycryptodome spotipy spotipy-anon tqdm fastapi uvicorn[standard]

# librespot-python 설치 (사용 가능할 경우)
RUN pip install --cache-dir /root/.cache/pip librespot-python || echo "librespot-python not available, continuing..."

COPY . .

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# COPY entrypoint.sh /entrypoint.sh
# RUN chmod +x /entrypoint.sh
# ENTRYPOINT ["/entrypoint.sh"]