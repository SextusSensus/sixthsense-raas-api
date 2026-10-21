# RAAS API

Backend for hourly rentals of the Sixth Sense Labs Booster K1: Phantom sign-in, USDC-on-Solana
checkout verified server-side, a per-booking AES-GCM file vault that is crypto-shredded after
`RESULTS_TTL_HOURS`, an operator (admin) API and the robot job API. FastAPI + SQLite, one process,
one instance. The renter site lives separately at https://sixthsense-raas.higgsfield.app and proxies
`/api/*` here; big transfers go straight to this server on signed `/direct/*` links.

## Local

```
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
copy .env.example .env        # fill in the secrets; for plain http set SECURE_COOKIE=0 and
                              # PUBLIC_API_URL=http://127.0.0.1:8000 (else signed links point at prod)
.venv/Scripts/python -m uvicorn server:app --env-file .env --port 8000
```

`.claude/launch.json` (`raas-api`) runs the same command for the preview pane; it needs that `.env`,
since the server refuses to start without its secrets.

Tests (no network, no secrets needed): `.venv/Scripts/python test_core.py` prints `RAAS-CORE-OK`.

## Deploy to Render

1. New Blueprint from this repo; `render.yaml` defines the `raas-api` web service, the 10 GB disk at
   `/var/data` and every env var. Fill the `sync: false` secrets in the dashboard
   (`python -c "import secrets;print(secrets.token_hex(32))"` for each of `SESSION_SECRET`,
   `VAULT_MASTER_KEY`, `ADMIN_TOKEN`, `ROBOT_TOKEN`). Back up `VAULT_MASTER_KEY`: without it every
   stored file is unreadable.
2. Add the custom domain `raas-api.sixthsenselabs.xyz` to the service. In Cloudflare DNS create a
   CNAME `raas-api` -> the service's `*.onrender.com` host with the proxy **OFF** (DNS only, grey
   cloud): the Cloudflare proxy caps request bodies at 100 MB and times out at 100 s, which breaks
   the direct uploads and downloads. Render issues the TLS certificate.
3. On the Higgsfield site set the `RAAS_API_URL` secret to `https://raas-api.sixthsenselabs.xyz` so
   the Worker proxies `/api/*` here. The health check is `GET /api/config`.
4. Open the operator console at `https://raas-api.sixthsenselabs.xyz/admin.html`, sign in with a wallet
   listed in `ADMIN_WALLETS` (or paste `ADMIN_TOKEN`), and save the weekly availability grid. **No slots
   appear on the booking site until this is done.**
5. The treasury wallet must already hold a USDC token account (send it any amount of USDC once);
   payments to a wallet without one fail on chain.

## What "erased" means

Purging deletes a booking's blobs and its wrapped key, so the live server can never decrypt that data
again. Render also keeps daily snapshots of the persistent disk (about 7 days), and those snapshots
still contain the encrypted blobs and wrapped keys until they age out. The real erase horizon is
therefore `RESULTS_TTL_HOURS` plus Render's snapshot retention. Keep Render account access tight, and
keep the renter terms in line with this.

## Operating notes

- One instance, one worker (`--workers 1`): SQLite and the per-booking upload locks assume a single
  process.
- The janitor runs every 60 s: it settles payments the browser never reported (by scanning recent
  treasury transactions for the `raas:<id>` memo) before a lapsed hold expires, expires holds after
  10 min + 15 min grace, and shreds completed bookings after `RESULTS_TTL_HOURS`.
- Admin purge is refused for `held`, `paid` and `active` bookings: let a hold expire, mark a paid
  booking refunded, or complete a running session first.
- Only completed rentals show up in `GET /api/admin/peaq-pending`; marking one posted is one-time (409
  on repeat).

## Booking site (`site/`)

`site/app` is the renter-facing booking site: React 19 + TanStack Start, deployed as a Cloudflare Worker
on Higgsfield (https://sixthsense-raas.higgsfield.app). It is a copy of the Higgsfield site repo; deploys
still go through Higgsfield, so copy changes back there (or re-export from there) to keep both in sync.

- `src/components/raas/*`, `src/lib/raas.ts`: booking UI, Phantom sign-in and USDC payment, sessions.
- `src/scroll-scrub-scenes.ts` + `public/assets/world/`: the K1 scroll film (5 seam-exact clips).
- `src/server.ts`: proxies `/api/*` to `RAAS_API_URL` (default `https://raas-api.sixthsenselabs.xyz`).
- `packages/`: Higgsfield-managed vendored packages the template's build needs; leave them as they are.
- `base44/`: builds the drag-in RAAS page for the Base44 site (`../base44/BASE44_INSTRUCTIONS.md`).

## Clients

- Robot agent (`robot_agent.py` on the K1): bearer `ROBOT_TOKEN`; polls `GET /api/robot/jobs`, pulls
  input files of ACTIVE sessions, `PUT /api/robot/bookings/{id}/results`, confirms purge with
  `POST /api/robot/bookings/{id}/purged`. Install files (RAM-disk mount, systemd unit) in `deploy/robot/`.
- peaqOS revenue events: `peaq_events.py` on the operator's peaqOS laptop (stdlib only) pulls
  `GET /api/admin/peaq-pending` with `ADMIN_TOKEN` and runs `peaqos qualify event` per completed
  rental. Dry run by default, `--post` submits, `--selftest` checks it offline. Confirm the flags in
  its `PEAQOS_EVENT` constant against `peaqos qualify event --help` before the first `--post`.
