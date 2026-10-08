import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    // These existing client-only hydration/reset effects predate the Next 16.4 preset.
    // Keep the compatibility exception confined to the three known persistence files.
    files: [
      "src/app/page.tsx",
      "src/components/ForecastDashboard.tsx",
      "src/components/HistorySection.tsx",
    ],
    rules: { "react-hooks/set-state-in-effect": "off" },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "coverage/**",
    "next-env.d.ts",
    "public/**",
    "legacy/**",
  ]),
]);

export default eslintConfig;
