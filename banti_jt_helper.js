const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { TextDecoder, TextEncoder } = require("util");
const nodeCrypto = require("crypto").webcrypto;

const BANTI_SDK_URL = "https://cdn.oreateai.com/static/v1/js/banti_21a851acb0_2025.js?_=247711";

function createStorage() {
  const m = new Map();
  return {
    getItem(k) { return m.has(String(k)) ? m.get(String(k)) : null; },
    setItem(k, v) { m.set(String(k), String(v)); },
    removeItem(k) { m.delete(String(k)); },
    clear() { m.clear(); },
    key(i) { return Array.from(m.keys())[i] || null; },
    get length() { return m.size; },
    _dump() { return Object.fromEntries(m.entries()); },
  };
}

class PluginArray extends Array {}
class MimeTypeArray extends Array {}
function WebGLRenderingContext() {}
WebGLRenderingContext.prototype.getParameter = function getParameter() { return ""; };
function WebGL2RenderingContext() {}
WebGL2RenderingContext.prototype.getParameter = function getParameter() { return ""; };
function Permissions() {}
Permissions.prototype.query = function query() { return Promise.resolve({ state: "prompt" }); };

let iframeContext = null;
function HTMLIFrameElement() {}
Object.defineProperty(HTMLIFrameElement.prototype, "contentWindow", {
  get() { return iframeContext; },
  configurable: true,
});
Object.defineProperty(HTMLIFrameElement.prototype, "srcdoc", {
  get() { return this.__srcdoc || ""; },
  set(v) { this.__srcdoc = String(v); },
  configurable: true,
});

function Document() {}

function createCanvasContext() {
  return {
    getSupportedExtensions() { return []; },
    getExtension() { return null; },
    getParameter() { return ""; },
    isPointInPath() { return false; },
    fillRect() {},
    fillText() {},
    beginPath() {},
    arc() {},
    rect() {},
    closePath() {},
    fill() {},
    set textBaseline(v) {},
    set fillStyle(v) {},
    set font(v) {},
    set globalCompositeOperation(v) {},
  };
}

function createDocument(ctx) {
  const cookies = new Map();
  const doc = Object.create(Document.prototype);
  Object.assign(doc, {
    referrer: "https://www.oreateai.com/home/vertical/aiImage/zh",
    visibilityState: "visible",
    hidden: false,
    compatMode: "CSS1Compat",
    characterSet: "UTF-8",
    body: {
      clientWidth: 1365,
      clientHeight: 768,
      appendChild() {},
      removeChild() {},
      style: {},
      parentNode: null,
    },
    documentElement: {
      clientWidth: 1365,
      clientHeight: 768,
      getAttribute() { return null; },
      hasAttribute() { return false; },
      style: {},
    },
    addEventListener() {},
    removeEventListener() {},
    attachEvent() {},
    detachEvent() {},
    dispatchEvent() { return true; },
    getElementsByTagName(name) { return name === "body" ? [this.body] : []; },
    createEvent() { return { initEvent() {}, initCustomEvent() {} }; },
  });
  doc.createElement = function createElement(tag) {
    const localName = String(tag).toLowerCase();
    const proto = localName === "iframe" ? HTMLIFrameElement.prototype : Object.prototype;
    const el = Object.create(proto);
    Object.assign(el, {
      tagName: localName.toUpperCase(),
      localName,
      nodeType: 1,
      style: {},
      children: [],
      parentNode: doc.body,
      setAttribute(k, v) { this[k] = String(v); },
      getAttribute(k) { return this[k] || null; },
      hasAttribute(k) { return Object.prototype.hasOwnProperty.call(this, k); },
      appendChild(c) { this.children.push(c); return c; },
      removeChild(c) { this.children = this.children.filter((x) => x !== c); },
      getBoundingClientRect() { return { left: 0, top: 0, width: 1, height: 1, right: 1, bottom: 1 }; },
    });
    if (localName === "canvas") {
      el.width = 1;
      el.height = 1;
      el.toDataURL = () => "data:image/png;base64,";
      el.getContext = () => createCanvasContext();
    }
    if (localName === "video") {
      el.canPlayType = () => "";
    }
    return el;
  };
  Object.defineProperty(doc, "cookie", {
    get() { return Array.from(cookies.entries()).map(([k, v]) => `${k}=${v}`).join("; "); },
    set(v) {
      const first = String(v).split(";")[0];
      const idx = first.indexOf("=");
      if (idx >= 0) {
        cookies.set(first.slice(0, idx).trim(), first.slice(idx + 1));
      }
    },
  });
  doc._dumpCookies = function dumpCookies() {
    return Object.fromEntries(cookies.entries());
  };
  return doc;
}

class LocalXHR {
  constructor() {
    this.headers = {};
    this.readyState = 0;
    this.status = 0;
    this.responseText = "";
    this.timeout = 0;
  }

  open(method, url, async = true) {
    this.method = method;
    this.url = url;
    this.async = async;
    this.readyState = 1;
  }

  setRequestHeader(k, v) {
    this.headers[k] = v;
  }

  getAllResponseHeaders() { return ""; }
  getResponseHeader() { return null; }

  async send(data) {
    try {
      const resolved = new URL(this.url, "https://banti.oreateai.com").toString();
      if (process.env.BANTI_DEBUG) {
        process.stderr.write(`[xhr] ${this.method || "POST"} ${resolved} bodyLen=${data ? String(data).length : 0}\n`);
      }
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), Number(this.timeout || 5000));
      const res = await fetch(resolved, {
        method: this.method || "POST",
        headers: {
          "content-type": "text/plain;charset=UTF-8",
          origin: "https://www.oreateai.com",
          referer: "https://www.oreateai.com/",
          ...this.headers,
        },
        body: data,
        signal: controller.signal,
      });
      clearTimeout(timer);
      this.status = res.status;
      this.responseText = await res.text();
      if (process.env.BANTI_DEBUG) {
        process.stderr.write(`[xhr:done] ${this.status} ${this.responseText.slice(0, 240)}\n`);
      }
      this.readyState = 4;
      if (this.onreadystatechange) this.onreadystatechange();
      if (this.onload) this.onload();
    } catch (err) {
      if (process.env.BANTI_DEBUG) {
        process.stderr.write(`[xhr:error] ${err && err.message}\n`);
      }
      if (err && err.name === "AbortError" && this.ontimeout) this.ontimeout(err);
      else if (this.onerror) this.onerror(err);
    }
  }
}

function createContext() {
  const unrefInterval = (fn, ms, ...args) => {
    const timer = setInterval(fn, ms, ...args);
    if (timer && timer.unref) timer.unref();
    return timer;
  };
  const ctx = {
    console: { log() {}, error() {}, warn() {}, groupEnd() {} },
    setTimeout,
    clearTimeout,
    setInterval: unrefInterval,
    clearInterval,
    TextEncoder,
    TextDecoder,
    crypto: nodeCrypto,
    btoa: (s) => Buffer.from(String(s), "binary").toString("base64"),
    atob: (s) => Buffer.from(String(s), "base64").toString("binary"),
    name: "",
    innerWidth: 1365,
    innerHeight: 768,
    outerWidth: 1365,
    outerHeight: 768,
    pageXOffset: 0,
    pageYOffset: 0,
    localStorage: createStorage(),
    sessionStorage: createStorage(),
    location: {
      protocol: "https:",
      host: "www.oreateai.com",
      hostname: "www.oreateai.com",
      href: "https://www.oreateai.com/home/vertical/aiImage/zh",
      origin: "https://www.oreateai.com",
    },
    navigator: {
      userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
      platform: "Win32",
      language: "zh-CN",
      userLanguage: "zh-CN",
      languages: ["zh-CN", "zh"],
      hardwareConcurrency: 8,
      deviceMemory: 8,
      cookieEnabled: true,
      onLine: true,
      maxTouchPoints: 0,
      webdriver: false,
      plugins: new PluginArray(),
      mimeTypes: new MimeTypeArray(),
      permissions: new Permissions(),
      mediaDevices: { enumerateDevices() { return Promise.resolve([]); } },
      javaEnabled() { return false; },
      getBattery() { return Promise.reject(new Error("n/a")); },
    },
    screen: {
      width: 1365,
      height: 768,
      availWidth: 1365,
      availHeight: 728,
      colorDepth: 24,
      pixelDepth: 24,
      deviceXDPI: 96,
      deviceYDPI: 96,
    },
    performance: {
      now() { return Date.now(); },
      memory: { jsHeapSizeLimit: 4294705152 },
    },
    history: { length: 1 },
    CustomEvent: function CustomEvent(type, init) { return { type, detail: init && init.detail }; },
    Event: function Event(type) { return { type }; },
    Document,
    HTMLIFrameElement,
    PluginArray,
    MimeTypeArray,
    WebGLRenderingContext,
    WebGL2RenderingContext,
    Permissions,
    Notification: { permission: "default" },
    indexedDB: undefined,
    openDatabase: undefined,
    WebSocket: undefined,
    RTCPeerConnection: undefined,
    webkitRTCPeerConnection: undefined,
    XMLHttpRequest: LocalXHR,
    XDomainRequest: undefined,
    addEventListener() {},
    removeEventListener() {},
    attachEvent() {},
    detachEvent() {},
    dispatchEvent() { return true; },
    matchMedia() { return { matches: false, addListener() {}, removeListener() {} }; },
  };
  ctx.window = ctx;
  ctx.self = ctx;
  ctx.globalThis = ctx;
  ctx.parent = ctx;
  ctx.top = ctx;
  iframeContext = ctx;
  ctx.document = createDocument(ctx);
  Document.prototype.createElement = function createElement(tag) {
    return ctx.document.createElement(tag);
  };
  return ctx;
}

async function loadBantiSource() {
  const localPath = path.join(__dirname, "banti_raw.js");
  if (fs.existsSync(localPath)) return fs.readFileSync(localPath, "utf8");
  const res = await fetch(BANTI_SDK_URL, {
    headers: {
      "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
      referer: "https://www.oreateai.com/",
    },
  });
  if (!res.ok) throw new Error(`fetch banti sdk failed: ${res.status}`);
  return res.text();
}

async function generateJt({ subid = "", timeoutMs = 8000 } = {}) {
  const source = await loadBantiSource();
  const ctx = createContext();
  vm.createContext(ctx);
  vm.runInContext(source, ctx, { timeout: 10000, filename: "banti_raw.js" });
  const inst = ctx.Banti.create({
    sid: "2146",
    sak: "21a851acb0",
    timeout: 5000,
    autoInit: true,
    reportTimeout: 200,
    bantiOrigin: "https://banti.oreateai.com",
    ymgOrigin: "https://banti.oreateai.com",
  });
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("banti helper timeout")), timeoutMs);
    setTimeout(() => {
      inst.send({ subid }, (err, res) => {
        clearTimeout(timer);
        if (err) reject(err);
        else {
          const cookies = ctx.document && ctx.document._dumpCookies ? ctx.document._dumpCookies() : {};
          resolve({ jt: res && res.htj && res.htj.jt, version: ctx.Banti.VERSION, cookies });
        }
      });
    }, 500);
  });
}

if (require.main === module) {
  generateJt()
    .then((result) => {
      if (!result.jt) throw new Error("missing jt");
      process.stdout.write(JSON.stringify(result));
    })
    .catch((err) => {
      process.stderr.write(String((err && err.stack) || err));
      process.exit(1);
    });
}

module.exports = { generateJt };
