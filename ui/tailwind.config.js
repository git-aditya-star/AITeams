/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      // F-1 toasts: entrance slide, attention ring on the icon (uses currentColor), dismiss countdown bar.
      keyframes: {
        'toast-in': {
          from: { opacity: '0', transform: 'translateX(24px)' },
          to: { opacity: '1', transform: 'translateX(0)' },
        },
        'ring-pulse': {
          '0%': { boxShadow: '0 0 0 0 currentColor' },
          '100%': { boxShadow: '0 0 0 10px transparent' },
        },
        'toast-bar': {
          from: { transform: 'scaleX(1)' },
          to: { transform: 'scaleX(0)' },
        },
      },
      animation: {
        'toast-in': 'toast-in 220ms ease-out both',
        'ring-pulse': 'ring-pulse 1.2s ease-out 2',
        'toast-bar': 'toast-bar 15s linear forwards',
      },
    },
  },
  plugins: [],
}
