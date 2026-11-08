# Troubleshooting

## `Package '…' is not installed`

The import was resolved against this frontend project's `node_modules`, not a global folder and not a CDN. Install it with your package manager. `limbo add` only edits `package.json`.

## `CommonJS module … is not browser ESM`

The package entry is CommonJS. Install esbuild in that same project:

```bash
npm install --save-dev esbuild
```

When running Limbo's browser tests or CLI from a checkout, make sure the local npm binary directory is visible to the process:

```bash
export PATH="$PWD/node_modules/.bin:$PATH"
```

Without this PATH entry, the package may be installed but Limbo's executable discovery can still report `Local esbuild is not installed`; the TypeScript and JSX browser checks will then be skipped.

Limbo will transform the entry locally and keep its package imports on the import map. If esbuild then reports a Node built-in (`fs`, `path`, …), the package is not browser code. Limbo will not fetch a CDN build to hide that.

## `Remote frontend import`

An `http:`, `https:` or `data:` module specifier was found and `allow_remote_imports` is false (the default). Vendor the file or install the package locally.

## TypeScript, JSX, Vue, Svelte

| Symptom | Fix |
|---|---|
| `.ts` / `.tsx` / `.jsx` fails | `npm install --save-dev esbuild` in the frontend project |
| `.vue` fails | `npm install --save-dev @vue/compiler-sfc` and install `vue` |
| `.svelte` fails | `npm install --save-dev svelte` |
| JSX looks for `react/jsx-runtime` but you use Preact | Set `[frontend] jsx_import_source = "preact"` and pass `config=` to `enable_frontend` |

Production build fails closed on those extensions when the compiler is missing. It does not emit the original source as if it were runnable.

## Import map URL changed

Generated maps now use `/_limbo_modules/<package>/<file>` under the frontend prefix (default `/__limbo`). Old `/_pkgfile/node_modules/...` URLs still serve an allowed file. See [Migration](MIGRATION.md).

## Tunnel

- `cloudflared` not found: install the official binary yourself. Limbo will not download it.
- No public URL: the process exited or the log did not contain a URL yet. The redacted log is the source of truth.
- `delete` refused: pass `--confirm`. That flag is required on purpose.

## `limbo.toml`

`limbo config validate` prints the unknown key. `[frontend].runtime_modules` only accepts `local`.
