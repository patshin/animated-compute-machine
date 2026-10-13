FROM caddy:2.11.4-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648
COPY deploy/proxy/Caddyfile /etc/caddy/Caddyfile
RUN addgroup -S -g 10003 agenttrace \
    && adduser -S -D -H -u 10003 -G agenttrace agenttrace \
    && setcap -r /usr/bin/caddy \
    && chown -R 10003:10003 /etc/caddy /data /config
USER 10003:10003
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=4 \
  CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/healthz"]
