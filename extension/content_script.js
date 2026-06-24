/**
 * LinkedIn Apply AI Agent — content script (DOM actuator).
 *
 * This script is a *dumb* DOM actuator. It holds NO application logic and makes
 * NO autonomous decisions: the server orchestrates the whole Easy Apply flow
 * over the WebSocket bridge and calls these primitives one RPC at a time
 * (`serialize_form` -> `fill_field` -> `click_button` -> `submit`...).
 *
 * Field classification (which value goes in which field) lives SERVER-SIDE in
 * `src/services/linkedin/field_classifier.py`. We deliberately do NOT guess
 * answers here — the DOM primitives below ported from the proven AutoApplyMax
 * extension keep only the *mechanics* of reading and writing the form.
 *
 * SECURITY MODEL (ported from AutoApplyMax content-simple.js :11-46, :417-429):
 * mutating primitives (`fill_field`, `click_button`, `upload_file`, `submit`,
 * `discard`) are gated behind an explicit-connect flag. The server flips it on
 * only for the duration of an apply session via the `begin_session` RPC, so a
 * stray message can never click or type into the page unprompted. Read-only
 * primitives (`serialize_form`, `take_screenshot`) are always allowed.
 *
 * The script is injected ON DEMAND by background.js (`chrome.scripting`), never
 * via a declarative `content_scripts` block.
 */
(function () {
  'use strict';

  // ---- Gating flags (security) --------------------------------------------
  // `userExplicitlyConnected` mirrors AutoApplyMax's `userExplicitlyClickedStart`.
  // The server sets it via `begin_session`; cleared by `end_session`.
  let userExplicitlyConnected = false;

  function isMutationAllowed() {
    return userExplicitlyConnected === true;
  }

  // ---- Small DOM helpers ---------------------------------------------------
  function wait(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function isVisible(el) {
    if (!el) return false;
    // Fast path in a real browser: offsetParent is non-null for rendered,
    // statically-positioned elements. It is null for position:fixed elements
    // (and always null under JSDOM, which has no layout), so fall back to the
    // computed style to decide visibility in those cases.
    if (el.offsetParent !== null) return true;
    const view = (el.ownerDocument && el.ownerDocument.defaultView) || (typeof window !== 'undefined' ? window : null);
    if (!view || typeof view.getComputedStyle !== 'function') return false;
    const style = view.getComputedStyle(el);
    return !!style && style.display !== 'none' && style.visibility !== 'hidden';
  }

  // CSS.escape is a window global in a real content script; fall back to a
  // minimal escaper so the primitives stay testable under bare JSDOM too.
  function escapeIdent(value) {
    const css = (typeof globalThis !== 'undefined' && globalThis.CSS) || null;
    if (css && typeof css.escape === 'function') return css.escape(value);
    return String(value).replace(/([^\w-])/g, '\\$1');
  }

  // Stable, unique selector for a field. Prefer a real id; otherwise stamp a
  // private data attribute so the server can address the same node on a later
  // RPC (`fill_field`) without relying on brittle nth-child paths.
  let _eaaSeq = 0;
  function cssSelectorFor(el) {
    const id = el.getAttribute && el.getAttribute('id');
    if (id && /^[A-Za-z][\w:.-]*$/.test(id)) {
      return '#' + escapeIdent(id);
    }
    let marker = el.getAttribute && el.getAttribute('data-eaa-id');
    if (!marker) {
      marker = 'eaa-' + ++_eaaSeq;
      el.setAttribute('data-eaa-id', marker);
    }
    return '[data-eaa-id="' + marker + '"]';
  }

  // Assemble a field label from every source LinkedIn uses
  // (AutoApplyMax :872-918): aria-label + name + <label for> + ancestor label.
  function labelFor(el, scope) {
    let parts = '';
    parts += ' ' + (el.getAttribute('aria-label') || '');
    parts += ' ' + (el.getAttribute('name') || '');
    const id = el.getAttribute('id');
    if (id) {
      const labelEl = (scope || document).querySelector('label[for="' + escapeIdent(id) + '"]');
      if (labelEl) parts += ' ' + labelEl.textContent;
    }
    const parentLabel = el.closest && el.closest('label');
    if (parentLabel) parts += ' ' + parentLabel.textContent;
    // Fieldset legend for grouped controls (radios).
    const fieldset = el.closest && el.closest('fieldset');
    if (fieldset) {
      const legend = fieldset.querySelector('legend, span[class*="title"], span[class*="label"]');
      if (legend) parts += ' ' + legend.textContent;
    }
    return parts.replace(/\s+/g, ' ').trim();
  }

  function getModal() {
    return document.querySelector('.jobs-easy-apply-modal') || document.querySelector('[role="dialog"]');
  }

  function isRequired(el) {
    return (
      el.required === true ||
      el.getAttribute('aria-required') === 'true' ||
      el.getAttribute('required') !== null
    );
  }

  // ---- Primitive: serialize_form ------------------------------------------
  // Read-only. Returns the structured form state the server classifies.
  function serializeForm() {
    const modal = getModal();
    const scope = modal || document;
    const fields = [];

    // 1. Text-like inputs (text/email/tel/number) — AutoApplyMax :872.
    scope
      .querySelectorAll('input[type="text"], input[type="email"], input[type="tel"], input[type="number"]')
      .forEach((input) => {
        fields.push({
          selector: cssSelectorFor(input),
          label: labelFor(input, scope),
          type: input.getAttribute('type') || 'text',
          value: input.value || '',
          options: [],
          required: isRequired(input),
        });
      });

    // 2. File inputs (resume/CV upload).
    scope.querySelectorAll('input[type="file"]').forEach((input) => {
      fields.push({
        selector: cssSelectorFor(input),
        label: labelFor(input, scope),
        type: 'file',
        value: input.files && input.files.length ? input.files[0].name : '',
        options: [],
        required: isRequired(input),
      });
    });

    // 3. Checkboxes (consent / follow-company). AutoApplyMax :1111.
    scope.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
      fields.push({
        selector: cssSelectorFor(cb),
        label: labelFor(cb, scope),
        type: 'checkbox',
        value: cb.checked ? 'checked' : '',
        options: [],
        required: isRequired(cb),
      });
    });

    // 4. Radio fieldsets — one field per group. AutoApplyMax :1130.
    scope
      .querySelectorAll('fieldset[data-test-form-builder-radio-button-form-component], fieldset')
      .forEach((fieldset) => {
        const radios = fieldset.querySelectorAll('input[type="radio"]');
        if (!radios.length) return;
        const legend = fieldset.querySelector('legend, span[class*="title"], span[class*="label"]');
        const options = [];
        let checked = '';
        radios.forEach((radio) => {
          const rl = fieldset.querySelector('label[for="' + escapeIdent(radio.id) + '"]');
          const text = rl ? rl.textContent.trim() : radio.value || '';
          options.push(text);
          if (radio.checked) checked = text;
        });
        fields.push({
          selector: cssSelectorFor(fieldset),
          label: (legend ? legend.textContent : labelFor(radios[0], scope)).replace(/\s+/g, ' ').trim(),
          type: 'radio',
          value: checked,
          options: options,
          required: true,
        });
      });

    // 5. Native <select>. AutoApplyMax :1213.
    scope.querySelectorAll('select').forEach((select) => {
      const options = Array.from(select.options).map((o) => o.text.trim());
      fields.push({
        selector: cssSelectorFor(select),
        label: labelFor(select, scope),
        type: 'select',
        value: select.selectedIndex >= 0 ? select.options[select.selectedIndex].text.trim() : '',
        options: options,
        required: isRequired(select),
      });
    });

    // 6. Custom LinkedIn listbox dropdowns. AutoApplyMax :1282.
    scope
      .querySelectorAll('button[aria-haspopup="listbox"], button.artdeco-dropdown__trigger')
      .forEach((btn) => {
        fields.push({
          selector: cssSelectorFor(btn),
          label: labelFor(btn, scope),
          type: 'listbox',
          value: (btn.textContent || '').trim(),
          options: [],
          required: false,
        });
      });

    // Progress (best-effort): LinkedIn renders a [role=progressbar] with
    // aria-valuenow / aria-valuemax, else a "Step X of Y" string.
    let step = null;
    let total = null;
    const progress = scope.querySelector('[role="progressbar"]');
    if (progress) {
      const now = parseInt(progress.getAttribute('aria-valuenow'), 10);
      const max = parseInt(progress.getAttribute('aria-valuemax'), 10);
      if (!Number.isNaN(now)) step = now;
      if (!Number.isNaN(max)) total = max;
    }

    // Flags the server inspects (spinner / modal / daily-limit text scan).
    const spinner = document.querySelector(
      '.artdeco-loader, [role="progressbar"], .loading-spinner, .spinner'
    );
    // innerText reflects rendered (visible) text in a real browser; JSDOM only
    // implements textContent, so fall back to it for tests.
    const bodyText =
      (document.body && (document.body.innerText || document.body.textContent)) || '';

    return {
      step: step,
      total: total,
      fields: fields,
      flags: {
        has_spinner: isVisible(spinner),
        modal_present: isVisible(modal),
        page_text_excerpt: bodyText.slice(0, 4000),
      },
    };
  }

  // ---- Primitive: fill_field ----------------------------------------------
  // Gated. `value` semantics depend on the element kind:
  //   text/email/tel/number -> set value
  //   checkbox               -> truthy value => check, else uncheck
  //   radio (input/fieldset) -> select the option whose label matches `value`
  //   select                 -> choose option matching `value` (text or value)
  //   custom listbox button  -> open and click the option matching `value`
  async function fillField(selector, value) {
    if (!isMutationAllowed()) {
      return { error: 'mutation blocked: session not connected' };
    }
    const el = document.querySelector(selector);
    if (!el) return { error: 'element not found: ' + selector };

    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();

    // Native text-like inputs.
    if (tag === 'input' && ['text', 'email', 'tel', 'number', ''].includes(type)) {
      setNativeValue(el, value);
      // City/location fields surface an autocomplete; accept the first match.
      await maybePickAutocomplete(el);
      return { filled: true };
    }

    // Checkbox.
    if (tag === 'input' && type === 'checkbox') {
      const want = isTruthy(value);
      if (el.checked !== want) clickLabelOrSelf(el);
      return { filled: true, checked: want };
    }

    // A single radio input.
    if (tag === 'input' && type === 'radio') {
      if (!el.checked) clickLabelOrSelf(el);
      return { filled: true };
    }

    // A radio group (fieldset) — match the option by label text.
    if (tag === 'fieldset') {
      const radios = el.querySelectorAll('input[type="radio"]');
      for (const radio of radios) {
        const rl = el.querySelector('label[for="' + escapeIdent(radio.id) + '"]');
        const text = (rl ? rl.textContent : radio.value || '').trim().toLowerCase();
        if (text === String(value).trim().toLowerCase()) {
          if (!radio.checked) (rl || radio).click();
          return { filled: true };
        }
      }
      return { error: 'no radio option matched value: ' + value };
    }

    // Native select.
    if (tag === 'select') {
      const options = Array.from(el.options);
      const want = String(value).trim().toLowerCase();
      const match =
        options.find((o) => o.text.trim().toLowerCase() === want) ||
        options.find((o) => o.value.trim().toLowerCase() === want) ||
        options.find((o) => o.text.trim().toLowerCase().includes(want));
      if (!match) return { error: 'no select option matched value: ' + value };
      el.value = match.value;
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return { filled: true };
    }

    // Custom LinkedIn listbox.
    if (tag === 'button') {
      el.click();
      await wait(400);
      const listbox = document.querySelector('[role="listbox"]');
      if (listbox) {
        const options = Array.from(listbox.querySelectorAll('[role="option"]'));
        const want = String(value).trim().toLowerCase();
        const match =
          options.find((o) => o.textContent.trim().toLowerCase() === want) ||
          options.find((o) => o.textContent.trim().toLowerCase().includes(want));
        if (match) {
          match.click();
          return { filled: true };
        }
      }
      return { error: 'no listbox option matched value: ' + value };
    }

    return { error: 'unsupported field kind: ' + tag + '/' + type };
  }

  // Set a value the way React-style listeners expect (AutoApplyMax :417-429).
  function setNativeValue(input, value) {
    input.value = value;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function clickLabelOrSelf(input) {
    const label = document.querySelector('label[for="' + escapeIdent(input.id) + '"]');
    (label || input).click();
  }

  function isTruthy(value) {
    if (value === true) return true;
    const s = String(value).trim().toLowerCase();
    return s === 'true' || s === 'yes' || s === '1' || s === 'on' || s === 'checked';
  }

  // Location autocomplete: accept the first dropdown option (AutoApplyMax :928-977).
  async function maybePickAutocomplete(input) {
    const label = labelFor(input).toLowerCase();
    if (!/city|location|ville|ciudad|stadt|città|localisation|ubicación|standort/.test(label)) {
      return;
    }
    await wait(800);
    const dropdown =
      document.querySelector('[role="listbox"]') ||
      document.querySelector('.basic-typeahead__selectable') ||
      document.querySelector('.artdeco-typeahead__results');
    if (dropdown && isVisible(dropdown)) {
      const first =
        dropdown.querySelector('[role="option"]') ||
        dropdown.querySelector('.basic-typeahead__selectable-item') ||
        dropdown.querySelector('li');
      if (first) {
        first.click();
        await wait(300);
      }
    }
  }

  // ---- Primitive: upload_file (DataTransfer) ------------------------------
  // Gated. Ported from AutoApplyMax base64ToFile / fillFileInput :432-473.
  function base64ToFile(dataUrl, filename, mime) {
    const base64 = dataUrl.includes(',') ? dataUrl.split(',')[1] : dataUrl;
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return new File([bytes], filename, { type: mime });
  }

  async function uploadFile(selector, dataUrl, filename, mime) {
    if (!isMutationAllowed()) {
      return { error: 'mutation blocked: session not connected' };
    }
    const input = document.querySelector(selector);
    if (!input) return { error: 'file input not found: ' + selector };
    try {
      const file = base64ToFile(dataUrl, filename, mime);
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      input.dispatchEvent(new Event('change', { bubbles: true }));
      await wait(500);
      return { uploaded: true, filename: filename };
    } catch (e) {
      return { error: 'upload failed: ' + e.message };
    }
  }

  // ---- Primitive: click_button (Next / Review / Submit) -------------------
  // Gated. AutoApplyMax :1363-1367.
  const BUTTON_TEXTS = {
    next: ['next', 'suivant', 'siguiente', 'weiter', 'avanti'],
    review: ['review', 'révision', 'revisar', 'überprüfen', 'rivedi'],
    submit: ['submit', 'soumettre', 'enviar', 'absenden', 'invia', 'submit application'],
  };

  async function clickButton(role) {
    if (!isMutationAllowed()) {
      return { error: 'mutation blocked: session not connected' };
    }
    const modal = getModal();
    if (!modal) return { error: 'no modal present' };
    const wanted = BUTTON_TEXTS[role] || [role];
    const btn = Array.from(modal.querySelectorAll('button')).find((b) => {
      const t = b.textContent.trim().toLowerCase();
      return isVisible(b) && wanted.some((w) => t.includes(w));
    });
    if (!btn) return { clicked: false, error: 'button not found for role: ' + role };
    if (btn.disabled || btn.getAttribute('aria-disabled') === 'true') {
      return { clicked: false, disabled: true };
    }
    btn.click();
    await wait(600);
    return { clicked: true };
  }

  // ---- Primitive: find_and_click_done -------------------------------------
  // Gated. The 4-method finder ported from AutoApplyMax :126-281.
  const DONE_TEXTS = ['Done', 'Terminé', 'Submit application', 'Soumettre la candidature', 'Dismiss', 'Close', 'Fermer'];
  const DONE_CONTROL_NAMES = ['done', 'submit', 'continue_application'];

  async function findAndClickDone(maxAttempts) {
    if (!isMutationAllowed()) {
      return { error: 'mutation blocked: session not connected' };
    }
    const attempts = maxAttempts || 8;
    let doneBtn = null;
    for (let attempt = 0; attempt < attempts && !doneBtn; attempt++) {
      await wait(700);
      // METHOD 1: span text.
      for (const target of DONE_TEXTS) {
        const spans = Array.from(document.querySelectorAll('span.artdeco-button__text, span'));
        for (const span of spans) {
          if (span.textContent.trim() === target) {
            const clickable = span.closest('button, [role="button"], .artdeco-button') || span;
            if (isVisible(clickable)) {
              doneBtn = clickable;
              break;
            }
          }
        }
        if (doneBtn) break;
      }
      // METHOD 2: direct button text.
      if (!doneBtn) {
        for (const btn of Array.from(document.querySelectorAll('button, [role="button"]'))) {
          if (DONE_TEXTS.includes(btn.textContent.trim()) && isVisible(btn)) {
            doneBtn = btn;
            break;
          }
        }
      }
      // METHOD 3: aria-label.
      if (!doneBtn) {
        for (const target of DONE_TEXTS) {
          const ariaBtn = document.querySelector(
            'button[aria-label*="' + target + '"], [role="button"][aria-label*="' + target + '"]'
          );
          if (ariaBtn && isVisible(ariaBtn)) {
            doneBtn = ariaBtn;
            break;
          }
        }
      }
      // METHOD 4: data-control-name.
      if (!doneBtn) {
        for (const name of DONE_CONTROL_NAMES) {
          const ctl = document.querySelector('button[data-control-name*="' + name + '"]');
          if (ctl && isVisible(ctl)) {
            doneBtn = ctl;
            break;
          }
        }
      }
    }
    if (!doneBtn) return { clicked: false, reason: 'button not found' };
    doneBtn.click();
    await wait(500);
    return { clicked: true };
  }

  // ---- Primitive: unfollow_company ----------------------------------------
  // Gated. Un-tick the "follow company" box before submit (AutoApplyMax :1377-1408).
  async function unfollowCompany() {
    if (!isMutationAllowed()) {
      return { error: 'mutation blocked: session not connected' };
    }
    const modal = getModal();
    const cb =
      (modal || document).querySelector('input[id="follow-company-checkbox"]') ||
      (modal || document).querySelector('input[id*="follow-company"][type="checkbox"]');
    if (cb && cb.checked) {
      const label = (modal || document).querySelector(
        'label[for="' + escapeIdent(cb.id) + '"]'
      );
      (label || cb).click();
      await wait(300);
      return { unfollowed: true };
    }
    return { unfollowed: false };
  }

  // ---- Primitive: discard_application -------------------------------------
  // Gated. Ported from AutoApplyMax discardApplication :298-414 (X -> ESC -> scan).
  const DISCARD_TEXTS = ['discard', 'annuler', 'cancel', 'abandonner', 'descartar'];

  async function discardApplication() {
    if (!isMutationAllowed()) {
      return { error: 'mutation blocked: session not connected' };
    }
    // STEP 1: X / close button, then confirm discard.
    const closeButtons = document.querySelectorAll(
      'button[aria-label*="Dismiss"], button[aria-label*="Close"], button.artdeco-modal__dismiss'
    );
    for (const btn of closeButtons) {
      if (!isVisible(btn)) continue;
      btn.click();
      await wait(800);
      const discardBtn = Array.from(document.querySelectorAll('button')).find(
        (b) => isVisible(b) && DISCARD_TEXTS.some((t) => b.textContent.trim().toLowerCase().includes(t))
      );
      if (discardBtn) {
        discardBtn.click();
        await wait(800);
      }
      if (!isVisible(getModal())) return { discarded: true };
    }
    // STEP 2: ESC.
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', keyCode: 27, bubbles: true }));
    document.dispatchEvent(new KeyboardEvent('keyup', { key: 'Escape', keyCode: 27, bubbles: true }));
    await wait(700);
    // STEP 3: scan all buttons for a discard/cancel control.
    for (let attempt = 0; attempt < 3; attempt++) {
      for (const btn of Array.from(document.querySelectorAll('button, [role="button"]'))) {
        if (!isVisible(btn)) continue;
        const txt = btn.textContent.trim().toLowerCase();
        const aria = (btn.getAttribute('aria-label') || '').toLowerCase();
        if (DISCARD_TEXTS.some((t) => txt === t || txt.includes(t) || aria.includes(t))) {
          btn.click();
          await wait(800);
          if (!isVisible(getModal())) return { discarded: true };
        }
      }
      await wait(600);
    }
    return { discarded: !isVisible(getModal()) };
  }

  // ---- Primitive: open_easy_apply -----------------------------------------
  // Gated. Click the Easy Apply button + dismiss the safety-reminder modal
  // (AutoApplyMax :646-687). Navigation to the job URL is a background concern.
  async function openEasyApply() {
    if (!isMutationAllowed()) {
      return { error: 'mutation blocked: session not connected' };
    }
    let btn =
      document.querySelector('button.jobs-apply-button[aria-label*="Easy"]') ||
      document.querySelector('button[aria-label*="Easy Apply"]') ||
      document.querySelector('button.jobs-apply-button');
    if (!btn) return { opened: false, error: 'Easy Apply button not found' };
    btn.click();
    await wait(900);

    // Safety-reminder dialog ("Continue applying").
    const safety = document.querySelector('[role="dialog"], .artdeco-modal');
    if (safety && isVisible(safety)) {
      const text = safety.textContent.toLowerCase();
      if (/safety reminder|rappel de sécurité|continue applying|continuer à postuler/.test(text)) {
        const cont = Array.from(safety.querySelectorAll('button')).find((b) => {
          const t = b.textContent.trim().toLowerCase();
          return /continue applying|continuer à postuler|continue|continuer/.test(t);
        });
        if (cont) {
          cont.click();
          await wait(800);
        }
      }
    }
    const modal = getModal();
    return { opened: isVisible(modal) };
  }

  // ---- Primitive: reload_page ---------------------------------------------
  function reloadPage() {
    location.reload();
    return { reloaded: true };
  }

  // ---- Primitive: take_screenshot -----------------------------------------
  // Read-only. The content script can't capture pixels — the actual image grab
  // is done by background.js via chrome.tabs.captureVisibleTab. Here we return
  // the textual confirmation context (used to assert "Application sent").
  function takeScreenshot() {
    const modal = getModal();
    return {
      url: location.href,
      title: document.title,
      confirmation_text: ((modal && modal.textContent) || '').replace(/\s+/g, ' ').trim().slice(0, 500),
    };
  }

  // ---- Session gating RPCs -------------------------------------------------
  function beginSession() {
    userExplicitlyConnected = true;
    return { connected: true };
  }
  function endSession() {
    userExplicitlyConnected = false;
    return { connected: false };
  }

  // ---- RPC dispatch table --------------------------------------------------
  const HANDLERS = {
    begin_session: () => beginSession(),
    end_session: () => endSession(),
    serialize_form: () => serializeForm(),
    fill_field: (p) => fillField(p.selector, p.value),
    upload_file: (p) => uploadFile(p.selector, p.dataUrl, p.filename, p.mime),
    click_button: (p) => clickButton(p.role),
    find_and_click_done: (p) => findAndClickDone(p && p.maxAttempts),
    unfollow_company: () => unfollowCompany(),
    discard_application: () => discardApplication(),
    open_easy_apply: () => openEasyApply(),
    reload_page: () => reloadPage(),
    take_screenshot: () => takeScreenshot(),
  };

  async function dispatch(method, params) {
    const handler = HANDLERS[method];
    if (!handler) return { error: 'unknown method: ' + method };
    try {
      return await handler(params || {});
    } catch (e) {
      return { error: (e && e.message) || String(e) };
    }
  }

  // ---- chrome runtime wiring (guarded for test environments) --------------
  if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
      if (!msg || !msg.method) return false;
      dispatch(msg.method, msg.params).then(sendResponse);
      return true; // async response
    });
    console.log('[EasyApply actuator] content script ready (mutations gated until begin_session)');
  }

  // ---- Test export ---------------------------------------------------------
  // Exposed for the JSDOM unit test (`extension/tests/`). Harmless in the page:
  // the content script runs in an isolated world, so this global is invisible
  // to LinkedIn's own scripts.
  const api = {
    serializeForm,
    fillField,
    uploadFile,
    clickButton,
    findAndClickDone,
    unfollowCompany,
    discardApplication,
    openEasyApply,
    takeScreenshot,
    beginSession,
    endSession,
    dispatch,
    _setConnected: (v) => {
      userExplicitlyConnected = !!v;
    },
  };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  if (typeof globalThis !== 'undefined') {
    globalThis.EasyApplyActuator = api;
  }
})();
