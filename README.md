# Hermes Agent Skills

[![validate](https://github.com/grefcon/hermes-skills/actions/workflows/validate.yml/badge.svg)](https://github.com/grefcon/hermes-skills/actions/workflows/validate.yml)

Skills for [Hermes Agent](https://hermes-agent.nousresearch.com/docs), each written
from a real incident rather than a specification. Every skill states the failure it
fixes, the sequence that actually works, and how to prove the fix held.

## Contents

| Skill | Use it when |
|---|---|
| [`aistudio-app-to-wpengine`](skills/deployment/aistudio-app-to-wpengine/SKILL.md) | An AI Studio / Gemini app must run on WP Engine's Node hosting — 403 on the live URL, every AI feature 503ing |

## Install

```bash
hermes skills install https://raw.githubusercontent.com/grefcon/hermes-skills/main/skills/deployment/aistudio-app-to-wpengine/SKILL.md --category deployment
```

`hermes skills install` accepts any direct URL to a `SKILL.md`; `--category` only
decides which folder it lands in under `~/.hermes/skills/`. Nothing else is needed —
the skill loads itself once its trigger matches a request ("host this on WP Engine",
"Blocked request … is not allowed", "the AI features need an API key").

## aistudio-app-to-wpengine

Takes an exported AI Studio app (Express + Vite + React + `@google/genai`) from
*"403 on the live URL, every AI feature 503s"* to serving on WP Engine with no
secrets configured.

The five defects that bite, in the order they surface:

| Symptom | Actual cause | Fix |
|---|---|---|
| `403 Blocked request. This host ("…") is not allowed.` | Vite's DNS-rebinding host check — and the `server` block in `vite.config.ts` never reaches the resolved config in middleware mode | pass `allowedHosts` **inline** to `createViteServer()` |
| Every AI endpoint returns `503 GEMINI_API_KEY is not configured` | the host injects no secrets | a curated offline dataset that matches the response contract exactly |
| AI requests hang, then return `500` | the upstream call is unbounded, and the `catch` returns 500 instead of the fallback | set `httpOptions.timeout`, and return the same fallback the keyless path uses |
| `npm install` aborts with `ERESOLVE` on esbuild | Vite 8's `peerOptional` range versus an export pinning `^0.25.0` | bump `esbuild` to `^0.28.0` |
| A third-party API key sits in `server.ts` | AI Studio exports have shipped live keys as a hardcoded default | remove it — and rotate the key, because it was committed |

### Verify it

`scripts/smoke_wpengine.py` reproduces all three failure classes against a local
server, keyless, using only the standard library:

```bash
python3 skills/deployment/aistudio-app-to-wpengine/scripts/smoke_wpengine.py \
  --repo /path/to/your/app \
  --host-to-spoof <slug>.js.wpenginepowered.com
```

It launches the app with the API keys forced to empty strings. dotenv will not
override a variable that already exists, so this reproduces a zero-secret host
**without touching `.env`**. It then checks that the site loads, that the platform's
`Host` header is accepted, and that every route answers.

- **FAIL** — a 5xx, or any request that hangs (a hang is a failure, never slowness)
- **WARN** — a 400, i.e. the route is alive but needs a real payload
- A documented exception can be allowed per probe, e.g. a keyless VIN-decode `503`

Exit code `0` means the conversion holds. Supply `--probes probes.json` for real
payloads; without it a skeleton is generated from `server.ts`.

The check was validated in **both** directions: 11/11 pass against a converted app,
and exit `1` with a `403 → FAIL` row when the allowed-host list is deliberately
restricted. A verification step that cannot fail is not a verification step.

## Repository layout

```
skills/<category>/<skill>/
├── SKILL.md          # frontmatter + the procedure
├── references/       # the exact patches, kept out of the body to keep it scannable
└── scripts/          # runnable checks, not prose about checks
tools/validate_skills.py        # the conventions, enforced
.github/workflows/validate.yml  # runs it on every push
```

## Contributing

Skills here follow the [Hermes authoring conventions](https://hermes-agent.nousresearch.com/docs):
a `description` under 60 characters whose opening words are a self-contained trigger,
a `## When to Use` section, and a `## Verification` section with a checkable
criterion. Write lessons, not logs — an imperative rule and its reason, never a
narrative of the incident.

`tools/validate_skills.py` enforces the mechanical parts:

```bash
python3 tools/validate_skills.py skills
```

## License

MIT — see [LICENSE](LICENSE).
