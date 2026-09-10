// A deliberately tiny DOM stand-in for driving the static pages' inline scripts under
// node:test with NO dependencies (no jsdom). It implements exactly what public/*.html's
// vanilla scripts use — createElement/createTextNode/getElementById/querySelectorAll,
// textContent/innerHTML, className/classList, style, dataset, appendChild,
// addEventListener (+ a `dispatch` helper to simulate clicks), select.options/value —
// and nothing more. Text assertions read `.textContent`, which is what the reader sees.
function matches(el, sel) {
  if (sel.startsWith(".")) return (el.className || "").split(/\s+/).includes(sel.slice(1));
  if (sel.startsWith("#")) return el.id === sel.slice(1);
  return el.tagName === sel.toLowerCase();
}

export class Element {
  constructor(tag, doc) {
    this.tagName = tag.toLowerCase(); this.doc = doc; this.children = []; this._text = "";
    this.className = ""; this.style = {}; this.dataset = {}; this.attrs = {}; this._listeners = {};
    this.value = ""; this.options = []; this.parentNode = null;
  }
  get id() { return this._id; }
  set id(v) { this._id = v; this.doc.ids.set(v, this); }
  get classList() {
    const self = this;
    const list = () => (self.className || "").split(/\s+/).filter(Boolean);
    return {
      toggle(c, force) {
        const has = list(); const i = has.indexOf(c);
        const want = force === undefined ? i < 0 : !!force;
        if (want && i < 0) has.push(c); if (!want && i >= 0) has.splice(i, 1);
        self.className = has.join(" "); return want;
      },
      add(c) { this.toggle(c, true); }, remove(c) { this.toggle(c, false); },
      contains(c) { return list().includes(c); },
    };
  }
  appendChild(c) {
    this.children.push(c); c.parentNode = this;
    if (this.tagName === "select" && c.tagName === "option") this.options.push(c);
    return c;
  }
  get textContent() { return this.children.length ? this.children.map((c) => c.textContent).join("") : this._text; }
  set textContent(v) { this.children = []; this.options = []; this._html = undefined; this._text = String(v); }
  get innerHTML() { return this._html !== undefined ? this._html : this.textContent; }
  set innerHTML(v) { this.children = []; this.options = []; this._html = String(v); this._text = String(v).replace(/<[^>]*>/g, ""); }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === "id") this.id = String(v); if (k === "class") this.className = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  addEventListener(ev, fn) { (this._listeners[ev] ||= []).push(fn); }
  dispatch(ev) { for (const fn of this._listeners[ev] || []) fn({ target: this, preventDefault() {} }); }
  querySelectorAll(sel) { return this.doc.all(this).filter((e) => matches(e, sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

class TextNode { constructor(t) { this.textContent = String(t); this.children = []; } }

export class Document {
  constructor() { this.ids = new Map(); this.body = new Element("body", this); }
  createElement(tag) { return new Element(tag, this); }
  createTextNode(t) { return new TextNode(t); }
  getElementById(id) { return this.ids.get(id) || null; }
  all(root = this.body) {
    const out = [];
    const walk = (e) => { for (const c of e.children || []) { if (c instanceof Element) { out.push(c); walk(c); } } };
    walk(root); return out;
  }
  querySelectorAll(sel) { return this.all().filter((e) => matches(e, sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

/** Build a Document carrying every id="…" element of the page's STATIC markup (so the
 *  scripts' getElementById calls resolve), then run each inline <script> with the given
 *  fetch stub. Returns the document once the fetch-driven renders have settled. */
export async function runPage(html, { fetch: fetchStub, ticks = 5 } = {}) {
  const doc = new Document();
  const staticHtml = html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "");
  for (const m of staticHtml.matchAll(/<([a-zA-Z][\w-]*)\b[^>]*\sid\s*=\s*["']([^"']+)["']/g)) {
    const el = doc.createElement(m[1]); el.id = m[2]; doc.body.appendChild(el);
  }
  const scripts = [...html.matchAll(/<script\b(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)].map((m) => m[1]);
  const localStorage = { getItem() { return null; }, setItem() {} };
  for (const code of scripts) {
    new Function("document", "fetch", "localStorage", "window", code)(doc, fetchStub, localStorage, {});
  }
  for (let i = 0; i < ticks; i++) await new Promise((r) => setTimeout(r, 0));
  return doc;
}

export function jsonFetch(routes) {
  // routes: { "/coverage.json": obj, ... } — unknown URLs resolve to an empty ok response
  return async (url) => {
    const key = Object.keys(routes).find((k) => String(url).includes(k));
    const body = key ? routes[key] : {};
    return { ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(body)), text: async () => JSON.stringify(body) };
  };
}
