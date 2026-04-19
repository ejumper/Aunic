# Plan 7 — PWA + Polish

## Context
Plans 1–6 shipped a working browser UI: WS server, file explorer, note editor, transcript view, prompt composer with send/cancel/mode/model/permission. Plan 7 is the final slice of the browser-UI v1: make the app installable as an "Add to Home Screen" PWA (especially on iOS), close known mobile layout bugs, and add a Playwright smoke-test harness so future changes don't silently regress the shell.

Per `notes-and-plans/UI/browser-ui/browserUI-overview.md:26` "built to the limitations of ios pwa, since that is the most restrictive" and `:157` Plan 7 bullet: "PWA manifest, iOS edge cases, Playwright smoke tests."

User dropped `aunic-pwa.webp` (iOS home-screen icon) and `aunic-fav.webp` (favicon) at repo root and asked for them to be moved to a folder that can hold future site icons too.

## Scope

**In:**
1. Move `aunic-pwa.webp` and `aunic-fav.webp` from repo root → `web/public/icons/` (new directory, the home for future site icons).
2. Add `web/public/manifest.webmanifest` declaring the PWA (name, icons, standalone, theme color).
3. Rewrite `web/index.html` head: viewport with `viewport-fit=cover`, `theme-color`, iOS meta tags (`apple-mobile-web-app-capable`, `apple-mobile-web-app-title`, `apple-mobile-web-app-status-bar-style`), `<link rel="apple-touch-icon">`, `<link rel="icon">`, `<link rel="manifest">`, `<meta name="description">`.
4. Fix the three `100vh` sites in `web/src/styles/app.css` to `100dvh` (body `:35`, explorer panel `:226`, maximized transcript `:608`).
5. Apply `env(safe-area-inset-*)` to `.app-shell` padding so iOS notch/home-indicator don't overlap content in standalone PWA mode.
6. Gate the `<details>Debug</details>` block and `<RawLog />` in `App.tsx` behind `import.meta.env.DEV` so production builds don't ship debug UI.
7. Add a top-level `ErrorBoundary` component wrapping `<App />` in `main.tsx`, fallback renders error message + reload button.
8. Add Playwright + smoke-test harness: `@playwright/test` dev dep, `web/playwright.config.ts`, `web/e2e/mock-ws-server.ts` (minimal WS mock for hermetic tests), `web/e2e/smoke.spec.ts` (5 tests), `npm run e2e` script.
9. Mark Plans 6 and 7 finished in `notes-and-plans/UI/browser-ui/browserUI-overview.md`.

**Deferred (explicit — scope creep guard):**
- Service worker / offline caching / Workbox / `vite-plugin-pwa` (LAN-only, backend must be reachable; SW caching would mask stale code and complicate the existing WS reconnect loop).
- Visibility-change "stale connection" toast (existing exponential backoff in `web/src/ws/client.ts:51-56` already reconnects silently; add later if users report confusion).
- Keyboard shortcut help overlay (not in Plan 7 bullet; nice-to-have for a later pass).
- Code splitting (897KB bundle is acceptable for LAN v1).
- Python backend serving the SPA's static files (backend stays WS-only; `vite preview` or a future reverse-proxy is the deploy path).
- Token-based device trust / auth (explicitly deferred in `security/security-overview.md`).
- Multi-resolution PNG icon set (single webp covers iOS 14+ and all modern browsers; revisit if older-device support is requested).

## Default policy (flip any at approval)

| # | Decision | Default | Rationale |
|---|---|---|---|
| 1 | Icon folder | `web/public/icons/` | Vite serves `public/` verbatim at root; user can drop more icons alongside. |
| 2 | Icon format | webp only (as provided) | iOS 14+ supports webp apple-touch-icon; one asset covers all targets. |
| 3 | Manifest filename | `manifest.webmanifest` | Correct MIME; Vite serves from `public/`. |
| 4 | App name / short_name | `Aunic` / `Aunic` | Matches brand in header. |
| 5 | Display mode | `standalone` | Chromeless iOS PWA feel. |
| 6 | theme_color / background_color | `#101312` (matches `body` bg) | Single dark color avoids flash on launch. |
| 7 | Icon `purpose` | `any maskable` | Covers iOS home-screen + Android maskable with one asset. |
| 8 | Viewport fix | `100vh → 100dvh` (no `-webkit-fill-available` fallback) | Safari 15.4+ supports `dvh`; user targets modern iOS. |
| 9 | Safe-area targets | horizontal + bottom padding on `.app-shell` | Covers notch + home indicator; top header is not fixed so no top-inset needed. |
| 10 | Debug gate | `import.meta.env.DEV` (build-time, not runtime flag) | Production PWA build drops debug code entirely. |
| 11 | Error boundary fallback | Error message + "Reload" button (no auto-retry) | Keeps UX predictable; user decides when to reload. |
| 12 | Playwright browsers | Chromium desktop + Mobile Safari (webkit, iPhone 14 preset) | Overview requires mobile-first; webkit preset is the closest hermetic iOS sim. |
| 13 | Playwright WS mock port | `8766` (distinct from backend `8765`) | Prevents accidental collision with a running `aunic serve`. |
| 14 | Playwright frontend run | `vite preview` started by Playwright `webServer` | Tests the production bundle, not dev-mode HMR. |

## Backend changes
**None.** Plan 7 is frontend + tooling only. Backend already emits `run_active` on reconnect (`session.py` → `hello` handler in `connection.py:109-118`), which covers the overview's "Run-state hydration" requirement for PWA reconnect.

## Frontend changes

### Files + assets to add
- `web/public/icons/aunic-pwa.webp` (moved from `./aunic-pwa.webp`; delete original)
- `web/public/icons/aunic-fav.webp` (moved from `./aunic-fav.webp`; delete original)
- `web/public/manifest.webmanifest`:
  ```json
  {
    "name": "Aunic",
    "short_name": "Aunic",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#101312",
    "theme_color": "#101312",
    "description": "Aunic browser workspace",
    "icons": [
      {
        "src": "/icons/aunic-pwa.webp",
        "sizes": "512x512",
        "type": "image/webp",
        "purpose": "any maskable"
      }
    ]
  }
  ```
- `web/src/components/ErrorBoundary.tsx` — class component, `componentDidCatch` logs to `console.error`, fallback JSX shows error + a button that calls `location.reload()`.
- `web/playwright.config.ts` — `webServer` starts `npm run preview` with `VITE_AUNIC_WS_URL=ws://127.0.0.1:8766/ws` plus the mock WS server; projects = `chromium`, `Mobile Safari` (use `devices['iPhone 14']` preset).
- `web/e2e/mock-ws-server.ts` — tiny Node script using the `ws` package (new dev dep) that listens on `:8766/ws`, answers `hello` → emits `session_state` with empty model list + workspace_root + `run_active:false`, answers `read_dir` with a two-entry stub (`README.md` + a folder), answers `read_file` with a minimal `FileSnapshotPayload`. Handlers are enough for the smoke tests to traverse the UI without real backend work.
- `web/e2e/smoke.spec.ts` — 5 tests:
  1. App loads and ConnectionBadge reads "Connected".
  2. Manifest is reachable at `/manifest.webmanifest` and parses as JSON.
  3. `apple-touch-icon` link resolves to a 200 OK webp.
  4. FileExplorer renders at least one entry, clicking it mounts the NoteEditor.
  5. PromptComposer is visible and its Send button has `aria-label="Send"` (smoke — doesn't actually submit).

### Files to modify
- `web/index.html` — replace head. Add:
  ```html
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
  <meta name="theme-color" content="#101312" />
  <meta name="description" content="Aunic browser workspace" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-title" content="Aunic" />
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
  <link rel="icon" href="/icons/aunic-fav.webp" type="image/webp" />
  <link rel="apple-touch-icon" href="/icons/aunic-pwa.webp" />
  <link rel="manifest" href="/manifest.webmanifest" />
  ```
- `web/src/main.tsx` — wrap `<App />` in `<ErrorBoundary>`.
- `web/src/App.tsx` — gate `<details className="debug-details">…</details>` and `<RawLog />` behind `import.meta.env.DEV`.
- `web/src/styles/app.css`:
  - line 35: `min-height: 100vh` → `min-height: 100dvh`
  - line 226: `max-height: calc(100vh - 2rem)` → `max-height: calc(100dvh - 2rem)`
  - line 608: `height: calc(100vh - 4rem)` → `height: calc(100dvh - 4rem)`
  - `.app-shell` padding rule: add `padding-left: max(1rem, env(safe-area-inset-left)); padding-right: max(1rem, env(safe-area-inset-right)); padding-bottom: max(1rem, env(safe-area-inset-bottom));` (preserve existing top padding).
- `web/package.json` — add `"preview": "vite preview --port 4173"`, `"e2e": "playwright test"`, devDeps `@playwright/test` + `ws` + `@types/ws`.
- `web/vite.config.ts` — add `preview: { port: 4173 }` block so the preview server has a stable port for Playwright.
- `web/.gitignore` — add `playwright-report/`, `test-results/` (create `.gitignore` if missing).
- `notes-and-plans/UI/browser-ui/browserUI-overview.md:152` — change Plan 6 section to `[status: finished]` if not already, add `[status: finished]` under Plan 7.

### Files to reference (no edits)
- `web/src/ws/client.ts:51-56` — confirms backoff reconnect already exists, Plan 7 relies on it.
- `web/src/state/session.ts:33-39` — confirms `run_active` hydrates from the server's `session_state` on every connect.
- `web/src/components/ConnectionBadge.tsx` — smoke test asserts its "Connected" text.
- `web/src/env.ts:1-4` — `VITE_AUNIC_WS_URL` override is already wired; Playwright uses it.

## Verification

### Automated
```
cd web
npm install                  # pull @playwright/test + ws
npx playwright install chromium webkit   # one-time browser download
npm run lint
npm run build                # tsc typechecks + vite build; confirm manifest + icons land in dist/
npm run e2e                  # runs smoke.spec.ts against vite preview + mock WS
```

### Manual
1. `cd web && npm run build && npm run preview`, open `http://localhost:4173/` in desktop Chrome — DevTools → Application → Manifest reads Aunic with correct icon, no warnings.
2. DevTools → Lighthouse → "Progressive Web App" category — at minimum "installable" passes.
3. Desktop Safari → View Source → confirm `<link rel="apple-touch-icon" href="/icons/aunic-pwa.webp">` is present.
4. iOS device (real or simulator): Safari → load LAN IP of dev machine → Share → "Add to Home Screen" → icon is `aunic-pwa.webp`, tapping the home-screen tile opens in standalone mode (no Safari chrome), status bar is dark-translucent, content doesn't overlap the notch or home indicator.
5. iPhone portrait: scroll the page, verify the transcript-maximized view reaches exactly the visible viewport (no off-screen cut) — confirms `100dvh` fix.
6. iPhone PWA: background the app → wait 30s → reopen. The ConnectionBadge shows "Reconnecting…" briefly then "Connected". Open note still loads. `run_active` flag hydrates correctly (visible as spinner state).
7. Force a component error (e.g., temp throw in a child) → ErrorBoundary fallback shows with a Reload button; clicking Reload recovers.
8. Production build: `grep -r HelloPanel web/dist/assets` — should NOT find the import (dead-code eliminated via `import.meta.env.DEV` gate).
9. `cat web/dist/manifest.webmanifest` — confirm the icon path matches the served URL.
10. `curl -I http://localhost:4173/icons/aunic-pwa.webp` — 200 OK, `Content-Type: image/webp`.
11. Root repo check: `ls /home/ejumps/HalfaCloud/Aunic/aunic-pwa.webp` should fail (file moved, not copied).
12. Playwright: `npm run e2e -- --project="Mobile Safari"` — all 5 smoke tests pass under webkit.

## Done when
- [ ] Icons live at `web/public/icons/` and repo root no longer contains them.
- [ ] `web/public/manifest.webmanifest` exists and `npm run build` copies it into `web/dist/`.
- [ ] `web/index.html` has the viewport + theme-color + iOS meta tags + 3 icon links.
- [ ] All three `100vh` sites in `app.css` read `100dvh`; `.app-shell` has safe-area paddings.
- [ ] `<HelloPanel />` / `<RawLog />` only render in dev.
- [ ] `ErrorBoundary` wraps `<App />` in `main.tsx`.
- [ ] `npm run e2e` passes under both Chromium and Mobile Safari projects.
- [ ] `browserUI-overview.md` marks Plan 7 as `[status: finished]` (and Plan 6 too, if not already).
