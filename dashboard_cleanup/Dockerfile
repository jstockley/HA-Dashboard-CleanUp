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
COPY run.sh /
RUN chmod a+x /run.sh
WORKDIR /app

CMD [ "/run.sh" ]
