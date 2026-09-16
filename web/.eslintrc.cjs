'use strict';

module.exports = {
  root: true,
  env: {
    browser: true,
    es2020: true,
  },
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended-type-checked',
    'plugin:react/recommended',
    'plugin:react-hooks/recommended',
    'plugin:react/jsx-runtime',
  ],
  ignorePatterns: ['dist', '.eslintrc.cjs', 'vite.config.ts', 'postcss.config.js', 'tailwind.config.js'],
  parser: '@typescript-eslint/parser',
  parserOptions: {
    ecmaVersion: 'latest',
    sourceType: 'module',
    project: ['./tsconfig.app.json'],
    tsconfigRootDir: __dirname,
  },
  plugins: ['react-refresh', '@typescript-eslint', 'react'],
  settings: {
    react: {
      version: 'detect',
    },
  },
  rules: {
    'react-refresh/only-export-components': [
      'warn',
      { allowConstantExport: true },
    ],
    '@typescript-eslint/no-explicit-any': 'error',
    '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    'no-console': 'error',
    'react/prop-types': 'off',
    // i18n.ts sets `interpolation: { escapeValue: false }` — t() hands React the
    // raw string and React escapes it as a text child. That is correct, but it
    // is correct ONLY while no innerHTML sink exists anywhere in this tree:
    // the first dangerouslySetInnerHTML added for an unrelated reason silently
    // turns ~2,100 t() calls into stored-XSS sinks, including ones that
    // interpolate a name parsed out of a candidate's uploaded CV.
    //
    // So the invariant is enforced here rather than remembered. If you need raw
    // HTML, sanitise it and disable this rule on that single line with a comment
    // saying why — do not switch it off globally.
    'react/no-danger': 'error',
  },
  overrides: [
    {
      // shadcn/ui primitive files legitimately export both components and
      // variant helpers (buttonVariants, badgeVariants) and hooks (useFormField).
      // Fast-refresh only-export-components is not meaningful here.
      files: ['src/components/ui/**/*.tsx'],
      rules: {
        'react-refresh/only-export-components': 'off',
      },
    },
  ],
};
