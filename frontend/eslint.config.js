import tseslint from "@typescript-eslint/eslint-plugin";
import tsParser from "@typescript-eslint/parser";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import query from "@tanstack/eslint-plugin-query";
import jsxA11y from "eslint-plugin-jsx-a11y";
import importPlugin from "eslint-plugin-import";
import i18next from "eslint-plugin-i18next";
import unusedImports from "eslint-plugin-unused-imports";
import prettierPlugin from "eslint-plugin-prettier/recommended";
import prettierConfig from "eslint-config-prettier";

export default [
  {
    ignores: ["build/**", "dist/**", "node_modules/**", "public/**", "coverage/**"],
    linterOptions: { reportUnusedDisableDirectives: "off" },
  },

  ...tseslint.configs["flat/recommended"],
  tseslint.configs["flat/eslint-recommended"],

  react.configs.flat.recommended,

  jsxA11y.flatConfigs.recommended,
  importPlugin.flatConfigs.recommended,
  importPlugin.flatConfigs.typescript,

  ...query.configs["flat/recommended"],

  i18next.configs["flat/recommended"],

  {
    plugins: {
      "react-hooks": reactHooks,
      "unused-imports": unusedImports,
    },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "off",
      // unused-imports replaces the base rules; keep TS variant off to avoid duplicates
      "no-unused-vars": "off",
      "@typescript-eslint/no-unused-vars": "off",
      "unused-imports/no-unused-imports": "error",
      "unused-imports/no-unused-vars": [
        "error",
        { vars: "all", args: "after-used", ignoreRestSiblings: true, caughtErrors: "none" },
      ],
    },
  },

  prettierPlugin,
  prettierConfig,

  {
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaFeatures: { jsx: true },
        project: "./tsconfig.json",
      },
    },
    settings: {
      react: { version: "detect" },
    },
    rules: {
      "prettier/prettier": "error",
      "i18next/no-literal-string": "error",
      "@typescript-eslint/prefer-optional-chain": "error",
      // airbnb-typescript disables this (TS compiler already checks imports)
      "import/no-unresolved": "off",
      "import/extensions": [
        "error",
        "ignorePackages",
        {
          "": "never",
          ts: "never",
          tsx: "never",
        },
      ],
    },
  },

  {
    files: ["**/*.ts", "**/*.tsx"],
    ignores: ["src/hooks/query/query-keys.ts"],
    rules: {
      "no-param-reassign": [
        "error",
        {
          props: true,
          ignorePropertyModificationsFor: ["acc", "state"],
        },
      ],
      "no-restricted-syntax": [
        "error",
        {
          selector: "Property[key.name='queryKey'] > ArrayExpression[elements.0.value='settings']",
          message: "Use SETTINGS_QUERY_KEYS helpers instead of raw settings query key arrays.",
        },
      ],
      "react/require-default-props": "off",
      "import/prefer-default-export": "off",
      "no-underscore-dangle": "off",
      "jsx-a11y/no-static-element-interactions": "off",
      "jsx-a11y/click-events-have-key-events": "off",
      "jsx-a11y/label-has-associated-control": [
        2,
        {
          required: {
            some: ["nesting", "id"],
          },
        },
      ],
      "react/prop-types": "off",
      "react/no-array-index-key": "off",
      "react/react-in-jsx-scope": "off",
      "import/no-extraneous-dependencies": "off",
    },
  },
];
