ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3 py3-pip \
    && pip3 install --no-cache-dir --break-system-packages flask requests websocket-client

COPY app /app
WORKDIR /app

CMD [ "python3", "/app/app.py" ]
