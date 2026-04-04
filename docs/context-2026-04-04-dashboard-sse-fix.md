# Dashboard SSE Fix Context

Date: 2026-04-04

The session goal was to review `docs/prd.md` section 10 against the existing `dashboard/` implementation and fix defects without restructuring or restyling. The main issue found was a runtime mismatch in the live update path: `dashboard/app.py` streamed raw JSON from `/sse/messages`, but the templates were wired for the htmx SSE extension with `sse-swap="message"`. That meant the browser could establish the SSE connection but would not render live message updates correctly because the endpoint was not emitting named SSE events or swap-ready HTML.

The fix changed the SSE endpoint to emit proper `event: message` frames containing HTML fragments that match the existing message markup in the templates. It also added keep-alive comments and `Cache-Control` / `X-Accel-Buffering` headers to make long-lived streams behave more reliably behind buffering proxies. A smaller runtime hardening change was added on the write path so missing `drifter` binaries return a usable `503` error instead of an unhandled server failure.

Validation was lightweight but sufficient for the defect scope: `python3 -m py_compile dashboard/app.py` passed, and FastAPI `TestClient` smoke checks returned `200` for `/`, `/agents`, `/proposals`, and `/login`. End-to-end SSE delivery against real message rows was not exercised in this workspace because `drifter.db` had zero rows in `messages` at the time of verification.

Reusable lesson: when using htmx SSE swapping, verify the server emits the exact event shape the template expects. A healthy SSE connection is not enough; named events and swap-ready payload format must match the frontend wiring or the feature fails silently at runtime.
