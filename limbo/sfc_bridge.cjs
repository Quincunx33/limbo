"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { createRequire } = require("node:module");

const sourcePath = path.resolve(process.argv[2]);
const source = fs.readFileSync(sourcePath, "utf8");
const projectRequire = createRequire(path.join(process.cwd(), "package.json"));
const framework = path.extname(sourcePath).toLowerCase();
let sourceMap = null;

function injectStyles(code, styles, id) {
  if (!styles.length) return code;
  const css = JSON.stringify(styles.join("\n"));
  const elementId = JSON.stringify(`limbo-style-${id}`);
  return `${code}\n;(()=>{if(typeof document==="undefined")return;let e=document.getElementById(${elementId});if(!e){e=document.createElement("style");e.id=${elementId};document.head.appendChild(e)}e.textContent=${css}})();\n`;
}

if (framework === ".vue") {
  const { parse, compileScript, compileTemplate, compileStyle } = projectRequire("@vue/compiler-sfc");
  const id = crypto.createHash("sha1").update(sourcePath).digest("hex").slice(0, 8);
  const parsed = parse(source, { filename: sourcePath });
  if (parsed.errors.length) throw parsed.errors[0];
  const descriptor = parsed.descriptor;
  const scoped = descriptor.styles.some((style) => style.scoped);
  const scopeId = `data-v-${id}`;
  let code;
  if (descriptor.scriptSetup) {
    const compiled = compileScript(descriptor, {
      id,
      inlineTemplate: true,
      templateOptions: { scoped, compilerOptions: scoped ? { scopeId } : {} },
    });
    code = compiled.content.replace(/export\s+default\s+/, "const __limbo_sfc__ = ");
    sourceMap = compiled.map || null;
    code += `\n__limbo_sfc__.__hmrId=${JSON.stringify(id)};`;
    if (scoped) code += `\n__limbo_sfc__.__scopeId=${JSON.stringify(scopeId)};`;
    code += "\nexport default __limbo_sfc__;";
  } else {
    let script = descriptor.script ? descriptor.script.content : "const __sfc__ = {};";
    if (/export\s+default\s+/.test(script)) {
      script = script.replace(/export\s+default\s+/, "const __sfc__ = ");
    }
    const template = descriptor.template
      ? compileTemplate({
          source: descriptor.template.content,
          filename: sourcePath,
          id,
          scoped,
          compilerOptions: scoped ? { scopeId } : {},
        })
      : { code: "export function render(){return null}", errors: [] };
    sourceMap = template.map || null;
    if (template.errors.length) throw template.errors[0];
    const scopedSetup = scoped ? `__sfc__.__scopeId=${JSON.stringify(scopeId)};` : "";
    code = `${script}\n${template.code}\nif(typeof __sfc__!=="undefined"){__sfc__.render=render;__sfc__.__hmrId=${JSON.stringify(id)};${scopedSetup}}\nexport default typeof __sfc__!=="undefined"?__sfc__:{render};`;
  }
  const styles = descriptor.styles.map((style) => {
    if (style.module) throw new Error("Vue CSS modules are not supported by this preview adapter yet");
    const result = compileStyle({
      source: style.content,
      filename: sourcePath,
      id: scopeId,
      scoped: style.scoped,
      preprocessLang: style.lang,
    });
    if (result.errors.length) throw result.errors[0];
    return result.code;
  });
  // The dev server serves ESM directly without bundling.  Vue's package
  // default import is the bundler build, which expects compile-time globals
  // such as `__DEV__`; use the browser runtime for compiled SFC helpers.
  code = code.replace(/from\s+(["'])vue\1/g, 'from $1vue/dist/vue.runtime.esm-browser.js$1');
  code += `\nif(import.meta.hot)import.meta.hot.accept((mod)=>{const r=globalThis.__VUE_HMR_RUNTIME__;if(r&&mod&&mod.default)r.reload(${JSON.stringify(id)},mod.default);});\n`;
  const output = injectStyles(code, styles, id);
  process.stdout.write(process.argv.includes("--json")
    ? JSON.stringify({ code: output, sourceMap })
    : output);
} else if (framework === ".svelte") {
  const { compile } = projectRequire("svelte/compiler");
  const result = compile(source, { filename: sourcePath, generate: "client", dev: true });
  const styles = result.css?.code ? [result.css.code] : [];
  const output = injectStyles(result.js.code, styles, crypto.createHash("sha1").update(sourcePath).digest("hex").slice(0, 8));
  sourceMap = result.js.map || null;
  process.stdout.write(process.argv.includes("--json")
    ? JSON.stringify({ code: output, sourceMap })
    : output);
} else {
  throw new Error(`Unsupported single-file component: ${framework}`);
}
