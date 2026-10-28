# DESIGN.md

UI rules for `web/`. Follow this for any UI work so the app looks and behaves consistently.

## Principles

- **Desktop-first, responsive.** Design the main layout for a laptop or desktop screen. It must still work on a phone (~360px wide): no horizontal scrolling, and touch targets at least 48px on small screens.
- **Dark only.** One theme. No light theme, no theme toggle.
- **High contrast and readable.** Keep text legible and avoid typing where a picker or tap works.
- **Always show state.** Uploads and jobs show progress, and every screen has a loading, empty, and error state.

## Stack (UI)

- Vuetify 3 via the official Nuxt module `vuetify-nuxt-module`, with the `md3` blueprint (`vuetify/blueprints`)
- Icons: Material Design Icons (`@mdi/js`, `mdi-svg` set). Font: Roboto (Vuetify default).
- Map: `maplibre-gl` with one dark basemap style. Its URL comes from runtime config, never hardcoded in components.
- Do not add another UI library, a CSS framework (e.g. Tailwind), or extra fonts without asking.

## Theme

The theme is defined in **one place**: `web/vuetify.config.ts`. These are starting values. Change them there and nowhere else.

```ts
import { md3 } from 'vuetify/blueprints'

export default defineVuetifyConfiguration({
  blueprint: md3,
  theme: {
    defaultTheme: 'dark',
    themes: {
      dark: {
        dark: true,
        colors: {
          background: '#0E1415',
          surface: '#171D1E',
          primary: '#4FD8EB',
          'on-primary': '#00363D',
          error: '#FFB4AB',
          success: '#81C784',
          info: '#4FC3F7',
          warning: '#FFD54F',
        },
      },
    },
  },
})
```

## Rules

- **Colors come from theme tokens only** (`color="primary"`, `bg-surface`, `bg-error`). Never write hex values in components.
- **Spacing and type use Vuetify classes** (`pa-4`, `ga-2`, `text-h6`, `text-body-1`). Stick to the 8px grid. Don't write custom CSS if a prop or class exists.
- **Distress classes are never color alone.** Always pair a marker or chip color with its class label or icon.
- **Distress classes are defined once** (class → label, icon, theme color token) in `web/app/constants/distress.ts`, matching `api/app/domain/distress.py`. Don't redefine them in components. Detections are bounding boxes with class and confidence only (no severity levels).
- **Job status chips** are one shared component: `queued` (neutral), `processing` (info), `done` (success), `failed` (error).
- **Buttons:** one filled `v-btn` (primary action) per screen. Use `tonal` for secondary and `text` for tertiary. Destructive actions need a confirm dialog.

## Layout

- **Desktop (`md` and up):** a navigation rail (`v-navigation-drawer` with `rail`) on the left, the map as the main area, and a side panel for lists, filters, and detection details. Nav items: **Map**, **Uploads**, **Settings**.
- **Phone (below `md`):** the map fills the screen. Navigation becomes a bottom bar (`v-bottom-navigation`), and the side panel becomes a bottom sheet (`v-bottom-sheet`).
- Build each panel's content once as a component and place it in the side panel or the bottom sheet depending on screen size. Don't duplicate screens per device.
- Switch layouts with Vuetify's `useDisplay()`, not with custom media queries.
- Keep map controls clear of the navigation bar and the bottom sheet.
- Clicking or tapping a marker opens its detection in the side panel or bottom sheet. Cluster markers at low zoom.

## Screens

- **Map:** detections colored by distress class, a legend, and a filter by class.
- **Uploads:** the upload button (for video + optional `.SRT`, or drone photos), and a list of uploads with job status and progress.
- **Detection detail:** the frame image with bounding box overlay, distress class, confidence, location, and time.

## Don't

- Don't add a light theme or a theme toggle.
- Don't use hex colors or inline styles in components.
- Don't put the MapLibre map in an SSR-rendered component. It must be client-only.
- Don't add custom animations beyond Vuetify's defaults.
