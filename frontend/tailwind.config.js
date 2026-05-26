/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        primary: "#1a2a3a",
        secondary: "#2c3e50",
        accent: "#3498db",
        win: "#2ecc71",
        draw: "#f1c40f",
        loss: "#e74c3c",
      }
    },
  },
  plugins: [],
}
