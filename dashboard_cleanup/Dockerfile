FROM ghcr.io/home-assistant/base:latest

ARG BUILD_VERSION
ARG BUILD_ARCH

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

RUN apk add --no-cache python3 py3-pip \
    && pip3 install --no-cache-dir --break-system-packages flask requests websocket-client

COPY app /app
WORKDIR /app

CMD [ "python3", "/app/app.py" ]
