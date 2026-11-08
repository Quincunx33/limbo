# Migration

## 0.9.0 → 0.9.1

No Python API was removed.

- Generated import maps now point at `/_limbo_modules/...` instead of `/_pkgfile/node_modules/...`. Hand-written `/_pkgfile/` URLs keep working for files the graph already allows.
- `enable_frontend` accepts `config`, `module_namespace`, `jsx_import_source`, `jsx_runtime`, and `allow_remote`.
- `limbo build` copies installed local ESM dependencies into `dist/` and rewrites those imports. A bare import with no `node_modules` entry stays external and is listed on the manifest, same as before.
- CommonJS packages are transformed only when the project has esbuild. They are not silently served as CommonJS and they are not replaced with a CDN URL.

## Moving an app onto Limbo

1. Keep the Python process as the origin. Routes you write on `Limbo` replace the previous framework's routes; there is no automatic converter.
2. Put browser source under `frontend/` and mount it with `enable_frontend`.
3. Install framework packages in that frontend project yourself.
4. Run `limbo build` for browser-native output. Do not point production at the dev prefix.

## Not a migration path

PostgreSQL, MySQL, Next, Nuxt, SvelteKit, Remix, Angular, Astro and Qwik do not have an adapter to migrate onto. The capability matrix is the list.
