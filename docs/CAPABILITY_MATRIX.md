# Capability matrix

Generated from `limbo.capabilities.MATRIX`. `supported` means a test in this repository exercises the behavior. `optional` means a local tool you install yourself and a fixture passed. `experimental` means a dev-time adapter has been exercised and production support is not claimed. `native-script` means there is nothing to compile. `unsupported` means do not advertise it.

| Id | Status | Evidence |
|---|---|---|
| html-css-js | supported | Browser-native pass-through and production build. |
| web-components | supported | Custom elements are ordinary browser JavaScript; no compiler is required. |
| wasm | supported | WASM assets are copied and served with a WASM MIME type. The browser instantiates them. |
| typescript | optional | Local esbuild adapter in development. Production build refuses .ts files when esbuild is absent. |
| jsx-tsx | optional | Local esbuild transform only. No framework runtime is bundled. |
| react | optional | Browser dev and production fixtures with a local esbuild CommonJS-to-ESM transform. No React Fast Refresh. |
| preact | optional | Local browser ESM fixture passes dev and production preview. No CDN. |
| vue | experimental | Optional local @vue/compiler-sfc dev adapter. Production SFC emit is not claimed. |
| svelte | experimental | Optional local svelte dev adapter. Svelte component-state HMR is not implemented. |
| solid | unsupported | No Solid adapter or fixture. |
| lit | optional | Local browser ESM fixture passes dev and production preview. No CDN. |
| alpine | native-script | No compiler. Serve a script you install yourself. Limbo does not bundle Alpine. |
| htmx | native-script | No compiler. Serve a script you install yourself. Limbo does not bundle htmx. |
| angular | unsupported | Angular's compiler and build are not integrated. |
| astro | unsupported | No Astro adapter or starter test. |
| qwik | unsupported | No Qwik adapter or starter test. |
| next | unsupported | Local process supervisor exists. Next.js itself is not integration-tested. |
| nuxt | unsupported | Local process supervisor exists. Nuxt itself is not integration-tested. |
| sveltekit | unsupported | Local process supervisor exists. SvelteKit itself is not integration-tested. |
| remix | unsupported | Local process supervisor exists. Remix itself is not integration-tested. |
| sqlite | supported | Serialized connection, transactions, and checksummed migrations. |
| postgres | unsupported | URL scheme is rejected. No driver and no integration suite. |
| mysql | unsupported | URL scheme is rejected. No driver and no integration suite. |
| cloudflare-quick-tunnel | optional | Supervises an existing cloudflared process. Limbo does not download it or implement the protocol. |
| cloudflare-named-tunnel | optional | Runs only with a user-supplied config. Destructive delete requires --confirm. Credentials are redacted from logs. |

`assert_no_blanket_claim()` fails the suite if a forbidden id is marked supported. React and Preact may be `optional`; they must not be marked `supported`.
