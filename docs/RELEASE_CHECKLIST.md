# Release checklist

0.9.0 is an engineering tag, not a 1.0 release. Leave these unchecked until they are actually done.

- [x] Standard-library unit suite covers build, migrations, tunnel redaction/cleanup, SSR lifecycle and the capability matrix.
- [x] Capability matrix refuses blanket framework claims.
- [x] README, status and threat model match the code.
- [x] No silent install of Node, compilers or cloudflared.
- [ ] GitHub Actions green on Linux, Windows and macOS for this commit.
- [ ] Independent review of HTTP parsing, paths, sessions, CSRF, CORS, uploads, WebSockets and tunnel exposure.
- [ ] Real browser smoke for every adapter that is marked supported. Browser-native and WASM fixtures already exist; framework rows are not supported.
- [ ] Reproducible package install verified on a clean machine.
- [ ] Version set to 1.0.0 only after the items above.

Do not tick a box because a document mentions the control.
