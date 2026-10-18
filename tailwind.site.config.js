/**
 * Tailwind configuration for the public Eden Creative Systems website.
 *
 * Kept separate from the application config (`tailwind.config.js`) so the
 * public site's stylesheet does not inherit the dense, dark-mode-aware
 * application layers, and so the application's `output.css` is never touched.
 *
 * Palette note
 * ------------
 * The `eden` colours are sampled from the supplied brand mark
 * (`static/img/eden-creative-systems-mark.png`) rather than invented:
 *   mark blues   #001850 → #002868 → #003888 → #0050b0 → #0060c8
 *   mark greens  #108848 → #289048 → #68b848
 * Gold is a restrained supporting accent, as specified in the brand brief, and
 * is never used for large fills.
 *
 * Every text-bearing pair below meets WCAG AA. `*Bright` tones are for
 * accents, borders and icons only — not for text on white.
 *
 * @type {import('tailwindcss').Config}
 */
module.exports = {
  content: ['./website/templates/**/*.html'],

  /**
   * Classes that are applied from Python rather than written in a template, so
   * Tailwind's content scanner cannot see them:
   *
   *   field-input    `website.forms` widget attributes (every text control)
   *   field-invalid  `website.forms` widget attributes, added only to fields
   *                  that failed validation
   *
   * Chip colour classes are deliberately *not* listed here — the templates
   * write each variant out in full so the scanner finds them.
   */
  safelist: ['field-input', 'field-invalid'],

  theme: {
    extend: {
      colors: {
        eden: {
          navy: '#001a4d',
          navyMid: '#002868',
          navyDeep: '#001233',
          ink: '#16202e',
          slate: '#46586c',

          blue: '#0050b0',
          blueBright: '#0060c8',
          blueDark: '#003888',
          blueInk: '#00306f',
          blueSoft: '#e8f0fa',

          green: '#147a42',
          greenBright: '#289048',
          greenInk: '#0d5c36',
          greenSoft: '#e6f4ec',

          gold: '#8a6a1c',
          goldBright: '#c9a227',
          goldInk: '#74581a',
          goldSoft: '#fbf4e2',

          mist: '#f4f8fc',
          line: '#dbe6f2',
        },
      },
      fontFamily: {
        // Self-hosted system stack: no external font requests, so the
        // project's report-only Content Security Policy remains satisfiable
        // without loosening `font-src` or `style-src`.
        sans: [
          'system-ui',
          '-apple-system',
          'Segoe UI',
          'Roboto',
          'Helvetica Neue',
          'Arial',
          'Noto Sans',
          'sans-serif',
        ],
      },
      maxWidth: {
        prose: '68ch',
      },
      boxShadow: {
        card: '0 1px 2px rgba(0, 26, 77, 0.06), 0 8px 24px -12px rgba(0, 26, 77, 0.18)',
        panel: '0 24px 60px -30px rgba(0, 26, 77, 0.45)',
      },
    },
  },
  plugins: [],
};
