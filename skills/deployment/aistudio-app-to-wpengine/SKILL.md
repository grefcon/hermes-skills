---
name: aistudio-app-to-wpengine
description: Use when hosting a Gemini/AI Studio app on WP Engine.
version: 1.0.0
author: Jonathan Alling (grefcon), Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [wpengine, deployment, vite, gemini, aistudio, offline-fallback]
    related_skills: [vite-middleware-allowed-hosts, deployment-architecture-selection]
---

# Hosting a Gemini / AI Studio app on WP Engine

Take an AI Studio–exported full-stack app (Express + Vite + React + `@google/genai`)
from "403 on the live URL, every AI feature 503s" to serving on WP Engine's Node
hosting, keyless. Covers the request-layer host fix, the offline fallback layer,
bounded upstream calls, and install/type defects the export ships with.

## When to Use

- An AI Studio / Gemini app must run on WP Engine's Node host (`<slug>.js.wpenginepowered.com`)
- The live URL answers 403 `Blocked request. This host ("…") is not allowed.`
- The app must work with no `GEMINI_API_KEY` because the platform injects no secrets
- Don't use for: picking a host in the first place (`deployment-architecture-selection`),
  or host-check debugging alone (`vite-middleware-allowed-hosts`)

## Prerequisites

- The repo checked out locally; `gh` authenticated if you will push
- The WP Engine hostname, and access to the platform's server log
- Node + npm; the app's start command is the platform's entry point

## Quick Reference

- Launch keyless: `terminal(command="GEMINI_API_KEY='' AUTO_DEV_API_KEY='' npm run dev")`
- Read the platform log: `terminal(command="…")` on the deploy provider's log view
- Spoofed host check: `curl -s -o /dev/null -w '%{http_code}' -H 'Host: <host>' http://localhost:3000/`
- Free the port: `terminal(command="lsof -ti:3000 | xargs -r kill -9")`
- Keyless sweep: `terminal(command="python3 scripts/smoke_wpengine.py --host-to-spoof <host> --probes probes.json")`
- Lint + compile: `npx tsc --noEmit` and `npm run build`
- Hunt a baked-in key: `search_files(pattern="sk_|AIza")`

## Procedure

1. **Establish which code path the platform actually runs.** Read the platform log
   for the startup banner and any Vite lines. Completion: you can state whether the
   server started in dev (Vite middleware) or production (static `dist/`) mode.
   Why: hosts run the start command with `NODE_ENV` unset, so `tsx server.ts` takes
   its dev branch and Vite's middleware host check is live. A `server.hmr.*`
   deprecation warning or a vite.config warning in the log proves you are on it.

2. **Fix the 403 in the layer Vite actually reads.** Pass `allowedHosts` *inline* to
   `createViteServer()` (snippet in `references/patches.md`); default `true`, allow
   `VITE_ALLOWED_HOSTS` to restrict, and mirror the host into `vite.config.ts` for the
   CLI. Completion: the spoofed-host curl returns 200, not 403.
   Why: in middleware mode the config file's `server` block never reaches the resolved
   config, so the edit the error message tells you to make silently does nothing.

3. **Take the paid API out of the request path.** For every AI endpoint, add a curated
   offline dataset with the *same* response contract and return it when the key is
   absent (`if (!ai) return res.json(fallback(...))`). Completion: keyless sweep is all 2xx.
   Why: match the contract exactly and the frontend needs no changes.

4. **Bound every upstream AI call.** Set the SDK's HTTP timeout (e.g. `@google/genai`
   `httpOptions.timeout`, overridable by env). Completion: with a failing key the
   endpoint answers inside the bound. Why: unbounded, a slow or absent upstream holds
   the request open and the fallback is never reached — the browser just spins.

5. **Make the failure path identical to the no-key path.** Every AI endpoint's `catch`
   returns the same fallback instead of `res.status(500)`. Completion: with a key
   present but failing, the endpoint returns 2xx.
   Why: quota and expired keys otherwise break the app exactly where the fallback was
   meant to save it.

6. **Fix install pins before compiling anything.** Vite 8's `peerOptional` esbuild range
   needs `^0.27 || ^0.28`; exports often pin `^0.25.0` and `npm install` aborts with
   ERESOLVE. Completion: `npm install`, `npx tsc --noEmit`, `npm run build` all exit 0.

7. **Strip any key baked into the export, and tell the user to rotate it.** AI Studio
   exports have shipped live third-party keys as a hardcoded default. Replace with
   `process.env.X || ''`. Completion: searching the key prefix finds nothing, and the
   user has been told to revoke it.
   Why: a committed key is compromised the moment it is committed, whatever the repo
   visibility.

8. **Degrade optional third-party feeds instead of erroring.** A keyless integration
   returns a valid empty payload so the UI shows its empty state; operations that
   genuinely cannot work say so explicitly. Completion: no unexplained 5xx in the sweep.

9. **Fix export type errors; do not delete the writes.** Run lint; when a component
   writes a field the domain type does not declare, add the optional field. Completion:
   `npx tsc --noEmit` exits 0. Why: the write is intent; the type is what is wrong.

10. **Push to the branch the platform deploys, then redeploy.** Server changes only
    take effect on a fresh process start. Completion: the live hostname serves 200 and
    the blocked-request body is gone.

## Pitfalls

- The Vite 403 text **always** says `vite.config.js` — it is hardcoded, whatever your
  real filename is. Do not go create a `.js` config in response to it.
- A stale server on the same port makes a correct fix look broken. Kill it, then confirm
  the listening PID is the process you started (`lsof -i :3000`).
- `innerText` reflects CSS `text-transform`, so case-sensitive DOM assertions give false
  negatives — a Tailwind `uppercase` label reads back uppercased. Compare lowercased.
- Before padding a fallback with every field, check whether the frontend already guards
  optional ones (`if (data.x) …`). Porting only the contract's required fields is less work.
- Keep the AI path behind the key check; a configured key must still use the model.
- `lsof` / `xargs -r` are POSIX. On Windows use `netstat -ano` + `Stop-Process`, or test
  an already-running server with `--base-url`.
- The dev path on a public host is a stopgap. `NODE_ENV=production` + a built `dist/`
  avoids Vite entirely — switch only if the platform runs the build step first, or the
  site 404s.

## Verification

Launch the app keyless, then run `scripts/smoke_wpengine.py` against it. Every check must
pass before you call the conversion done:

- **Keyless sweep** — start the app with the keys forced to empty strings, then probe
  every route; each must answer 2xx, sub-second. dotenv does not override a variable that
  already exists, so an empty value in the environment reproduces a zero-secret host
  without touching `.env`. The script derives a probe skeleton from `server.ts` when you
  have not written one.
- **Failing-key sweep** — relaunch with a real (even bogus) key set. Require bounded 2xx
  and no hang; treat a hang as a failure, never as slowness.
- **Host check** — the spoofed `Host` header returns 200, not the blocked-request body.
- **Build** — `npx tsc --noEmit` and `npm run build` both clean.
- **Deployed** — load the WP Engine hostname and confirm it is the app, not the 403 body.

See `references/patches.md` for the exact edits and a search for any key baked into source.
