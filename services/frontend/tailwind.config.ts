import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        card: "hsl(var(--card))",
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        border: "hsl(var(--border))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        // Grading feedback. Not raw green/red: these are the same hues the
        // progress view uses, so "correct" reads consistently across screens.
        success: {
          DEFAULT: "hsl(var(--success))",
          foreground: "hsl(var(--success-foreground))",
        },
        danger: {
          DEFAULT: "hsl(var(--danger))",
          foreground: "hsl(var(--danger-foreground))",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      // Study-screen motion. Hand-rolled rather than adding Framer Motion: every
      // one of these is a transform, which the browser composites on the GPU, and
      // the library would add ~30KB to a bundle with no animation dependency today.
      keyframes: {
        // A card arriving at center stage: grows from small while travelling in
        // from the pile's side of the screen.
        "card-grow": {
          "0%": { transform: "translateX(18%) scale(0.42)", opacity: "0" },
          "60%": { opacity: "1" },
          "100%": { transform: "none", opacity: "1" },
        },
        // An answered card leaving. Symmetric with the entrance and carrying no
        // directional meaning — the grade is already stated on the card face.
        "card-shrink": {
          "0%": { transform: "none", opacity: "1" },
          "100%": { transform: "scale(0.42)", opacity: "0" },
        },
      },
      animation: {
        "card-grow": "card-grow 0.45s cubic-bezier(0.2, 0.8, 0.25, 1)",
        "card-shrink": "card-shrink 0.45s cubic-bezier(0.4, 0, 0.7, 0.2) forwards",
      },
    },
  },
  plugins: [],
};

export default config;
