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
  voice_id: "Stimme (voice_id)", face_eyebrows: "Augenbrauen", face_eyes: "Augen",
  face_mouth: "Mund", face_hairstyle: "Frisur", face_beard: "Bart",
};

// Deutsche Beschriftungen fuer die Gesichts-Dropdowns - reine
// UI-Anzeige, gespeichert wird immer der lateinische toon-head-
// Schluessel bzw. der Frisur-Preset-Schluessel (siehe HAIRSTYLE_PRESETS).
const FACE_OPTION_LABELS = {
  face_eyebrows: {
    angry: "Zornig", happy: "Fröhlich", neutral: "Neutral", raised: "Hochgezogen", sad: "Traurig",
  },
  face_eyes: {
    bow: "Lachend (zu)", happy: "Fröhlich", humble: "Bescheiden", wide: "Aufmerksam", wink: "Zwinkernd",
  },
  face_mouth: {
    agape: "Erstaunt", angry: "Zornig", laugh: "Lachend", sad: "Traurig", smile: "Lächelnd",
  },
  face_hairstyle: {
    kurz: "Kurz", kurz_gescheitelt: "Kurz, gescheitelt", spiky: "Kurz, wuschelig",
    dutt: "Dutt", lang_glatt: "Lang, glatt", lang_gewellt: "Lang, gewellt",
  },
  face_beard: {
    "": "Kein Bart", chin: "Kinnbart", chinMoustache: "Kinnbart mit Schnurrbart",
    fullBeard: "Vollbart", longBeard: "Langer Bart", moustacheTwirl: "Gezwirbelter Schnurrbart",
  },
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

// --- Avatare: bewusst dupliziert aus client/js/app.js (kein
// Modul-System vorhanden) - bei Aenderungen an der Avatar-Form beide
// Kopien synchron halten. Die zugrundeliegenden JSON-Daten
// (FACE_DATA) werden dagegen NICHT dupliziert, sondern von beiden
// Dateien unabhaengig per fetch() geladen. ------------------------------

let FACE_DATA = null;
async function loadFaceData() {
  if (!FACE_DATA) {
    FACE_DATA = await (await fetch("assets/toon-head-faces.json")).json();
  }
  return FACE_DATA;
}

const HAIRSTYLE_PRESETS = {
  kurz: { hair: "undercut", rearHair: "neckHigh" },
  kurz_gescheitelt: { hair: "sideComed", rearHair: "neckHigh" },
  spiky: { hair: "spiky", rearHair: "neckHigh" },
  dutt: { hair: "bun", rearHair: "shoulderHigh" },
  lang_glatt: { hair: "sideComed", rearHair: "longStraight" },
  lang_gewellt: { hair: "bun", rearHair: "longWavy" },
};

function faceAttrValue(v, colorMap) {
  if (v && typeof v === "object" && v.type === "color") return colorMap[v.name];
  return v;
}

function faceAttrs(attributes, colorMap) {
  if (!attributes) return "";
  return Object.entries(attributes)
    .map(([k, v]) => ` ${k}="${faceAttrValue(v, colorMap)}"`)
    .join("");
}

function renderFaceElements(elements, colorMap) {
  return (elements || [])
    .map((el) => {
      const attrs = faceAttrs(el.attributes, colorMap);
      if (el.children) {
        return `<${el.name}${attrs}>${renderFaceElements(el.children, colorMap)}</${el.name}>`;
      }
      return `<${el.name}${attrs}/>`;
    })
    .join("");
}

function faceComponentGroup(faceData, componentName, variantKey, colorMap) {
  const component = faceData.components[componentName];
  const variant = component && component.variants[variantKey];
  if (!variant) return "";
  const canvasEl = faceData.canvas.elements.find((e) => e.name === componentName);
  const transform = canvasEl ? canvasEl.attributes.transform : "";
  return `<g transform="${transform}">${renderFaceElements(variant.elements, colorMap)}</g>`;
}

function noseSvg(color) {
  return `<path d="M384 380 Q392 420 378 438" stroke="${color}" fill="none" stroke-width="6" stroke-linecap="round"/>`;
}

function avatarSvg(personaId, faceData, face) {
  if (!faceData || !face) {
    return `<svg class="avatar-shape" data-persona="${personaId}" viewBox="0 0 768 768"></svg>`;
  }
  const preset = HAIRSTYLE_PRESETS[face.face_hairstyle] || HAIRSTYLE_PRESETS.kurz;
  const colorMap = { stroke: face.color, hair: "var(--ink)", skin: "none" };
  const parts = [
    faceComponentGroup(faceData, "rearHair", preset.rearHair, colorMap),
    faceComponentGroup(faceData, "head", "head", colorMap),
    faceComponentGroup(faceData, "eyebrows", face.face_eyebrows, colorMap),
    faceComponentGroup(faceData, "eyes", face.face_eyes, colorMap),
    faceComponentGroup(faceData, "mouth", face.face_mouth, colorMap),
    noseSvg(face.color),
    faceComponentGroup(faceData, "hair", preset.hair, colorMap),
    face.face_beard ? faceComponentGroup(faceData, "beard", face.face_beard, colorMap) : "",
  ];
  return `<svg class="avatar-shape" data-persona="${personaId}" viewBox="0 0 768 768">${parts.join("")}</svg>`;
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
  await loadFaceData();
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
    avatarWrap.innerHTML = avatarSvg(p.id, FACE_DATA, { ...p.variants.neutral, color: p.color });
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

const FACE_FIELDS = ["face_eyebrows", "face_eyes", "face_mouth", "face_hairstyle", "face_beard"];

function faceSelectHTML(field, value) {
  const options = Object.entries(FACE_OPTION_LABELS[field])
    .map(([v, label]) => `<option value="${v}"${v === value ? " selected" : ""}>${label}</option>`)
    .join("");
  return `<label>${FIELD_LABELS[field]}
    <select name="${field}">${options}</select>
  </label>`;
}

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
      <span class="avatar-shape-bg variant-face-preview"></span>
      ${FACE_FIELDS.map((f) => faceSelectHTML(f, variant[f])).join("")}
    </fieldset>`;
}

const EMPTY_VARIANT = {
  display_name: "", system_prompt: "", voice_id: "",
  face_eyebrows: "neutral", face_eyes: "happy", face_mouth: "smile",
  face_hairstyle: "kurz", face_beard: "",
};
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
  colorPreview.innerHTML = avatarSvg("preview", FACE_DATA, { ...persona.variants.neutral, color: persona.color });

  variantsContainer.innerHTML = GENDER_KEYS
    .map((g) => variantFieldsetHTML(g, persona.variants[g] || EMPTY_VARIANT))
    .join("");
  refreshVariantPreviews();
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
    refreshVariantPreviews();
  });
});

// Liest die 5 Gesichts-Dropdowns eines Fieldsets + die aktuell im
// Formular gesetzte Farbe (color-Feld ist persona-weit, nicht pro
// Geschlechts-Variante) - fuer die Live-Vorschau.
function readVariantFace(fieldset) {
  const face = { color: colorInput.value };
  FACE_FIELDS.forEach((f) => {
    face[f] = fieldset.querySelector(`[name="${f}"]`).value;
  });
  return face;
}

function refreshVariantPreviews() {
  variantsContainer.querySelectorAll(".variant-fieldset").forEach((fs) => {
    const preview = fs.querySelector(".variant-face-preview");
    if (!preview) return;
    preview.innerHTML = avatarSvg("preview", FACE_DATA, readVariantFace(fs));
  });
}

// Delegierter Listener statt 5 Listener pro Fieldset x 3 Geschlechter -
// variantsContainer.innerHTML wird bei jedem fillForm() komplett neu
// aufgebaut, direkte Listener wuerden dabei ohnehin verloren gehen.
variantsContainer.addEventListener("change", (e) => {
  if (FACE_FIELDS.includes(e.target.name)) refreshVariantPreviews();
});

function collectVariants() {
  const variants = {};
  variantsContainer.querySelectorAll(".variant-fieldset").forEach((fs) => {
    const gender = fs.dataset.gender;
    const variant = {
      display_name: fs.querySelector('[name="display_name"]').value,
      system_prompt: fs.querySelector('[name="system_prompt"]').value,
      voice_id: fs.querySelector('[name="voice_id"]').value,
    };
    FACE_FIELDS.forEach((f) => {
      variant[f] = fs.querySelector(`[name="${f}"]`).value;
    });
    variants[gender] = variant;
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
