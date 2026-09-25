#!/usr/bin/env node
/* Valide les diagrammes Mermaid du wiki avec le vrai parseur Mermaid.
   Usage : node tools/check_diagrams.js   (npm i d'abord)
   BSD-3-Clause */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { JSDOM } = require("jsdom");

const ROOT = path.resolve(__dirname, "..");
const WIKI = path.join(ROOT, "wiki");
const MERMAID_JS = path.join(ROOT, "web", "assets", "mermaid.min.js");
const MERMAID_RE = /```mermaid[ \t]*\n([\s\S]*?)```/g;

if (!fs.existsSync(MERMAID_JS)) {
  console.error(`absent : ${MERMAID_JS} — lancez \`make site-assets\``);
  process.exit(2);
}
if (!fs.existsSync(path.join(ROOT, "node_modules", "jsdom"))) {
  console.error("absent : node_modules/jsdom — lancez `npm install`");
  process.exit(2);
}

const dom = new JSDOM(
  "<!doctype html><html><body><div id='root'></div></body></html>",
  { pretendToBeVisual: true, url: "https://localhost/" }
);
const w = dom.window;

global.window = w;
global.document = w.document;
global.navigator = w.navigator;
global.location = w.location;
global.HTMLElement = w.HTMLElement;
global.SVGElement = w.SVGElement;
global.Element = w.Element;
global.Node = w.Node;
global.DOMParser = w.DOMParser;
global.XMLSerializer = w.XMLSerializer;
global.getComputedStyle = w.getComputedStyle;
global.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 0);
if (!w.ResizeObserver) {
  w.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  global.ResizeObserver = w.ResizeObserver;
}

vm.runInThisContext(fs.readFileSync(MERMAID_JS, "utf8"), { filename: MERMAID_JS });
const mermaid = global.mermaid || w.mermaid;
if (!mermaid) {
  console.error("mermaid n'a pas exposé de global");
  process.exit(2);
}

const diagrams = [];
for (const file of fs.readdirSync(WIKI).sort()) {
  if (!file.endsWith(".md") || file.startsWith("_")) continue;
  const text = fs.readFileSync(path.join(WIKI, file), "utf8");
  let m;
  MERMAID_RE.lastIndex = 0;
  while ((m = MERMAID_RE.exec(text)) !== null) {
    diagrams.push({ file, src: m[1].replace(/\s+$/, "") });
  }
}

mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "dark" });

(async () => {
  let bad = 0;
  for (const [i, d] of diagrams.entries()) {
    try {
      await mermaid.parse(d.src);
      console.log(`OK   ${d.file} #${i}`);
    } catch (e) {
      bad++;
      const msg = String((e && e.message) || e)
        .split("\n")
        .slice(0, 4)
        .join(" | ");
      console.log(`FAIL ${d.file} #${i} :: ${msg}`);
    }
  }
  console.log(
    bad === 0
      ? `${diagrams.length} diagrammes Mermaid validés`
      : `${bad}/${diagrams.length} diagramme(s) invalide(s)`
  );
  process.exit(bad === 0 ? 0 : 1);
})();
