FROM python:3-alpine

RUN apk add --no-cache iperf3 procps coreutils

WORKDIR /app
COPY loadshaper.py /app/

CMD ["python", "-u", "loadshaper.py"]