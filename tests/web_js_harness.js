const fs = require("fs");
const vm = require("vm");

const js = fs.readFileSync(process.argv[2], "utf8");

function fakeEl(id) {
  const target = {
    id: id || "",
    open: false,
    value: "",
    textContent: "",
    innerHTML: "",
    innerText: "",
    hidden: false,
    checked: false,
    disabled: false,
    readOnly: false,
    selected: false,
    scrollTop: 0,
    scrollHeight: 0,
    clientHeight: 0,
    offsetWidth: 0,
    offsetHeight: 0,
    files: [],
    children: [],
    childNodes: [],
    dataset: {},
    style: {},
    classList: {
      add() {},
      remove() {},
      toggle() {},
      contains() { return false; },
    },
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() { return true; },
    appendChild(child) { return child; },
    append() {},
    prepend() {},
    insertBefore(child) { return child; },
    removeChild(child) { return child; },
    remove() {},
    replaceChildren() {},
    setAttribute() {},
    removeAttribute() {},
    getAttribute() { return null; },
    hasAttribute() { return false; },
    focus() {},
    blur() {},
    click() {},
    scrollIntoView() {},
    scrollTo() {},
    closest() { return null; },
    matches() { return false; },
    querySelector() { return fakeEl(); },
    querySelectorAll() { return []; },
    getContext() { return {}; },
    toDataURL() { return "data:image/png;base64,"; },
    cloneNode() { return fakeEl(); },
    contains() { return false; },
    animate() {
      return { cancel() {}, finished: Promise.resolve() };
    },
  };
  return new Proxy(target, {
    get(obj, prop) {
      if (prop in obj) return obj[prop];
      if (prop === Symbol.toPrimitive) return () => "";
      if (prop === "then") return undefined;
      return () => fakeEl();
    },
    set(obj, prop, value) {
      obj[prop] = value;
      return true;
    },
  });
}

const elements = new Map();
const document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, fakeEl(id));
    return elements.get(id);
  },
  createElement() { return fakeEl(); },
  createTextNode(text) { return { textContent: text }; },
  querySelector() { return fakeEl(); },
  querySelectorAll() { return []; },
  addEventListener() {},
  removeEventListener() {},
  body: fakeEl("body"),
  documentElement: fakeEl("html"),
  head: fakeEl("head"),
  readyState: "complete",
  title: "",
  cookie: "",
};
document.body.parentNode = document.documentElement;
document.documentElement.parentNode = document;

const storage = {
  getItem() { return null; },
  setItem() {},
  removeItem() {},
  clear() {},
};

let unhandled = null;
process.on("unhandledRejection", (err) => {
  unhandled = err;
});

const sandbox = {
  console,
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  queueMicrotask,
  URLSearchParams,
  URL,
  Map,
  Set,
  WeakMap,
  WeakSet,
  Promise,
  Date,
  Math,
  JSON,
  Intl,
  Number,
  String,
  Boolean,
  Array,
  Object,
  RegExp,
  Error,
  TypeError,
  AbortController,
  TextDecoder,
  TextEncoder,
  Blob,
  FormData,
  location: {
    search: "?token=test-token",
    href: "http://localhost/?token=test-token",
    hash: "",
    pathname: "/",
    host: "localhost",
  },
  localStorage: storage,
  sessionStorage: storage,
  navigator: {
    userAgent: "test",
    clipboard: { writeText() { return Promise.resolve(); } },
  },
  document,
  alert() {},
  confirm() { return true; },
  prompt() { return ""; },
  fetch() { return new Promise(() => {}); },
  requestAnimationFrame() { return 0; },
  cancelAnimationFrame() {},
  getComputedStyle() {
    return { getPropertyValue() { return ""; } };
  },
  matchMedia() {
    return {
      matches: false,
      addListener() {},
      removeListener() {},
      addEventListener() {},
      removeEventListener() {},
    };
  },
  history: { replaceState() {}, pushState() {} },
  scrollTo() {},
  open() {},
  addEventListener() {},
  removeEventListener() {},
  Image: function () { return fakeEl(); },
  FileReader: function () {
    this.readAsDataURL = function () {
      if (this.onload) {
        this.onload({ target: { result: "data:image/png;base64," } });
      }
    };
    this.readAsText = function () {
      if (this.onload) this.onload({ target: { result: "" } });
    };
  },
};
sandbox.window = sandbox;
sandbox.self = sandbox;
sandbox.globalThis = sandbox;

try {
  vm.runInNewContext(js, sandbox, { filename: "benben-web.js" });
} catch (err) {
  console.error("JS_RUNTIME_ERROR");
  console.error(err && err.stack ? err.stack : String(err));
  process.exit(1);
}

// Let immediate microtasks run; the app's startup fetch never resolves.
setTimeout(() => {
  if (unhandled) {
    console.error("JS_UNHANDLED_REJECTION");
    console.error(
      unhandled && unhandled.stack ? unhandled.stack : String(unhandled)
    );
    process.exit(1);
  }
  console.log("JS_RUNTIME_OK");
  process.exit(0);
}, 20);
