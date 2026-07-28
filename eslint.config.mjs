import js from "@eslint/js";
import globals from "globals";

export default [
  {
    files: ["pj_realtime_backend_worker.js", "tests/test_worker_auth.mjs"],
    ...js.configs.recommended,
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
  },
];
