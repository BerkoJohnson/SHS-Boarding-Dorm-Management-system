/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './templates/**/*.html',
    './**/templates/**/*.html',
    './static/**/*.js',
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#f0f7ff',
          100: '#e0effe',
          500: '#0284c7', // Primary Blue
          600: '#0369a1',
          700: '#075985',
          900: '#0c4a6e',
        },
        house: {
          house1: '#ef4444', // Red House
          house2: '#3b82f6', // Blue House
          house3: '#eab308', // Yellow House
          house4: '#22c55e', // Green House
        }
      },
      fontSize: {
        '2xs': ['0.65rem', { lineHeight: '0.85rem' }],
        'xs': ['0.75rem', { lineHeight: '1rem' }],
        'sm': ['0.8125rem', { lineHeight: '1.125rem' }],
        'base': ['0.875rem', { lineHeight: '1.25rem' }],
      }
    },
  },
  plugins: [],
}