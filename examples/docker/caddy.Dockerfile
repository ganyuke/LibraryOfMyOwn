# Caddy with rate limiting, built by docker compose.
FROM docker.io/library/caddy:2-builder AS build
RUN xcaddy build --with github.com/mholt/caddy-ratelimit

FROM docker.io/library/caddy:2
COPY --from=build /usr/bin/caddy /usr/bin/caddy
