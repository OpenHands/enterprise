/** @type {import('tailwindcss').Config} */
import typography from "@tailwindcss/typography";
export default {
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        modal: {
          background: "var(--cool-grey-975)",
          input: "var(--cool-grey-900)",
          primary: "var(--oh-color-primary)",
          secondary: "var(--cool-grey-500)",
          muted: "var(--cool-grey-400)",
        },
        org: {
          border: "var(--cool-grey-975)",
          background: "var(--cool-grey-900)",
          divider: "var(--cool-grey-600)",
          button: "var(--cool-grey-500)",
          text: "var(--cool-grey-400)",
        },
      },
    },
  },
  plugins: [typography],
};
