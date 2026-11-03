# Capability matrix

Generated from `limbo.capabilities.MATRIX`. `supported` means a test in this repository exercises the behavior. `optional` means a local tool you install yourself. `experimental` means a dev-time adapter has been exercised and production support is not claimed. `native-script` means there is nothing to compile. `unsupported` means do not advertise it.

| Id | Status | Evidence |
|---|---|---|
| html-css-js | supported | Browser-native pass-through and production build. |
| web-components | supported | Ordinary browser JavaScript. |
| wasm | supported | Asset copy and WASM content type. |
| typescript | optional | esbuild when installed. Production build fails closed without it. |
| jsx-tsx | optional | Syntax transform only. |
| react | unsupported | No verified browser runtime integration. |
| preact | unsupported | No fixture. |
| vue | experimental | Optional local SFC compiler in development. |
| svelte | experimental | Optional local SFC compiler. No component-state HMR. |
| solid | unsupported | No adapter. |
| lit | unsupported | No dedicated fixture, so it is not declared supported. |
| alpine | native-script | Serve a script you install. Limbo does not bundle it. |
| htmx | native-script | Serve a script you install. Limbo does not bundle it. |
| angular | unsupported | Compiler not integrated. |
| astro | unsupported | No starter test. |
| qwik | unsupported | No starter test. |
| next | unsupported | Process supervisor only. |
| nuxt | unsupported | Process supervisor only. |
| sveltekit | unsupported | Process supervisor only. |
| remix | unsupported | Process supervisor only. |
| sqlite | supported | Transactions and checksummed migrations. |
| postgres | unsupported | URL rejected. |
| mysql | unsupported | URL rejected. |
| cloudflare-quick-tunnel | optional | Supervises an existing `cloudflared`. |
| cloudflare-named-tunnel | optional | User-supplied config. Delete requires `--confirm`. |

`assert_no_blanket_claim()` fails the suite if a forbidden id is marked supported.
