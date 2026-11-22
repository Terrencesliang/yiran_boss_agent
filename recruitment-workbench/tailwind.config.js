const path = require("path");

/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [path.join(__dirname, "*.html"), path.join(__dirname, "*.js")],
  theme: {
    extend: {
      colors: {
        canvas: "#f7f8fb",
        surface: "#ffffff",
        ink: {
          DEFAULT: "#0b1220",
          soft: "#243049",
          mute: "#5b6880",
          faint: "#8b96ab",
        },
        line: {
          DEFAULT: "#e6e9f0",
          strong: "#d6dbe6",
        },
        brand: {
          50: "#eef3ff",
          100: "#dfe8ff",
          200: "#c7d7fe",
          300: "#a5bcfd",
          400: "#5b86f9",
          500: "#3b6cf6",
          600: "#2f5be0",
          700: "#2749b8",
        },
        ok: { 50: "#e9f8f1", 100: "#d3f2e5", 500: "#12a676", 600: "#0d8f66", 700: "#0a7353" },
        warn: { 50: "#fdf4e3", 100: "#f9e8c8", 500: "#c2810f", 600: "#a2690a", 700: "#855405" },
        bad: { 50: "#fdeef1", 100: "#fadce3", 500: "#d24a63", 600: "#c33a54", 700: "#a02c44" },
        conflict: { 50: "#fdf1e8", 100: "#f9e2d3", 500: "#c4611f", 600: "#b3541e", 700: "#934415" },
        violet: { 400: "#8b5cf6", 500: "#7c4dff" },
      },
      fontFamily: {
        sans: [
          "-apple-system", "BlinkMacSystemFont", "Segoe UI", "Microsoft YaHei",
          "PingFang SC", "Hiragino Sans GB", "sans-serif",
        ],
        mono: ["ui-monospace", "SFMono-Regular", "Cascadia Mono", "Consolas", "monospace"],
      },
      borderRadius: {
        xl2: "14px",
      },
      boxShadow: {
        card: "0 1px 2px rgba(11,18,32,.05)",
        lift: "0 10px 28px -22px rgba(11,18,32,.45)",
        pop: "0 28px 60px -30px rgba(8,14,28,.55)",
      },
      keyframes: {
        "fade-up": {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "none" },
        },
        "sheet-in": {
          from: { opacity: "0", transform: "translateY(10px) scale(.99)" },
          to: { opacity: "1", transform: "none" },
        },
        "drawer-in": {
          from: { opacity: ".4", transform: "translateX(24px)" },
          to: { opacity: "1", transform: "none" },
        },
        "fade": { from: { opacity: "0" }, to: { opacity: "1" } },
        "pulse-soft": {
          "0%, 100%": { opacity: "1", boxShadow: "0 0 0 0 rgba(18,166,118,.35)" },
          "50%": { opacity: ".75", boxShadow: "0 0 0 4px rgba(18,166,118,0)" },
        },
        "drift-a": {
          from: { transform: "translate3d(0,0,0) scale(1)" },
          to: { transform: "translate3d(40px,50px,0) scale(1.15)" },
        },
        "drift-b": {
          from: { transform: "translate3d(0,0,0) scale(1.1)" },
          to: { transform: "translate3d(-40px,-30px,0) scale(.95)" },
        },
        "sheen": {
          from: { transform: "translateX(-130%) skewX(-12deg)" },
          to: { transform: "translateX(130%) skewX(-12deg)" },
        },
        "spin-slow": { to: { transform: "rotate(360deg)" } },
      },
      animation: {
        "fade-up": "fade-up .38s cubic-bezier(.22,.68,.24,1) both",
        "sheet-in": "sheet-in .22s cubic-bezier(.22,.68,.24,1) both",
        "drawer-in": "drawer-in .28s cubic-bezier(.22,.68,.24,1) both",
        "fade": "fade .2s ease-out both",
        "pulse-soft": "pulse-soft 2.6s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
