# RAAS page for sixthsenselabs.xyz (Base44)

`raas-page.zip` holds the booking page as a ready-built bundle: `index.html`, `raas.js`, `raas.css`.
Fonts and the K1 image are inlined, so nothing else has to be uploaded. It is the same booking app as the
Higgsfield build (Phantom sign-in, hourly slots, USDC checkout, sessions with file upload, results and
erase receipts), with a static hero image instead of the scroll film.

## Before it works

The page talks to the booking API at `https://raas-api.sixthsenselabs.xyz`. Until that is deployed
(see `../README.md`, Deploy to Render), the page loads but shows "Booking is offline right now".

## Put it on the RAAS page

1. Open the sixthsenselabs.xyz app in Base44.
2. Drag `raas-page.zip` into the Base44 chat and send this:

   > Replace the content of the Machines page (route /Machines, nav label RAAS) with the page in this zip.
   > It is a pre-built bundle: index.html loads raas.js and raas.css and mounts into a div with id "root".
   > Keep the files exactly as they are, do not rewrite or re-style them, and keep the route /Machines.
   > The page includes its own header and footer, so do not wrap it in the site layout. Do not put it in an
   > iframe: the Phantom wallet does not work inside iframes.

3. Open https://sixthsenselabs.xyz/Machines in your own browser (the one with Phantom) and check:
   - the page loads with no "offline" banner (needs the API live),
   - Book the K1 shows days, times and the price,
   - Connect Phantom opens Phantom and asks you to sign a message (no money moves).

If Base44 puts the page inside an iframe, the page shows "Wallets cannot connect inside this preview".
Ask Base44 to render it directly instead.

## Rebuild after changes

The bundle source is in `../site/app/base44/` and builds from the booking-site code next to it, which
holds the shared components (`site/app/src/components/raas`, `site/app/src/lib/raas.ts`):

```
cd site/app
bun install                                          # npm needs: --legacy-peer-deps, and workspace:* -> *
npx vite build --config base44/vite.config.ts        # writes site/app/dist-base44/
```

Zip `dist-base44/index.html`, `raas.js` and `raas.css` into `raas-page.zip`.

`VITE_RAAS_API` overrides the API host (default `https://raas-api.sixthsenselabs.xyz`); the API must list
the page's origin in `SITE_ORIGINS` (`render.yaml` already includes https://sixthsenselabs.xyz and
https://www.sixthsenselabs.xyz).
