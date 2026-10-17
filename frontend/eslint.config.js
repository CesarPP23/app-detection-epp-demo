import js from "@eslint/js";
import globals from "globals";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: [
      "dist",
      "node_modules",
      "src/api/schema.d.ts",
      "playwright-report",
      "test-results",
      "public",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.strict,
  jsxA11y.flatConfigs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: { globals: { ...globals.browser } },
    plugins: { "react-hooks": reactHooks },
    rules: {
      "jsx-a11y/aria-role": ["error", { ignoreNonDOM: true }],
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      "@typescript-eslint/no-non-null-assertion": "error",
    },
  },
  { files: ["**/*.test.{ts,tsx}"], rules: { "@typescript-eslint/no-non-null-assertion": "off" } },
  { files: ["*.config.{ts,js}", "scripts/**/*.mjs"], languageOptions: { globals: { ...globals.node } } },
);
