/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './templates/**/*.html',
    './ui/**/*.{html,js,ts,tsx}',
    './static/scripts/**/*.{js,ts}'
  ],
  darkMode: ['class', '[data-theme="dark"]'],
  theme: {
    extend: {
      fontFamily: {
        sans: 'var(--font-sans)',
        heading: 'var(--font-heading)',
        mono: 'var(--font-mono)'
      },
      colors: {
        primary: 'var(--color-primary)',
        accent: 'var(--color-accent)',
        surface: 'var(--color-surface)',
        muted: 'var(--color-text-secondary)'
      },
      spacing: {
        1: 'var(--space-1)',
        2: 'var(--space-2)',
        3: 'var(--space-3)',
        4: 'var(--space-4)',
        5: 'var(--space-5)',
        6: 'var(--space-6)',
        7: 'var(--space-7)',
        8: 'var(--space-8)',
        9: 'var(--space-9)',
        10: 'var(--space-10)'
      },
      borderRadius: {
        xs: 'var(--radius-xs)',
        sm: 'var(--radius-sm)',
        md: 'var(--radius-md)',
        lg: 'var(--radius-lg)',
        xl: 'var(--radius-xl)',
        pill: 'var(--radius-pill)'
      },
      boxShadow: {
        sm: 'var(--shadow-sm)',
        md: 'var(--shadow-md)',
        lg: 'var(--shadow-lg)',
        glow: 'var(--shadow-glow)'
      },
      transitionTimingFunction: {
        brand: 'var(--motion-ease)'
      },
      transitionDuration: {
        standard: 'var(--motion-standard)',
        emphasis: 'var(--motion-emphasis)'
      },
      zIndex: {
        header: 'var(--z-header)',
        modal: 'var(--z-modal)',
        toast: 'var(--z-toast)'
      }
    }
  },
  plugins: [
    require('@tailwindcss/forms'),
    require('@tailwindcss/typography'),
  ]
};
