import '@testing-library/jest-dom';

// Polyfill window.fetch for vitest jsdom environment
if (!globalThis.fetch) {
  // @ts-ignore
  globalThis.fetch = (() =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({}),
    })) as unknown as typeof fetch;
}
