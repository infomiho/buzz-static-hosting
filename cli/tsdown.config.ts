import { defineConfig } from 'tsdown'

export default defineConfig({
  entry: 'src/cli.ts',
  format: ['esm'],
  dts: true,
  exports: true,
  publint: true,
  banner: {
    js: '#!/usr/bin/env node',
  },
})
