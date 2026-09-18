# Mockup / prototype template

Fixed skeleton `simo-dev:grill-me` (Design mode) substitutes content into when a task
is UX/UI-facing, writing the result to `docs/specs/YYYY-MM-DD-<topic>-mockup.html`.
Self-contained, no CDN dependency (unlike `lib/preview_widget.html`, which needs
`mermaid.js`) — every file produced from this skeleton works fully offline.

Never call the visualize `read_me` tool to obtain this kind of guidance instead of
using this file — forbidden plugin-wide, see `lib/tests/test_no_read_me_precondition.py`.

Two placeholders, same idiom as `preview_widget.html`'s `__MERMAID__`:

- `__TITLE__` — the recommended approach's one-line name.
- `__BODY__` — the actual mockup markup. Preserve newlines when substituting.

## Static mockup (no interaction)

`__BODY__` is plain HTML/CSS, no `<script>` needed.

## Interactive prototype (a flow/state to test)

`__BODY__` additionally includes a `<script>` block with plain `onclick`/
`addEventListener` handlers toggling a CSS class (e.g. hiding/showing a `.step-N`
block) to simulate the flow. No framework, no build step — vanilla DOM only.

## Multi-variant comparison

When the recommended approach has visual variants to compare side by side (e.g. an
A/B/C icon/layout test), wrap each variant in a `.variant-card` inside the skeleton's
`.variant-grid` container, instead of writing separate files. Add `class="selected"` to
the one variant grill-me is recommending.

## Skeleton

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>__TITLE__</title>
<style>
  :root {
    --font-sans: ui-sans-serif, system-ui;
    --text-muted: #71717a;
    --mockup-bg: #fafafa;
    --mockup-fg: #18181b;
    --mockup-accent: #2f6f62;
    --mockup-border: #d4d4d8;
  }
  body { font-family: var(--font-sans); background: var(--mockup-bg); color: var(--mockup-fg); margin: 0; padding: 24px; }
  .variant-grid { display: flex; gap: 16px; flex-wrap: wrap; }
  .variant-card { border: 2px solid var(--mockup-border); border-radius: 8px; padding: 24px; min-width: 220px; text-align: center; }
  .variant-card.selected { border-color: var(--mockup-accent); background: var(--mockup-accent); color: #fff; }
  .variant-card .icon { font-size: 32px; }
  .variant-card .muted { color: var(--text-muted); font-size: 13px; }
  .variant-card.selected .muted { color: rgba(255,255,255,0.8); }
</style>
</head>
<body>
__BODY__
</body>
</html>
```
