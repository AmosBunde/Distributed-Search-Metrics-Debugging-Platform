import '@testing-library/jest-dom/vitest'

// Recharts measures its container; jsdom reports zero, so charts would render
// nothing at all in tests without a size to work with.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver
