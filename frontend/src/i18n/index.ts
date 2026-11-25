import i18n from "i18next";
import Backend from "i18next-http-backend";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

export const AvailableLanguages = [
  { label: "English", value: "en" },
  { label: "日本語", value: "ja" },
  { label: "简体中文", value: "zh-CN" },
  { label: "繁體中文", value: "zh-TW" },
  { label: "한국어", value: "ko-KR" },
  { label: "Norsk", value: "no" },
  { label: "Arabic", value: "ar" },
  { label: "Deutsch", value: "de" },
  { label: "Français", value: "fr" },
  { label: "Italiano", value: "it" },
  { label: "Português", value: "pt" },
  { label: "Español", value: "es" },
  { label: "Català", value: "ca" },
  { label: "Türkçe", value: "tr" },
  { label: "Українська", value: "uk" },
];

i18n
  .use(Backend)
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    fallbackLng: "en",
    debug: import.meta.env.NODE_ENV === "development",

    // Explicit supportedLngs prevents 404s for unsupported codes like 'en-US@posix'
    supportedLngs: AvailableLanguages.map((lang) => lang.value),

    // Keep false: true causes 404s for region-specific codes missing from supportedLngs
    nonExplicitSupportedLngs: false,

    interpolation: {
      // React already escapes rendered text, so i18next's default escaping double-escapes
      // interpolated values (``/tmp/foo`` renders as ``&#x2F;tmp&#x2F;foo``).
      escapeValue: false,
    },
  });

export default i18n;
