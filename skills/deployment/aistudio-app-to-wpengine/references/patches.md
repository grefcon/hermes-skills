# The exact patches

Each edit below is the minimum that satisfies one Procedure step. Anchors are written
against a typical AI Studio `server.ts`; assert the anchor count before writing.

## 1. Host check: pass `allowedHosts` inline (Step 2)

```ts
// In the non-production branch of startServer():
const allowedHosts: string[] | true = process.env.VITE_ALLOWED_HOSTS
  ? process.env.VITE_ALLOWED_HOSTS.split(',').map((h) => h.trim()).filter(Boolean)
  : true;

const vite = await createViteServer({
  server: {
    middlewareMode: true,
    allowedHosts,          // <- must be here, NOT only in vite.config.ts
    hmr: false,
    watch: null,
  },
  appType: 'spa',
});
```

And for the `vite dev` / `vite preview` CLIs, `vite.config.ts`:

```ts
server: {
  // ...existing watch/hmr...
  allowedHosts: ['<slug>.js.wpenginepowered.com'],
},
```

The config-file entry is inert for the middleware path — it is not a substitute.

## 2. Bounded AI calls (Step 4)

```ts
const AI_TIMEOUT_MS = Number(process.env.AI_TIMEOUT_MS || 15000);

const ai = apiKey
  ? new GoogleGenAI({
      apiKey,
      httpOptions: {
        headers: { 'User-Agent': 'aistudio-build' },
        timeout: AI_TIMEOUT_MS,   // <- the whole fix
      },
    })
  : null;
```

The SDK's own deadline may fire first and raise `504 DEADLINE_EXCEEDED`; either way the
call rejects inside the bound and reaches the fallback.

## 3. Offline fallback on BOTH paths (Steps 3 and 5)

Replace a `503` guard, **keeping the guard**:

```ts
// before
if (!ai) {
  return res.status(503).json({ error: 'GEMINI_API_KEY is not configured on the server.' });
}

// after
if (!ai) {
  return res.json(carData.getCarOptions(year, make, model));
}
```

Then the same fallback in the catch:

```ts
} catch (error: any) {
  console.warn('Gemini failed, serving offline dataset:', error?.message || error);
  return res.json(carData.getCarOptions(year, make, model));
}
```

Grep the result: `503` should drop to only the intentional ones, `if (!ai) {` count must be
unchanged, and `ai.models.generateContent(` count must be unchanged. If the guard count
drops, you replaced the guard instead of its body and the fallback now runs
unconditionally (TypeScript reports `'ai' is possibly 'null'` at the call site — that error
is the tell).

## 4. Optional third-party key (Step 8)

```ts
// before: a live key shipped in source
const AUTO_DEV_API_KEY = process.env.AUTO_DEV_API_KEY || 'sk_ad_…';

// after
const AUTO_DEV_API_KEY = process.env.AUTO_DEV_API_KEY || '';
```

Then guard the endpoints that need it — a feed returns a valid empty payload:

```ts
if (!AUTO_DEV_API_KEY) {
  return res.json({ listings: [], total: 0, trims: [] });
}
```

…while an operation that genuinely cannot work says so:

```ts
if (!AUTO_DEV_API_KEY) {
  return res.status(503).json({
    error: 'AUTO_DEV_API_KEY is not configured on the server, so VIN decoding is unavailable.',
  });
}
```

Search for anything baked in:

`search_files(pattern="sk_|sk_ad_|AIza|-----BEGIN")`

## 5. Inheritance hazard: an export with two root histories

An AI Studio export and an existing working repo are frequently **unrelated histories**
with the same file paths. A plain merge then conflicts on every shared file (add/add) and
resolves by whichever side you pick — it is a content decision, not a mechanical merge.

Measure before choosing: `git --no-pager diff --stat <other-branch> <this-branch>`.
To adopt the export as the base while keeping history:

```bash
git merge <remote>/main --allow-unrelated-histories -X theirs --no-edit
git rm -r <paths-only-in-the-old-repo>   # files present only on your side survive the merge
git --no-pager diff --stat <remote>/main main   # must now be empty
```

That last diff is the proof: main is a byte-for-byte copy of the export.
