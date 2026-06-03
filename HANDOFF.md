# pdf2audio — overnight build handoff

State as of the end of this session: **public-SaaS scaffolding complete**, behind Cloudflare Access lockdown, ready for you to flip the gate.

## What's deployed and working

### Backend (PC + NAS)
- Per-user namespaced library: `<data_root>/users/<email>/projects/<name>/<doc_id>/…`
- Cloudflare Access JWT verification middleware (`pdf_reader/auth.py`)
- Rate limits + storage caps (`pdf_reader/limits.py`)
- New API endpoints: `GET /api/me`, `GET /api/usage`, `DELETE /api/projects/{project}/docs/{doc_id}`
- All existing endpoints now require the auth dependency + are user-scoped
- Existing 6 SQL-ambiguity docs migrated into your namespace; verified via `GET /api/projects` returning `user_id: ramtin.sirjani@gmail.com`

Live now under DEV bypass (`PDF_READER_DEV_USER=ramtin.sirjani@gmail.com`). You'll swap to real JWT verification by:
1. Going to Cloudflare → Zero Trust → Access → Applications → click `pdf2audio` → copy the **Application Audience (AUD) Tag**
2. SSHing into the PC and setting `PDF_READER_CF_AUD` in `/etc/systemd/system/pdf-backend.service` to that AUD, then removing the `PDF_READER_DEV_USER` line
3. Same change on the NAS in `/volume1/docker/pdf-reader/docker-compose.yml`
4. Restart both: `sudo systemctl restart pdf-backend` on PC, `docker compose up -d --force-recreate` on NAS

### Frontend (React PWA + uploader page)
- Top-bar **About / donate** modal showing your photo, name, Western affiliation, Ko-fi button, per-user usage bars, sign-out link
- Static uploader page (`pdf2audio.html`) with the same About/donate flow, "signed in as" indicator, link to library
- PWA service-worker still wired through — offline saves continue to work
- All pages: `https://pdf2audio.ca/` (PWA), `/pdf2audio.html` (uploader), `/terms.html`, `/privacy.html`

### Documentation + licensing
- `README.md` — architecture, env vars, repo layout
- `LICENSE` — AGPL-3.0 (full official text)
- `COPYRIGHT` — `Copyright (C) 2026 Ramtin Sirjani`
- `terms.html` + `privacy.html` — **DRAFT** stubs at top of each page, you must review before going public
- `.gitignore` — excludes data, secrets, venv, cloudflared creds

### Git repo
- Initialized at `/home/tin/projects/pdf-reader/.git`, single commit on `main`
- Configured with your name + email
- **NOT pushed** — waiting on you for repo name + visibility decision

## What needs you

| Item | Why | Where |
|---|---|---|
| **Photo** of yourself | About/donate panel | drop at `/home/tin/Downloads/ramtin-sirjani.jpg`; deploy by `cat ramtin-sirjani.jpg \| ssh tin-desktop wsl -d Ubuntu bash -c 'cat > /home/ramti/projects/pdf-reader/backend/static/ramtin.jpg'` (same for NAS at `/volume1/docker/pdf-reader/build/static/ramtin.jpg` + container rebuild) |
| **Ko-fi URL** | Replaces `https://ko-fi.com/REPLACE-ME` in README + HTML | After signup, edit `pdf2audio.html` + `frontend/src/components/AboutPanel.tsx` to set `KOFI_URL` |
| **Cloudflare Access AUD tag** | Real JWT verification (DEV bypass disabled) | Zero Trust → Apps → pdf2audio → overview tab |
| **Public GitHub repo** | Visibility on the open-source code | `gh repo create RamtinSirjani/pdf2audio --public --source=/home/tin/projects/pdf-reader --push` (after you've reviewed the diff) |
| **Cloudflare Access policy update** | Lets the world in | Zero Trust → Access → Policies → `Allowed users` → change to "Allow / Authentication Method: any verified email" (or "Allow / Action: Allow with Google identity") |
| **Legal review of Terms + Privacy** | They're stubs | Read `static/terms.html` + `static/privacy.html`; soften, edit, or get an actual lawyer to look |

## Things to know about the current state

- **Cloudflare Access** is still set to `Allow → ramtin.sirjani@gmail.com only`. The site is locked. Test in incognito: you'll be redirected to a one-time-PIN login.
- **DEV bypass is on**. Any request that survives Cloudflare Access (i.e. comes from a CF-authenticated session OR from the tailnet bypassing Cloudflare) gets treated as `ramtin.sirjani@gmail.com`. This is safe while the gate is locked but **must be removed before opening the gate** (see step in "What's deployed" above).
- The NAS container is rebuilt with new code. Reading via Tailscale (`https://ugreen-nas.tail2a1fd7.ts.net`) still works because Cloudflare Access only fronts pdf2audio.ca, not the tailnet URL.
- Rate-limit caps are intentionally high for you (`50 PDFs/day, 20 GB`); reduce them in the systemd unit / docker-compose for the public default once you open the gate.
- The Windows-side `WSLKeepaliveWatch` task is silent (uses `wscript.exe` + hidden VBS wrapper).
- Both `pdf-backend` and `cloudflared-pdf2audio` are systemd services inside WSL with `Restart=on-failure` — surviving NordVPN cycles without intervention.

## Quick verification commands

```bash
# Backend healthy?
curl https://pdf2audio.ca/api/health     # expect 302 to login (CF Access)
ssh tin-desktop curl.exe http://localhost:8000/api/health   # expect JSON

# Your library intact?
ssh tin-desktop curl.exe http://localhost:8000/api/projects   # expect SQL-ambiguity

# Repo state
cd ~/projects/pdf-reader && git log --oneline
```

## If something's wrong tomorrow

- WSL mirror broken (Windows curl to localhost:8000 hangs): `ssh tin-desktop wsl --shutdown` and let the keepalive watcher relaunch it.
- pdf2audio.ca returns 502: the WSL→Windows mirror is broken AND the WSL cloudflared restart hasn't caught up. Same fix.
- pdf2audio.ca returns 1033: cloudflared service is dead. `ssh tin-desktop wsl -d Ubuntu sudo systemctl restart cloudflared-pdf2audio`.

## Files changed in this session

```
backend/pdf_reader/
  schemas.py       — added user_id to Document, Project; new UserStats model
  library.py       — full rewrite for per-user nested layout + legacy migration
  auth.py          — new — Cloudflare Access JWT verification
  limits.py        — new — per-user rate limits + storage caps (JSON-backed)
  server.py        — all endpoints user-scoped via Depends(require_user)
  pipeline_vllm.py — user_id threaded through parse_doc, synthesize_doc, _sync_to_nas
  pyproject.toml   — added python-jose[cryptography], requests
backend/static/
  pdf2audio.html   — public uploader page with About/donate
  terms.html       — DRAFT Terms of Service
  privacy.html     — DRAFT Privacy Policy
frontend/src/
  App.tsx          — header rename, About button
  components/AboutPanel.tsx  — new modal with bio + Ko-fi + usage
  index.css        — modal + About panel styles
LICENSE            — AGPL-3.0
COPYRIGHT          — your copyright stamp
README.md          — architecture + run instructions
.gitignore
HANDOFF.md         — this file
```

Sleep well.
