/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the API gateway. Empty in production: nginx proxies /api. */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
