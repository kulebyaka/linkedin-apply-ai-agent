/**
 * JSDOM unit tests for the content-script DOM primitives (serialize_form /
 * fill_field) against the captured Easy Apply modal fixture.
 *
 * Run directly with:  node --test extension/tests/content_script.test.mjs
 * (also driven by tests/unit/test_extension.py so it runs under `pytest`).
 *
 * jsdom is resolved from the UI workspace's node_modules via createRequire so
 * we don't need a second package.json at the repo root.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import url from 'node:url';

const here = path.dirname(url.fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, '../..');
const uiDir = path.join(repoRoot, 'ui');
const require = createRequire(path.join(uiDir, 'package.json'));
const { JSDOM } = require('jsdom');

const CONTENT_SCRIPT = fs.readFileSync(path.join(repoRoot, 'extension/content_script.js'), 'utf8');
const FIXTURE = fs.readFileSync(path.join(repoRoot, 'tests/fixtures/easy_apply_modal.html'), 'utf8');

function load() {
  const dom = new JSDOM(FIXTURE, { runScripts: 'outside-only' });
  // Execute the content script inside the jsdom window context.
  dom.window.eval(CONTENT_SCRIPT);
  return dom.window.EasyApplyActuator;
}

test('serialize_form surfaces every field kind with labels and options', () => {
  const actuator = load();
  const state = actuator.serializeForm();

  assert.equal(state.flags.modal_present, true);
  assert.equal(state.step, 2);
  assert.equal(state.total, 4);
  assert.match(state.flags.page_text_excerpt, /visa sponsorship/i);

  const byLabel = (re) => state.fields.find((f) => re.test(f.label));

  const first = byLabel(/first name/i);
  assert.ok(first, 'first name field present');
  assert.equal(first.type, 'text');

  const email = byLabel(/email/i);
  assert.equal(email.type, 'email');

  const phone = byLabel(/phone/i);
  assert.equal(phone.type, 'tel');

  const yoe = byLabel(/years of experience/i);
  assert.equal(yoe.type, 'number');
  assert.equal(yoe.required, true);

  const lang = byLabel(/proficiency/i);
  assert.equal(lang.type, 'select');
  assert.ok(lang.options.includes('Native or bilingual'));

  const visa = byLabel(/visa sponsorship/i);
  assert.equal(visa.type, 'radio');
  // Array.from rehydrates the cross-realm (jsdom) array into this realm so
  // strict deepEqual doesn't trip on the differing Array.prototype.
  assert.deepEqual(Array.from(visa.options), ['Yes', 'No']);

  const consent = byLabel(/agree to the terms/i);
  assert.equal(consent.type, 'checkbox');

  const resume = byLabel(/upload resume/i);
  assert.equal(resume.type, 'file');

  const country = byLabel(/country code/i);
  assert.equal(country.type, 'listbox');

  // The follow-company checkbox is part of the form but must never be a
  // selectable text field; it should appear as a checkbox so the server can
  // explicitly leave it / unfollow it.
  const follow = state.fields.find((f) => /follow acme/i.test(f.label));
  assert.ok(follow);
  assert.equal(follow.type, 'checkbox');
});

test('fill_field is blocked until a session is connected (security gate)', async () => {
  const actuator = load();
  const state = actuator.serializeForm();
  const first = state.fields.find((f) => /first name/i.test(f.label));

  const blocked = await actuator.fillField(first.selector, 'Ada');
  assert.ok(blocked.error, 'mutation blocked when not connected');

  actuator._setConnected(true);
  const ok = await actuator.fillField(first.selector, 'Ada');
  assert.equal(ok.filled, true);
});

test('fill_field writes text, select, radio and checkbox values', async () => {
  const actuator = load();
  actuator._setConnected(true);
  const dom = actuator;
  const state = actuator.serializeForm();
  const sel = (re) => state.fields.find((f) => re.test(f.label)).selector;

  await actuator.fillField(sel(/email/i), 'ada@example.com');
  await actuator.fillField(sel(/proficiency/i), 'Native or bilingual');
  await actuator.fillField(sel(/visa sponsorship/i), 'No');
  await actuator.fillField(sel(/agree to the terms/i), true);

  // Re-read to confirm the writes landed.
  const after = actuator.serializeForm();
  const get = (re) => after.fields.find((f) => re.test(f.label));

  assert.equal(get(/email/i).value, 'ada@example.com');
  assert.equal(get(/proficiency/i).value, 'Native or bilingual');
  assert.equal(get(/visa sponsorship/i).value, 'No');
  assert.equal(get(/agree to the terms/i).value, 'checked');
  void dom;
});

test('unfollow_company unticks the follow-company box', async () => {
  const actuator = load();
  actuator._setConnected(true);
  const before = actuator.serializeForm().fields.find((f) => /follow acme/i.test(f.label));
  assert.equal(before.value, 'checked');

  const res = await actuator.unfollowCompany();
  assert.equal(res.unfollowed, true);

  const after = actuator.serializeForm().fields.find((f) => /follow acme/i.test(f.label));
  assert.equal(after.value, '');
});
