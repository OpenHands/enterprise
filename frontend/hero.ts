import { heroui } from "@heroui/react";

export default heroui({
  defaultTheme: "dark",
  layout: {
    radius: {
      small: "5px",
      large: "20px",
    },
  },
  themes: {
    dark: {
      colors: {
        // Build-time defaults; OpenHands-Neo overrides --heroui-* at runtime.
        primary: "#ffffff",

        background: {
          DEFAULT: "#181818", // cool-grey-950
          foreground: "#F7F7F7", // cool-grey-50
        },

        foreground: {
          DEFAULT: "#BEBEBE", // cool-grey-300
          "50": "#101010",
          "100": "#181818",
          "200": "#202020",
          "300": "#282828",
          "400": "#313131",
          "500": "#404040",
          "600": "#565656",
          "700": "#737373",
          "800": "#979797",
          "900": "#BEBEBE",
        },

        content1: { DEFAULT: "#202020", foreground: "#ECECEC" },
        content2: { DEFAULT: "#282828", foreground: "#DCDCDC" },
        content3: { DEFAULT: "#313131", foreground: "#BEBEBE" },
        content4: { DEFAULT: "#404040", foreground: "#979797" },

        focus: {
          DEFAULT: "#ffffff",
        },
        default: {
          "50": "#101010",
          "100": "#181818",
          "200": "#202020",
          "300": "#282828",
          "400": "#313131",
          "500": "#404040",
          "600": "#565656",
          "700": "#737373",
          "800": "#979797",
          "900": "#BEBEBE",
          DEFAULT: "#313131",
          foreground: "#F7F7F7",
        },
      },
    },
  },
});
