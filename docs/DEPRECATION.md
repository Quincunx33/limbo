# Deprecation policy

Limbo is 0.9.x. It does not promise a stable 1.0 contract yet.

## Rules once a 1.0 exists

- Breaking changes to `from limbo import Limbo`, documented route decorators, and `limbo.toml` keys require a minor-version note before 2.0 and a major version to remove them.
- A deprecated behavior stays for at least one minor release after the release that marks it deprecated.
- The capability matrix cannot mark a technology `supported` in the same release that deprecates it.

## Current compatibility window

| Behavior | Status |
|---|---|
| `/_pkgfile/node_modules/<path>` | Compatibility alias for `/_limbo_modules/<path>`. Still served. Not scheduled for removal in 0.x. |
| `/_limbo_modules/<path>` | Canonical package URL. |
| Bare imports with no local package | External in production builds, with a warning. Not rewritten to a CDN. |
| `code_splitting=True` | Hard error. Not a deprecated flag; it has never worked. |

Experimental rows (Vue and Svelte dev adapters) can change without a deprecation cycle. Unsupported rows are not a commitment.
