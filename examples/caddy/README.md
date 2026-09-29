# Caddy with rate limiting

LibraryOfMyOwn binds to `127.0.0.1:8000` by default and is intended to be used behind a reverse proxy, like [Caddy](https://caddyserver.com/). Caddy provides automatic TLS for LibraryOfMyOwn and forwards the `X-Forwarded-Proto` header, which LibraryOfMyOwn trusts only from `TRUSTED_PROXIES` (default `127.0.0.1`).

Caddy does not include ratelimiting out of the box. You need to compile Caddy with a plugin that supports ratelimiting, such as [mholt/caddy-ratelimit](https://github.com/mholt/caddy-ratelimit).

```bash
go install github.com/caddyserver/xcaddy/cmd/xcaddy@latest
xcaddy build --with github.com/mholt/caddy-ratelimit
sudo install -m 755 ./caddy /usr/local/bin/caddy
```

You can then use the example [Caddyfile](Caddyfile). Replace `your.domain` with your public domain name for LibraryOfMyOwn then:

```bash
sudo caddy run --config examples/caddy/Caddyfile
```

The provided Caddyfile ratelimits the following zones:

| Zone | Path | Limit |
|------|------|-------|
| `login_per_ip` | `/login` | 10 requests / minute / client IP |
| `git_per_ip` | `/git/*` | 30 requests / minute / client IP |

Adjust `events` and `window` for your traffic.