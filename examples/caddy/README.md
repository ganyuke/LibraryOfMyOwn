# Caddy with rate limiting

Standard Caddy does not include rate limiting. Build a custom binary with [mholt/caddy-ratelimit](https://github.com/mholt/caddy-ratelimit):

```bash
go install github.com/caddyserver/xcaddy/cmd/xcaddy@latest
xcaddy build --with github.com/mholt/caddy-ratelimit
sudo install -m 755 ./caddy /usr/local/bin/caddy
```

Replace `your.domain` in [Caddyfile](Caddyfile), then:

```bash
sudo caddy run --config examples/caddy/Caddyfile
```

## What the example limits

| Zone | Path | Limit |
|------|------|-------|
| `login_per_ip` | `/login` | 10 requests / minute / client IP |
| `git_per_ip` | `/git/*` | 30 requests / minute / client IP |

Adjust `events` and `window` for your traffic. LibraryOfMyOwn binds to `127.0.0.1:8000`; Caddy terminates TLS and forwards `X-Forwarded-Proto` so the app can infer HTTPS for cookies.

After first visit, complete setup at `/setup`, then set the public URL under **Admin → Site settings**.
