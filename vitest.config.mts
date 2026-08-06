import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: [
      'src/lib/__tests__/**/*.test.ts',
      'src/lib/snow/__tests__/**/*.test.ts',
    ],
    coverage: {
      provider: 'v8',
      include: ['src/lib/snow/**/*.ts'],
      reporter: ['text', 'html'],
    },
  },
});
