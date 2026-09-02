# LibraryOfMyOwn

> This is an archive for myself. An archive of my own, so to speak.

Self-hosted, git-backed reading site for Markdown-formatted creative writing with YAML frontmatter. Written so I can share my writing with my friends so I don't have to compete with all the other writers on AO3 writing fanfiction while I'm writing *wholly original* wish-fufillment.

Update public stories over HTTP with git. Read through your revision history for a story with immersive, unified, and split diff viewers. Have control over visibilty of stories, authorship, and granular suppression of history. Export your stories in PDF format using Pandoc and Typst, or alternatively create the export format of your choosing with your own Python module.

## Requirements

- Python 3.14+
- Optional: Caddy (or another reverse proxy) for HTTPS and rate limiting

PDF export is optional. If you want downloads, point `PDF_SCRIPTS` at a directory of builder modules (see below). The reading site itself needs only Python.

## Setup

```bash
cd /path/to/LibraryOfMyOwn
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`.env` only needs install paths (`DATA_DIR`, `HOST`, `PORT`). Cryptographic secrets are written to `data/secrets.json` on first start.

## Run locally

```bash
source .venv/bin/activate
set -a && source .env && set +a
python -m archive.main
```

Open http://127.0.0.1:8000 and complete **first-run setup** at `/setup` (admin password + public URL).

### Sample data

Seed the local stories repo from bundled fixtures:

```bash
python scripts/seed_sample.py
```

This imports markdown from `fixtures/sample-stories` (override with `STORIES_SOURCE`), adds a few sample commits for history/compare, and publishes `Series/` by default.

Run checks:

```bash
python scripts/smoke_test.py
```

## Push stories

After setup, open **Admin → Site settings** for the git remote URL and **Admin → Security** for the git push password.

```bash
mkdir -p ~/writing-source && cd ~/writing-source
git init
git remote add library https://git:YOUR_GIT_PASSWORD@your.domain/git/stories.git
# Add your .md files, commit, then:
git push -u library main
```

On first push to an empty bare repo, use `main` or `master` as your branch name.

The site reads the branch tip from repo `HEAD` by default. Override in **Admin → Site settings** or with `STORIES_BRANCH` in `.env` if needed.

## Publish

1. Log in at `/login`
2. Go to Admin → Publish works
3. Select files or folders to expose

Nothing is public until you publish it.

## Authorship

In Admin → Authorship, set the default author name for published works, choose per-work primary author (default or earliest git commit), and mask git commit identities with display names.

## Work metadata

Scalar frontmatter fields are stored generically and shown on the work page. `title` and `characters` are handled specially; everything else is discovered from your markdown.

In `data/site.json` (see `data/site.json.example`):

- `site_title` — name shown in the header (default: `Library of My Own`)
- `blurb_fields` — ordered list of frontmatter keys to try for the large summary blurb under the title (default: `["summary"]`)
- `field_order` — optional display order for the metadata list; any other fields sort alphabetically after these

Site-wide options (public URL, privacy toggles, git username) are edited in **Admin → Site settings**.

## PDF builders

By default the app loads builders from [`pdf-scripts/`](pdf-scripts/) in the repo (override with `PDF_SCRIPTS` in `.env`). Each module must define:

- `label` — button text shown on the work page
- `build(input_md, output_pdf, work_dir, *, work=None, author="", rev_label="", blurb_fields=None, title="", work_path="")` — write a PDF to `output_pdf`. LibraryOfMyOwn parses the work once and passes structured `work` (`title`, `fields`, `characters`, `body`). Legacy builders that only read `input_md` still work, but built-in scripts expect `work`.
- `suffix` (optional) — appended to the work filename for the download, e.g. `"-digital-quarter-letter.pdf"`. Defaults to `-{script_id}.pdf`.
- `order` (optional) — sort position on the work page (lower first). Defaults to `0`.

Built-in options (when pandoc and typst are on `PATH`):

| Script | Description |
|--------|-------------|
| `digital` | Quarter-letter, symmetric margins for screen reading |
| `logical` | Quarter-letter reading order with print gutter margins |
| `cutstack` | US Letter cut-and-stack imposition (requires `pdfimpose`) |
| `onecut` | US Letter one-cut fold imposition (requires `pypdf`) |

Install print imposition dependencies with `pip install -r requirements-pdf-print.txt`.

The filename stem (without `.py`) becomes the URL id: `/works/{slug}/pdf/{id}`.

Files whose names start with `_` are ignored. If `PDF_SCRIPTS` is unset and `pdf-scripts/` is missing, the Download row is hidden.

An example minimal builder lives at [`examples/pdf-scripts/plain.py`](examples/pdf-scripts/plain.py).

## HTTPS and rate limiting (Caddy)

The repo `Caddyfile` shows TLS plus per-IP rate limits on `/login` and `/git/*`. It requires a custom Caddy build:

```bash
xcaddy build --with github.com/mholt/caddy-ratelimit
```

See [`examples/caddy/README.md`](examples/caddy/README.md) for details. Run LibraryOfMyOwn on `127.0.0.1:8000`, then:

```bash
sudo caddy run --config ./Caddyfile
```

Caddy obtains Let's Encrypt certificates automatically and forwards `X-Forwarded-Proto` so the app can infer HTTPS for cookies. Optionally set `HTTPS_ENABLED=true` in `.env` to force `Secure` session cookies.

## Raspberry Pi (`/opt/archive`)

On an aarch64 Pi, install everything under `/opt/archive` (app, venv, data, typst, pandoc):

```bash
# Copy or git clone the repo onto the Pi, then:
sudo ./scripts/deploy-pi.sh
```

The script downloads [Typst 0.15.1](https://github.com/typst/typst/releases/download/v0.15.1/typst-aarch64-unknown-linux-musl.tar.xz) and [Pandoc 3.11 arm64](https://github.com/jgm/pandoc/releases/download/3.11/pandoc-3.11-linux-arm64.tar.gz) into `/opt/archive/bin`, creates a system `archive` user, and enables the `archive` systemd unit. Built-in `pdf-scripts/` are copied from the repo; override with `PDF_SCRIPTS_SRC` if needed.

Edit `/opt/archive/app/.env` (from `deploy/pi.env.example`), start the service, then complete `/setup` in the browser. See `deploy/Caddyfile.snippet` for TLS and rate limits in front of the service.

## Git remote URL

```
https://git@your.domain/git/stories.git
```

Use HTTP Basic auth with the git username from **Admin → Site settings** and the push password from **Admin → Security**. Anonymous fetch is disabled.
