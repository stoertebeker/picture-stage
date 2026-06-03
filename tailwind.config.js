/** @type {import('tailwindcss').Config} */
// Tokens mirror docs/design/tokens.md (Single Source of Truth for v0.5).
module.exports = {
  darkMode: ['selector', '[data-theme="dark"], .dark'],
  content: ['./app/templates/**/*.html'],
  theme: {
    extend: {
      colors: {
        surface: {
          base:    'var(--color-surface-base)',
          raised:  'var(--color-surface-raised)',
          sunken:  'var(--color-surface-sunken)',
          overlay: 'var(--color-surface-overlay)',
        },
        text: {
          primary:   'var(--color-text-primary)',
          secondary: 'var(--color-text-secondary)',
          muted:     'var(--color-text-muted)',
          inverse:   'var(--color-text-inverse)',
          'on-accent': 'var(--color-text-on-accent)',
        },
        border: {
          subtle: 'var(--color-border-subtle)',
          strong: 'var(--color-border-strong)',
          focus:  'var(--color-border-focus)',
        },
        accent: {
          DEFAULT: 'var(--color-accent)',
          hover:   'var(--color-accent-hover)',
          strong:  'var(--color-accent-strong)',
          muted:   'var(--color-accent-muted)',
          ring:    'var(--color-accent-ring)',
        },
        favorite: {
          DEFAULT: 'var(--color-favorite)',
          hover:   'var(--color-favorite-hover)',
          muted:   'var(--color-favorite-muted)',
        },
        status: {
          success: 'var(--color-status-success)',
          warn:    'var(--color-status-warn)',
          danger:  'var(--color-status-danger)',
          info:    'var(--color-status-info)',
        },
      },
      fontFamily: {
        display: ['Fraunces', '"Source Serif 4"', 'Georgia', 'serif'],
        sans:    ['Inter', 'system-ui', '-apple-system', '"Segoe UI"', 'Roboto', 'sans-serif'],
        mono:    ['ui-monospace', '"SF Mono"', '"Cascadia Mono"', 'Menlo', 'monospace'],
      },
      fontSize: {
        xs:   ['0.75rem',  { lineHeight: '1.5' }],
        sm:   ['0.875rem', { lineHeight: '1.5' }],
        base: ['1rem',     { lineHeight: '1.5' }],
        lg:   ['1.25rem',  { lineHeight: '1.4' }],
        xl:   ['1.5rem',   { lineHeight: '1.3' }],
        '2xl':['2rem',     { lineHeight: '1.25' }],
        '3xl':['2.5rem',   { lineHeight: '1.2' }],
        '4xl':['3.5rem',   { lineHeight: '1.1' }],
        '5xl':['4.5rem',   { lineHeight: '1.05' }],
      },
      spacing: {
        '0.5': '0.125rem',
        '1.5': '0.375rem',
        '4.5': '1.125rem',
        '13':  '3.25rem',
        '15':  '3.75rem',
        '18':  '4.5rem',
        '22':  '5.5rem',
        '30':  '7.5rem',
      },
      borderRadius: {
        none: '0',
        sm:   '0.25rem',
        DEFAULT: '0.5rem',
        md:   '0.5rem',
        lg:   '0.75rem',
        full: '9999px',
      },
      boxShadow: {
        sm: 'var(--shadow-sm)',
        DEFAULT: 'var(--shadow-md)',
        md: 'var(--shadow-md)',
        lg: 'var(--shadow-lg)',
      },
      zIndex: {
        base:     '0',
        dropdown: '10',
        sticky:   '20',
        overlay:  '30',
        modal:    '40',
        toast:    '50',
        tooltip:  '60',
      },
      ringColor: {
        focus: 'var(--color-accent-ring)',
      },
      ringWidth: {
        DEFAULT: '3px',
      },
    },
  },
  plugins: [],
};
