// Persona-Designer: Formular-Oberflaeche ueber die bestehende
// /admin/personas-JSON-API. Kein Modul-System im Projekt - ein
// einziges Top-Level-Script wie app.js, kein <script type="module">.

const TOKEN_KEY = "senior_companion_admin_token";
const GENDER_KEYS = ["neutral", "weiblich", "maennlich"];
const GENDER_LABELS = { neutral: "Neutral", weiblich: "Weiblich", maennlich: "Männlich" };
const FIELD_LABELS = {
  id: "ID", model: "Modell", always_loaded: "Immer geladen", max_tokens: "Max. Tokens",
  reengagement_tendency: "Neigung", color: "Farbe", background_color: "Hintergrundfarbe",
  variants: "Geschlechts-Varianten", display_name: "Anzeigename", system_prompt: "Systemprompt",
  voice_id: "Stimme (voice_id)",
};

let personas = [];       // zuletzt geladene Liste, im Speicher gehalten
let editingId = null;    // null = Anlegen-Modus, sonst die bearbeitete ID

const loginView = document.getElementById("loginView");
const appView = document.getElementById("appView");
const listView = document.getElementById("listView");
const formView = document.getElementById("formView");
const logoutBtn = document.getElementById("logoutBtn");
const loginForm = document.getElementById("loginForm");
const tokenInput = document.getElementById("tokenInput");
const loginError = document.getElementById("loginError");
const personaCards = document.getElementById("personaCards");
const warningsBanner = document.getElementById("warningsBanner");
const newPersonaBtn = document.getElementById("newPersonaBtn");
const formHeading = document.getElementById("formHeading");
const formError = document.getElementById("formError");
const personaForm = document.getElementById("personaForm");
const idInput = document.getElementById("idInput");
const idReadonly = document.getElementById("idReadonly");
const modelInput = document.getElementById("modelInput");
const modelSuggestions = document.getElementById("modelSuggestions");
const alwaysLoadedInput = document.getElementById("alwaysLoadedInput");
const maxTokensInput = document.getElementById("maxTokensInput");
const reengagementInput = document.getElementById("reengagementInput");
const reengagementOutput = document.getElementById("reengagementOutput");
const colorInput = document.getElementById("colorInput");
const backgroundColorInput = document.getElementById("backgroundColorInput");
const colorPreview = document.getElementById("colorPreview");
const variantsContainer = document.getElementById("variantsContainer");
const voiceSuggestions = document.getElementById("voiceSuggestions");
const cancelFormBtn = document.getElementById("cancelFormBtn");

// --- Avatare: bewusst dupliziert aus client/js/app.js (~15 Zeilen,
// kein Modul-System vorhanden) - bei Aenderungen an der Avatar-Form
// beide Kopien synchron halten. ---------------------------------------

function avatarSvg(personaId, style) {
  const body = style === "flat"
    ? `<circle cx="50" cy="26" r="15"/>
       <path d="M30,96 Q28,42 50,40 Q72,42 70,96 Z"/>`
    : `<circle cx="50" cy="26" r="15"/>
       <line x1="50" y1="41" x2="50" y2="72"/>
       <line x1="50" y1="52" x2="30" y2="68"/>
       <line x1="50" y1="52" x2="70" y2="68"/>
       <line x1="50" y1="72" x2="34" y2="96"/>
       <line x1="50" y1="72" x2="66" y2="96"/>`;
  return `<svg class="avatar-shape stick" data-persona="${personaId}" viewBox="0 0 100 100">${body}</svg>`;
}

// Anders als app.js's applyPersonaColor (die eine Lookup-Tabelle nach
// persona_id nutzt): hier bekommt jede Karte/Vorschau ihre Farben
// direkt uebergeben, IMMER aus dem color/background_color-Feld der
// jeweiligen Persona - NIE aus style.css's hart codierten
// [data-persona="freundin"]-Regeln, sonst wuerde eine geaenderte
// Farbe einer mitgelieferten Persona in der Vorschau nicht sichtbar
// werden (die CSS-Regel hat hoehere Spezifitaet und wuerde weiter
// gewinnen).
function applyPersonaColorAdmin(el, color, background) {
  if (!el) return;
  el.style.setProperty("--avatar-color", color);
  el.style.background = background;
}

function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// --- Token-Gate / authedFetch ------------------------------------------

function extractErrorMessage(body) {
  if (!body) return "Unbekannter Fehler.";
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) {
    return body.detail.map((e) => {
      const field = e.loc[e.loc.length - 1];
      return `${FIELD_LABELS[field] || field}: ${e.msg}`;
    }).join("\n");
  }
  return "Unbekannter Fehler.";
}

async function authedFetch(path, options = {}) {
  const token = localStorage.getItem(TOKEN_KEY);
  const headers = { ...(options.headers || {}), Authorization: "Bearer " + token };
  const res = await fetch(path, { ...options, headers });
  if (res.status === 401) {
    localStorage.removeItem(TOKEN_KEY);
    const body = await res.json().catch(() => null);
    showLoginView(extractErrorMessage(body) || "Ungültiges oder abgelaufenes Token.");
    throw new Error("unauthorized");
  }
  return res;
}

function showLoginView(message) {
  appView.hidden = true;
  logoutBtn.hidden = true;
  loginView.hidden = false;
  if (message) {
    loginError.textContent = message;
    loginError.hidden = false;
  } else {
    loginError.hidden = true;
  }
}

function showAppView() {
  loginView.hidden = true;
  appView.hidden = false;
  logoutBtn.hidden = false;
  showListView();
}

async function tryLogin(token) {
  try {
    const res = await fetch("/admin/personas", { headers: { Authorization: "Bearer " + token } });
    if (res.status === 200) {
      localStorage.setItem(TOKEN_KEY, token);
      personas = await res.json();
      showAppView();
      renderCards();
      return true;
    }
    localStorage.removeItem(TOKEN_KEY);
    const body = await res.json().catch(() => null);
    showLoginView(extractErrorMessage(body));
    return false;
  } catch (err) {
    localStorage.removeItem(TOKEN_KEY);
    showLoginView("Server nicht erreichbar.");
    return false;
  }
}

loginForm.addEventListener("submit", (e) => {
  e.preventDefault();
  tryLogin(tokenInput.value.trim());
});

logoutBtn.addEventListener("click", () => {
  localStorage.removeItem(TOKEN_KEY);
  showLoginView();
});

async function init() {
  const stored = localStorage.getItem(TOKEN_KEY);
  if (stored) {
    await tryLogin(stored);
  } else {
    showLoginView();
  }
}

// --- Listen-Ansicht ------------------------------------------------

function showListView() {
  formView.hidden = true;
  listView.hidden = false;
}

async function reloadPersonas() {
  const res = await authedFetch("/admin/personas");
  personas = await res.json();
  renderCards();
}

function renderCards() {
  personaCards.innerHTML = "";
  personas.forEach((p) => {
    const card = document.createElement("div");
    card.className = "admin-card";
    card.dataset.personaId = p.id;

    const avatarWrap = document.createElement("span");
    avatarWrap.className = "avatar-shape-bg admin-card-avatar";
    avatarWrap.innerHTML = avatarSvg(p.id, "stick");
    applyPersonaColorAdmin(avatarWrap, p.color, p.background_color);
    card.appendChild(avatarWrap);

    const info = document.createElement("div");
    info.className = "admin-card-info";
    const name = escapeHtml(p.variants.neutral.display_name);
    const badge = p.is_builtin ? "mitgeliefert" : "eigene";
    info.innerHTML = `
      <div class="admin-card-name">${name} <span class="admin-badge">${badge}</span></div>
      <div class="admin-card-meta">${escapeHtml(p.id)} &middot; ${escapeHtml(p.model)}</div>
    `;
    card.appendChild(info);

    const actions = document.createElement("div");
    actions.className = "admin-card-actions";

    const editBtn = document.createElement("button");
    editBtn.className = "admin-btn admin-btn-secondary";
    editBtn.textContent = "Bearbeiten";
    editBtn.addEventListener("click", () => openEditForm(p.id));
    actions.appendChild(editBtn);

    const dangerBtn = document.createElement("button");
    dangerBtn.className = "admin-btn admin-btn-danger";
    dangerBtn.textContent = p.is_builtin ? "Zurücksetzen" : "Löschen";
    dangerBtn.addEventListener("click", () => deleteOrRevert(p));
    actions.appendChild(dangerBtn);

    card.appendChild(actions);
    personaCards.appendChild(card);
  });
}

async function deleteOrRevert(p) {
  const name = p.variants.neutral.display_name;
  const message = p.is_builtin
    ? `"${name}" auf die mitgelieferten Standard-Werte zurücksetzen?`
    : `"${name}" endgültig löschen? Das kann nicht rückgängig gemacht werden.`;
  if (!confirm(message)) return;

  try {
    await authedFetch(`/admin/personas/${encodeURIComponent(p.id)}`, { method: "DELETE" });
  } catch (err) {
    return; // authedFetch hat bei 401 bereits auf die Login-Ansicht umgeschaltet
  }
  await reloadPersonas();
}

newPersonaBtn.addEventListener("click", () => openCreateForm());
cancelFormBtn.addEventListener("click", () => showListView());

// --- Formular-Ansicht ------------------------------------------------

function variantFieldsetHTML(genderKey, variant) {
  return `
    <fieldset class="variant-fieldset" data-gender="${genderKey}">
      <legend>${GENDER_LABELS[genderKey]}</legend>
      <label>Anzeigename
        <input type="text" name="display_name" required value="${escapeHtml(variant.display_name)}">
      </label>
      <label>Systemprompt
        <textarea name="system_prompt" rows="6" required>${escapeHtml(variant.system_prompt)}</textarea>
      </label>
      <label>Stimme (voice_id)
        <input type="text" name="voice_id" list="voiceSuggestions" value="${escapeHtml(variant.voice_id)}">
      </label>
    </fieldset>`;
}

const EMPTY_VARIANT = { display_name: "", system_prompt: "", voice_id: "" };
const DEFAULT_PERSONA = {
  model: "", always_loaded: true, max_tokens: 400, reengagement_tendency: 0.5,
  color: "#4A5D52", background_color: "#E9EEEA",
  variants: { neutral: EMPTY_VARIANT, weiblich: EMPTY_VARIANT, maennlich: EMPTY_VARIANT },
};

function populateSuggestions() {
  const models = new Set();
  const voices = new Set();
  personas.forEach((p) => {
    if (p.model) models.add(p.model);
    GENDER_KEYS.forEach((g) => {
      const v = p.variants[g];
      if (v && v.voice_id) voices.add(v.voice_id);
    });
  });
  modelSuggestions.innerHTML = [...models].map((m) => `<option value="${escapeHtml(m)}">`).join("");
  voiceSuggestions.innerHTML = [...voices].map((v) => `<option value="${escapeHtml(v)}">`).join("");
}

function fillForm(persona, isCreate) {
  populateSuggestions();

  if (isCreate) {
    idInput.hidden = false;
    idInput.value = "";
    idInput.required = true;
    idReadonly.hidden = true;
  } else {
    idInput.hidden = true;
    idInput.required = false;
    idReadonly.hidden = false;
    idReadonly.textContent = persona.id;
  }

  modelInput.value = persona.model;
  alwaysLoadedInput.checked = persona.always_loaded;
  maxTokensInput.value = persona.max_tokens;
  reengagementInput.value = persona.reengagement_tendency;
  reengagementOutput.textContent = Number(persona.reengagement_tendency).toFixed(2);
  colorInput.value = persona.color;
  backgroundColorInput.value = persona.background_color;
  applyPersonaColorAdmin(colorPreview, persona.color, persona.background_color);
  colorPreview.innerHTML = avatarSvg("preview", "stick");

  variantsContainer.innerHTML = GENDER_KEYS
    .map((g) => variantFieldsetHTML(g, persona.variants[g] || EMPTY_VARIANT))
    .join("");
}

function openCreateForm() {
  editingId = null;
  formHeading.textContent = "Neue Persona anlegen";
  formError.hidden = true;
  fillForm(DEFAULT_PERSONA, true);
  listView.hidden = true;
  formView.hidden = false;
}

function openEditForm(personaId) {
  const persona = personas.find((p) => p.id === personaId);
  if (!persona) return;
  editingId = personaId;
  formHeading.textContent = `Bearbeiten: ${persona.variants.neutral.display_name}`;
  formError.hidden = true;
  fillForm(persona, false);
  listView.hidden = true;
  formView.hidden = false;
}

reengagementInput.addEventListener("input", () => {
  reengagementOutput.textContent = Number(reengagementInput.value).toFixed(2);
});

[colorInput, backgroundColorInput].forEach((el) => {
  el.addEventListener("input", () => {
    applyPersonaColorAdmin(colorPreview, colorInput.value, backgroundColorInput.value);
  });
});

function collectVariants() {
  const variants = {};
  variantsContainer.querySelectorAll(".variant-fieldset").forEach((fs) => {
    const gender = fs.dataset.gender;
    variants[gender] = {
      display_name: fs.querySelector('[name="display_name"]').value,
      system_prompt: fs.querySelector('[name="system_prompt"]').value,
      voice_id: fs.querySelector('[name="voice_id"]').value,
    };
  });
  return variants;
}

function buildBody(isCreate) {
  const body = {
    model: modelInput.value,
    always_loaded: alwaysLoadedInput.checked,
    max_tokens: Number(maxTokensInput.value),
    reengagement_tendency: Number(reengagementInput.value),
    color: colorInput.value,
    background_color: backgroundColorInput.value,
    variants: collectVariants(),
  };
  if (isCreate) body.id = idInput.value;
  return body;
}

function showWarnings(warnings) {
  if (warnings && warnings.length > 0) {
    warningsBanner.textContent = "Hinweis: " + warnings.join("\n");
    warningsBanner.hidden = false;
  } else {
    warningsBanner.hidden = true;
  }
}

personaForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  formError.hidden = true;
  const isCreate = editingId === null;
  const body = buildBody(isCreate);
  const path = isCreate ? "/admin/personas" : `/admin/personas/${encodeURIComponent(editingId)}`;
  const method = isCreate ? "POST" : "PUT";

  let res;
  try {
    res = await authedFetch(path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    return; // authedFetch hat bei 401 bereits auf die Login-Ansicht umgeschaltet
  }

  if (res.status !== 200 && res.status !== 201) {
    const errBody = await res.json().catch(() => null);
    formError.textContent = extractErrorMessage(errBody);
    formError.hidden = false;
    return;
  }

  const result = await res.json();
  await reloadPersonas();
  showListView();
  showWarnings(result.warnings);
});

init();
