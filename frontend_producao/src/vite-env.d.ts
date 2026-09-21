/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_DEFAULT_OS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

