"""Run focused browser-independent UI logic checks with Node when available."""
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node is optional for UI logic checks")
class UIRegressions(unittest.TestCase):
    def run_js(self, code):
        result = subprocess.run(["node", "-e", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_safari_poll_visibility_overlap_banner_and_selection(self):
        source = Path("templates/index.html").read_text()
        safari = source[source.index("  let safariPollBusy"):source.index("  // Shared contextual actions")]
        self.run_js(r"""
const assert = require('node:assert/strict');
const elements = new Map();
function element() {
  const buttons = [{}, {}], label = {};
  return { style: {}, setAttribute() {}, querySelector: () => label,
    querySelectorAll: () => buttons, buttons, label };
}
const document = { hidden: true, body: { appendChild(el) { elements.set(el.id, el); } },
  getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); },
  createElement: element, addEventListener() {} };
// A missing banner must be distinguishable from the modal controls.
const get = document.getElementById;
document.getElementById = id => id === 'safari-updates-banner' ? (elements.get(id) || null) : get(id);
let calls = 0, payload = { ok:true, status:'baseline', items:[], count:0 }, resolveFetch;
let fetch = async () => { calls++; return { ok:true, json:async () => payload }; };
let interval;
function setInterval(fn, ms) { assert.equal(ms, 60000); interval = fn; }
let warnings = 0;
const console = { info() { warnings++; } };
let stationCandidates, stationCandidateSource, stationPicked, preview, opens = 0;
function showStationImport() { opens++; }
function renderStationCandidates(meta) { preview = meta; }
""" + safari + r"""
(async () => {
  assert.equal(calls, 0, 'hidden page must not poll');
  document.hidden = false;
  await pollSafariUpdates();
  assert.equal(opens, 0);
  assert.equal(elements.has('safari-updates-banner'), false, 'baseline is silent');
  payload = { ok:true, status:'ready', items:[{url:'https://example.org', title:'Example'}], count:1 };
  await pollSafariUpdates();
  const banner = elements.get('safari-updates-banner');
  assert.equal(banner.hidden, false);
  assert.equal(opens, 0, 'poll must not open preview');
  banner.buttons[0].onclick();
  assert.equal(opens, 1);
  assert.equal(preview.items.length, 1);
  assert.equal(stationPicked.size, 1);
  assert.equal(banner.hidden, true);
  await pollSafariUpdates();
  assert.equal(banner.hidden, true, 'repeat response must not nag');
  payload.items = Array.from({length:31}, (_, i) => ({url:'https://example.org/' + i}));
  await pollSafariUpdates();
  banner.buttons[0].onclick();
  assert.equal(stationCandidates.length, 31);
  assert.equal(stationPicked.size, 0, 'large previews start unselected');
  const before = calls;
  fetch = () => { calls++; return new Promise(resolve => { resolveFetch = resolve; }); };
  const pending = pollSafariUpdates();
  await pollSafariUpdates();
  assert.equal(calls, before + 1, 'requests must not overlap');
  resolveFetch({ok:true, json:async () => ({ok:true, items:[]})});
  await pending;
  fetch = async () => ({ok:false, json:async () => ({ok:false, status:'permission_denied'})});
  await pollSafariUpdates(); await pollSafariUpdates();
  assert.equal(warnings, 1, 'permission errors are logged once');
})().catch(error => { process.stderr.write(error.stack); process.exitCode = 1; });
""")

    def test_doge_pick_path_and_html_escaping(self):
        source = Path("templates/doge.html").read_text()
        escape = source[source.index("function esc(value)"):source.index("function applyTheme")]
        pick = source[source.index("function pickPath("):source.index("function renderGrid()")]
        self.run_js(r"""
const assert = require('node:assert/strict');
let selected = null, message = '';
const document = { querySelectorAll: () => [] };
function toast(value) { message = value; }
""" + escape + pick + r"""
pickPath('/static/doge/stickers/example.webp');
assert.equal(selected, '/static/doge/stickers/example.webp');
assert.match(message, /已选中贴纸/);
pickPath('/static/doge/stickers/example.webp');
assert.equal(selected, null);
assert.equal(esc('<img title="a&b">\''), '&lt;img title=&quot;a&amp;b&quot;&gt;&#39;');
""")

    def test_notification_pause_resume_and_dismiss(self):
        source = Path("templates/index.html").read_text()
        pop = source[source.index("    pop(mood, text, ms = 7000)"):source.index("    // 面板加载态")]
        self.run_js(r"""
const assert = require('node:assert/strict');
let now = 0, next = 0, hovered = false, focused = false, removed = false;
const performance = { now: () => now }, timers = new Map(), handlers = {};
function setTimeout(fn, delay) { const id = ++next; timers.set(id, {fn, delay}); return id; }
function clearTimeout(id) { timers.delete(id); }
const classes = new Set(), close = { addEventListener(name, fn) { this[name] = fn; } };
const el = { classList: { add: x => classes.add(x) }, setAttribute() {},
  addEventListener(name, fn) { handlers[name] = fn; },
  matches() { return hovered; }, contains() { return focused; },
  querySelector() { return close; }, remove() { removed = true; } };
const document = { activeElement:null, createElement: () => el,
  getElementById: () => ({appendChild() {}}) };
const esc = String;
const ui = { path: () => '/face.webp', anim: () => '',
""" + pop + r"""
};
ui.pop('ok', 'Saved');
assert.equal([...timers.values()][0].delay, 7000);
now = 2000; hovered = true; handlers.mouseenter();
assert.equal(timers.size, 0);
focused = true; handlers.focusin();
now = 12000; hovered = false; handlers.mouseleave();
assert.equal(timers.size, 0, 'focus also keeps notification open');
focused = false; handlers.focusout();
const deferred = [...timers.values()][0]; timers.clear(); deferred.fn();
assert.equal([...timers.values()][0].delay, 5000, 'hover duration must not consume lifetime');
close.click();
assert.equal(classes.has('out'), true);
assert.equal(timers.size, 1);
[...timers.values()][0].fn();
assert.equal(removed, true);
""")


if __name__ == "__main__":
    unittest.main()
