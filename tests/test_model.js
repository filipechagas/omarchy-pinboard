const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "Model.js"), "utf8");
const model = {};
vm.createContext(model);
vm.runInContext(source, model, { filename: "Model.js" });

test("recognizes URL-like input", () => {
  assert.equal(model.startsLikeUrl("https://example.com"), true);
  assert.equal(model.startsLikeUrl("example.com/path"), true);
  assert.equal(model.startsLikeUrl("localhost:8080/path"), true);
  assert.equal(model.startsLikeUrl("http://[::1]:8080/path"), true);
  assert.equal(model.startsLikeUrl("http://[2001:db8::1]/path"), true);
  assert.equal(model.startsLikeUrl("http://[::ffff:192.0.2.128]/path"), true);
  assert.equal(model.startsLikeUrl("not a link"), false);
  assert.equal(model.startsLikeUrl("https://"), false);
  assert.equal(model.startsLikeUrl("https://not a link"), false);
  assert.equal(model.startsLikeUrl("file://foo.bar"), false);
  assert.equal(model.startsLikeUrl("."), false);
  assert.equal(model.startsLikeUrl("http://example.com:99999"), false);
  assert.equal(model.startsLikeUrl("http://[::1]:99999"), false);
  assert.equal(model.startsLikeUrl("http://[not-ipv6]"), false);
  assert.equal(model.startsLikeUrl("http://[1:2:3]"), false);
  assert.equal(model.startsLikeUrl("http://[::ffff:192.168.001.1]"), false);
  assert.equal(model.startsLikeUrl("http://foo:80:90"), false);
  assert.equal(model.startsLikeUrl("http://::1"), false);
});

test("merges tags without case-insensitive duplicates", () => {
  assert.equal(model.mergeTag("Rust linux", "rust"), "Rust linux");
  assert.equal(model.mergeTag("Rust", "Wayland"), "Rust Wayland");
});

test("keeps tags whose names exist on Object.prototype", () => {
  assert.equal(
    model.mergeSuggestions("", {
      recommended: ["constructor", "toString"],
      popular: ["__proto__"]
    }),
    "constructor toString __proto__"
  );
  assert.deepEqual(
    Array.from(model.autocomplete("con", {}, ["constructor"])),
    ["constructor"]
  );
});

test("autocomplete prioritizes URL suggestions and excludes used tags", () => {
  const result = model.autocomplete("linux ru", {
    recommended: ["rust", "Ruby"],
    popular: ["runtime"]
  }, ["rumor", "linux"]);

  assert.deepEqual(Array.from(result), ["rust", "Ruby", "runtime", "rumor"]);
});

test("completes the final tag token", () => {
  assert.equal(model.completeTag("linux ru", "rust"), "linux rust ");
});

test("validates Pinboard field limits", () => {
  assert.equal(model.validateForm("example.com", "Example", "", "one two"), "");
  assert.equal(model.validateForm("", "Example", "", ""), "URL is required.");
  assert.equal(model.validateForm("example.com", " ", "", ""), "Title is required.");
  assert.equal(
    model.validateForm("example.com", "Example", "", "comma,tag"),
    "Tags cannot contain commas."
  );
});

test("counts queue states", () => {
  const items = [{ status: "pending" }, { status: "failed" }, { status: "pending" }];
  assert.equal(model.queueCount(items, "pending"), 2);
  assert.equal(model.queueCount(items, "failed"), 1);
});
