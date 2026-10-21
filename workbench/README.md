# Workbench

The accountants' review app: React + TypeScript (strict) + Vite, shadcn/ui (Radix + Tailwind,
decision 27), React Router, TanStack Query.

```bash
make web-install   # once: npm ci
make api           # the backend, in another terminal
make web           # http://127.0.0.1:5173
make web-types     # after any backend API change: regenerate src/api/schema.d.ts
make web-lint      # oxlint + TypeScript
make web-test      # Vitest
```

- **API calls** go to `/api/...`; Vite forwards them to the FastAPI server on `127.0.0.1:8000`.
  Both listen on localhost only: there is no login yet (decision 26).
- **API types** (`src/api/schema.d.ts`) are generated from the backend's OpenAPI description,
  never written by hand, so a backend change that breaks the Workbench fails to compile.
- **UI components** live in `src/components/ui/` (shadcn/ui source, ours to change). Add more with
  `npx shadcn@latest add <component>`.
- Fonts are bundled locally (`@fontsource-variable/geist`): the browser loads nothing from outside.
