# DockerPilot Extras — HA-iX industrial design

## Design reference

The visual language is based on the user's existing **HA-iX Home Control**
Home Assistant dashboard, not the generic Siemens branding or a stock UI kit.

Reference sources on dozeyserver:

- `/home/dozey/dozey-home-ui/lovelace-cards/theme-haix_classic.yaml`
- `/home/dozey/dozey-home-ui/lovelace-cards/haix-shared.js`
- `/home/dozey/dozey-home-ui/static/index.html`
- `/home/dozey/dozey-home-ui/static/responsive.css`

No Home Assistant credentials, entities, source assets, or external JavaScript
dependencies are copied into DockerPilot. The UI remains a native React app
using its existing bundled Nerd Fonts and CSS variables.

## Tokens and layout

| Property | Dark | Light |
| --- | --- | --- |
| Canvas | `#0b1116` | `#f2f5f6` |
| Surface | `#111820` | `#ffffff` |
| Raised surface | `#172129` | `#eaf0f2` |
| Thin dividers | `#29343c` | `#d5dde2` |
| Primary accent | `#00cccc` | `#008b8b` |

The visual hierarchy is a 76 px icon-only left rail plus a flat, compact
topbar, a muted content canvas, and bordered work panels. Narrow viewports use
a 56 px icon rail. Navigation retains accessible names, keyboard focus styles,
current-page semantics, and titles/tooltips.

Key rules:

- No decorative background gradients, animated lifts, neon glows, or
  marketing-style shadows on routine controls.
- Square or 2–3 px corners, 1 px panel dividers, condensed spacing, and
  unambiguous status colors.
- Green/amber/red signify operational state; teal identifies selected actions
  and navigation, not every metric.
- Keep the existing dark/light theme toggle, responsive routing, authentication,
  server switching, actions, and read-only demo behavior.
- Preserve `prefers-reduced-motion` behavior.

## Implementation

- `frontend/src/styles/tokens.css` — shared palette and compatibility aliases
- `frontend/src/styles/shell.css` — rail, header, content layout and breakpoints
- `frontend/src/App.css`, `styles/forms.css` — flat panels and action controls
- `frontend/src/components/AppNavigation.jsx` — accessible semantic icon rail
- `frontend/src/pages/Environments.jsx` — promotion cards without gradients/glows
- `frontend/src/pages/Pipelines.jsx` and `Status.jsx` — theme-aware dropdowns
  and help panel backgrounds

## Validation

Run from `DockerPilotExtras/frontend`:

```sh
npm ci
npm test
npm run build
```

Check both dark and light modes, a wide desktop, mobile viewport, keyboard
navigation, the server selection menu, and the existing CI/CD actions before
deploying. Changes to the running Home Assistant dashboard are out of scope.
