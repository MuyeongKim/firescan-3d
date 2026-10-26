FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
# DXF 안의 한글 문자(방 이름 등)를 바닥 텍스처에 그리기 위한 폰트
RUN apt-get update && apt-get install -y --no-install-recommends fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

VOLUME /data
EXPOSE 8000
# 믿을 앞단 프록시는 .env 의 FORWARDED_ALLOW_IPS 로 정한다(uvicorn 이 직접 읽음, 없으면 127.0.0.1).
# '*' 로 두면 nginx 없이 바로 접속한 사람이 X-Forwarded-For 로 IP 를 속여 횟수 제한을 피할 수 있다.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
