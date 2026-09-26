// Einfache, bewusst framework-freie Chat-Logik.
// USER_ID kommt aus dem Query-Parameter, mit dem die PWA installiert
// wurde (?user=maria) und wird danach in localStorage gemerkt - kein
// Login-Bildschirm, die Zuordnung Geraet<->Person passiert einmalig
// beim Einrichten durch Angehoerige (siehe README, Abschnitt
// "Mehrere Personen").
function resolveUserId() {
  const fromUrl = new URLSearchParams(location.search).get("user");
  if (fromUrl) {
    localStorage.setItem("senior_companion_user_id", fromUrl);
    return fromUrl;
  }
  return localStorage.getItem("senior_companion_user_id") || "testnutzer_1";
}
const USER_ID = resolveUserId();

// Spracherkennung/-ausgabe: "device" (Web Speech API, Standard) oder
// "server" (eigener Sprachdienst, siehe speech-service/ - fuer
// schwaechere Tablets). Pro Geraet gemerkt wie USER_ID.
function loadSetting(key, fallback) {
  return localStorage.getItem(key) || fallback;
}
let sttMode = loadSetting("senior_companion_stt_mode", "device");
let ttsMode = loadSetting("senior_companion_tts_mode", "server");
// Wie kleinteilig die Antwort an die Sprachausgabe gereicht wird,
// waehrend das LLM noch weiterschreibt - "sentence" (Standard, ganze
// Saetze, beste Betonung) oder eine Wortanzahl als String ("3","4","5"
// usw., geringere Latenz bis zum ersten Ton, kann aber abgehackt
// klingen). Bewusst experimentell umschaltbar (Transparenz-Panel), da
// noch unklar ist, wie gut die Piper-Stimmen mit Haeppchen zurechtkommen.
let ttsChunkMode = loadSetting("senior_companion_tts_chunk_mode", "sentence");
// "avatar" (Standard) zeigt die Präsenz-Oberfläche, "text" das
// bisherige Chat-Log. Der frühere Stil-Umschalter (Strichmännchen/
// Flächig) ist mit den Strichgesicht-Avataren entfallen - es gibt nur
// noch den einen Stil. Der alte localStorage-Schlüssel
// "senior_companion_avatar_style" bleibt ungenutzt liegen, harmlos.
let uiMode = loadSetting("senior_companion_ui_mode", "avatar");

let currentPersona = null;     // wer zuletzt sprach (Bubble-Zuordnung/TTS-Stimme)
let socket = null;

// --- Pause/Fortsetzen (siehe pauseBtn weiter unten) --------------------
// "paused" haelt Sprachausgabe UND eingehenden Chat komplett an (z.B.
// kurzer Toilettengang/Telefonanruf) - lastUtteranceText/-Persona
// merken sich, was zuletzt gesagt wurde, damit resumeSystem() es beim
// Fortsetzen wiederholen kann, um beim Wiedereinstieg zu helfen.
let paused = false;
let listening = false;         // Soll-Zustand: soll der Recognizer gerade laufen?
let lastUtteranceText = "";
let lastUtterancePersona = null;
let preparedResumeAudio = null; // waehrend der Pause vorab geholtes "resumed"-Audio (siehe pauseSystem())
const PERSONA_NAMES = {};          // fuer sichtbare Beschriftungen - voller display_name (z.B. "Professor Wallner")
const PERSONA_ADDRESS_NAMES = {};  // fuer addressPersona() - ohne Titel (z.B. "Wallner"), muss zu room.py's Ansprache-Erkennung passen
const PERSONA_COLORS = {};
const PERSONA_FACES = {};      // personaId -> {face_eyebrows, face_eyes, face_mouth, face_hairstyle, face_beard}

// Rohdaten des DiceBear-Stils "toon-head" (CC BY 4.0, siehe README) -
// einmalig geladen, von beiden Rendering-Stellen (Taskleiste + grosse
// Buehne) gemeinsam genutzt.
let FACE_DATA = null;
async function loadFaceData() {
  if (!FACE_DATA) {
    FACE_DATA = await (await fetch("assets/toon-head-faces.json")).json();
  }
  return FACE_DATA;
}

// Anwesenheits-Zustand der grossen Avatar-Buehne: wer ist gerade im
// Raum sichtbar (praesenzgetrieben, siehe presence-Nachricht vom
// Server), und wer davon GERADE spricht (Token-Stream/TTS-Wiedergabe -
// hoechstens eine Persona gleichzeitig). lastActiveTs bestimmt bei mehr
// als MAX_VISIBLE_PERSONAS Anwesenden, welche tatsaechlich gezeigt
// werden (die am laengsten inaktive faellt aus der Anzeige, nicht aus
// der eigentlichen Anwesenheit).
const presentPersonas = new Map(); // personaId -> { lastActiveTs: number }
let speakingPersona = null;
const MAX_VISIBLE_PERSONAS = 4;

// Setzt Avatar-Farbe/Hintergrund auf dem STABILEN Eltern-Element
// (.avatar-shape-bg), nicht auf der SVG selbst - refreshAvatarStyles()
// ersetzt bei einem Stilwechsel (Strichmaennchen/flaechig) nur deren
// innerHTML, das Eltern-Element bleibt bestehen. --avatar-color ist
// eine CSS-Custom-Property und vererbt sich an die neu erzeugte SVG
// darin automatisch weiter - die 4 mitgelieferten Personas behalten
// unveraendert ihre direkten CSS-Regeln (hoehere Spezifitaet), nur
// eine neu angelegte Persona ohne eigene CSS-Regel braucht diesen
// geerbten Wert tatsaechlich.
function applyPersonaColor(el, personaId) {
  const colors = PERSONA_COLORS[personaId];
  if (!el || !colors) return;
  el.style.setProperty("--avatar-color", colors.color);
  el.style.background = colors.background;
}

const chatArea = document.getElementById("chatArea");
const avatarStage = document.getElementById("avatarStage");
const personaTabs = document.getElementById("personaTabs");
const versionBadge = document.getElementById("versionBadge");
const chatHint = document.getElementById("chatHint");
const lookaheadKpi = document.getElementById("lookaheadKpi");
const textInput = document.getElementById("textInput");
const sendBtn = document.getElementById("sendBtn");
const pauseBtn = document.getElementById("pauseBtn");
const micLevelMeter = document.getElementById("micLevelMeter");

// Kein eigener Start-Bildschirm mehr (siehe docs/ARCHITECTURE.md-nahe
// Session-Notiz) - der Leerlauf-Hinweis in BEIDEN Ansichten zeigt bis
// zum Ende von loadPersonas() den Vorbereitungs-Text an derselben
// Stelle, an der danach der echte Leerlauf-Hinweis steht (siehe
// stageIdle()/updateChatHint() unten).
const LOADING_HINT = "Einen Moment, wir bereiten alles vor …";
const READY_HINT = "Tippe unten etwas, oder sprich einfach – ich höre zu.";
let systemReady = false;

function idleHintText() {
  return systemReady ? READY_HINT : LOADING_HINT;
}

function updateChatHint() {
  chatHint.textContent = idleHintText();
  chatHint.classList.toggle("hint-loading", !systemReady);
}

// --- Avatare: reine Strichgesichter (DiceBear "toon-head", CC BY --------
// 4.0), kein Koerper. Bauteil-Baeume kommen aus FACE_DATA (siehe
// loadFaceData()), Farbe wird beim Rendern direkt eingesetzt statt per
// CSS-Klasse, damit jede Persona ihre eigene Akzentfarbe behalten kann.

const HAIRSTYLE_PRESETS = {
  kurz: { hair: "undercut", rearHair: "neckHigh" },
  kurz_gescheitelt: { hair: "sideComed", rearHair: "neckHigh" },
  spiky: { hair: "spiky", rearHair: "neckHigh" },
  dutt: { hair: "bun", rearHair: "shoulderHigh" },
  lang_glatt: { hair: "sideComed", rearHair: "longStraight" },
  lang_gewellt: { hair: "bun", rearHair: "longWavy" },
};

// Setzt {type:"color", name:"stroke"|"hair"|"skin"}-Platzhalter durch
// echte Werte; literale Attribute (z.B. die halbtransparenten
// Schattierungs-Pfade) bleiben unveraendert.
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

// Rekursiv, da manche Varianten (z.B. eyebrows.neutral) ein <g> mit
// verschachtelten <path>-Kindern sind, keine flache Struktur.
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

// toon-head hat keine Nase - eigene, handgezeichnete Linie, fest
// positioniert zwischen Augen- (~y=367) und Mundhoehe (~y=487).
function noseSvg(color) {
  return `<path d="M384 380 Q392 420 378 438" stroke="${color}" fill="none" stroke-width="6" stroke-linecap="round"/>`;
}

function avatarSvg(personaId, faceData, face) {
  if (!faceData || !face) {
    return `<svg class="avatar-shape" data-persona="${personaId}" viewBox="0 0 768 768"></svg>`;
  }
  const preset = HAIRSTYLE_PRESETS[face.face_hairstyle] || HAIRSTYLE_PRESETS.kurz;
  // "skin" faerbt nur den Kopf - der bleibt bewusst NICHT transparent:
  // ohne opake Fuellung schiene das Hinterkopf-Haar (rearHair), das
  // hinter dem Kopf liegt, mitten durchs Gesicht durch (wirkte wie eine
  // dunkle Maske). Die Hintergrundfarbe der Person macht den Kopf
  // optisch unsichtbar (verschmilzt mit dem Kreis dahinter), blockt das
  // Hinterkopf-Haar aber korrekt ab.
  const colorMap = { stroke: face.color, hair: "var(--ink)", skin: face.background || "none" };
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

// Baut das face-Objekt, das avatarSvg() braucht, aus den beiden
// unabhaengigen Quellen zusammen: Zuege aus PERSONA_FACES, Farbe aus
// PERSONA_COLORS (bleibt eine eigene Zustaendigkeit, siehe
// applyPersonaColor()).
function faceFor(personaId) {
  const face = PERSONA_FACES[personaId];
  const colors = PERSONA_COLORS[personaId];
  if (!face || !colors) return null;
  return { ...face, color: colors.color, background: colors.background };
}

// --- Avatar-Buehne: zeigt, wer gerade antwortet/spricht ---------------

function stageIdle() {
  const loadingClass = systemReady ? "" : " hint-loading";
  avatarStage.innerHTML = `<p class="stage-hint${loadingClass}" id="stageHint">${idleHintText()}</p>`;
}

// Baut die gesamte Avatar-Buehne aus presentPersonas + speakingPersona
// neu auf - einzige Stelle, die avatarStage.innerHTML schreibt (frueher
// zwei Quellen der Wahrheit: stageShow()/stageIdle()). Immer von Grund
// auf neu zu rendern ist bei max. 4 Kacheln und seltenen Aufrufen
// (Token-Tempo ist durchs LLM gedrosselt) guenstig genug - kein Diffing
// noetig.
function renderAvatarStage() {
  if (presentPersonas.size === 0) {
    stageIdle();
    return;
  }
  const ordered = [...presentPersonas.entries()]
    .sort((a, b) => b[1].lastActiveTs - a[1].lastActiveTs)
    .slice(0, MAX_VISIBLE_PERSONAS)
    .map(([personaId]) => personaId);

  avatarStage.innerHTML = "";
  const grid = document.createElement("div");
  grid.className = `avatar-grid avatar-grid-${ordered.length}`;
  ordered.forEach((personaId) => {
    const tile = document.createElement("div");
    tile.className = "avatar-tile";
    tile.innerHTML = `
      <span class="avatar-shape-bg avatar-full-bg" data-persona="${personaId}">
        ${avatarSvg(personaId, FACE_DATA, faceFor(personaId))}
      </span>
      <span class="avatar-full-name">${PERSONA_NAMES[personaId] || ""}</span>
    `;
    const bg = tile.querySelector(".avatar-shape-bg");
    applyPersonaColor(bg, personaId);
    applySpeakingCue(bg, personaId, personaId === speakingPersona);
    grid.appendChild(tile);
  });
  avatarStage.appendChild(grid);
  syncPersonaPresentTabs(ordered);
}

// Einzige Stelle, die entscheidet, WIE sich eine sprechende Kachel von
// einer nur-anwesenden unterscheidet - Pulsieren + Rahmen, sobald
// diese Person laut speakingPersona "dran" ist (optimistisch, schon
// beim ersten Text-Token, siehe dort). Die MUND-Animation selbst
// laeuft NICHT mehr hier mit, sondern komplett getrennt ueber
// mouthAnimationTick() unten - siehe Begruendung dort.
function applySpeakingCue(tileBg, personaId, isSpeaking) {
  tileBg.classList.toggle("speaking", isSpeaking);
}

// Kein echtes Lippen-Lesen - wechselt im Sprechrhythmus einfach
// zwischen dem Ruhe-Mund der Persona und einer offenen Variante
// ("agape"). EIN durchgehendes Intervall fuer die ganze App-Laufzeit
// (statt pro renderAvatarStage()-Aufruf neu gestartet wie zuvor) -
// Grund: an isCurrentlySpeaking() (ECHTE Audio-Wiedergabe/-Synthese)
// gekoppelt, nicht an den Text-Token-/Done-Nachrichten-Rhythmus. Live
// beobachtet (Session-Notiz 2026-09-23): vorher lief die Animation
// schon los, sobald Text zu streamen begann - Sprachausgabe braucht
// aber noch Synthese-/Netzwerkzeit bis zum ersten Ton - und blieb nach
// "done" unveraendert weiterlaufen, obwohl das eigentliche Audio
// laengst fertig war. tileBg wird bei jedem renderAvatarStage()
// komplett neu gebaut (dort per innerHTML ersetzt), daher hier IMMER
// frisch ueber [data-persona] nachgeschlagen statt eine Referenz aus
// einem frueheren Render zu halten (die waere sonst ein Zombie-Element).
const MOUTH_ANIMATION_INTERVAL_MS = 220;
let mouthOpen = false;
let mouthActivePersona = null; // wessen Mund gerade offen gehalten wird

function _avatarTileFor(personaId) {
  return avatarStage.querySelector(`.avatar-shape-bg[data-persona="${personaId}"]`);
}

function _resetMouthIfNeeded() {
  if (!mouthActivePersona) return;
  const tileBg = _avatarTileFor(mouthActivePersona);
  const face = faceFor(mouthActivePersona);
  if (tileBg && face && FACE_DATA) {
    tileBg.innerHTML = avatarSvg(mouthActivePersona, FACE_DATA, face);
  }
  mouthActivePersona = null;
  mouthOpen = false;
}

function mouthAnimationTick() {
  const speaking = Boolean(speakingPersona) && isCurrentlySpeaking();
  if (!speaking) {
    _resetMouthIfNeeded();
    return;
  }
  const face = faceFor(speakingPersona);
  const tileBg = _avatarTileFor(speakingPersona);
  if (!face || !FACE_DATA || !tileBg) return;
  mouthOpen = !mouthOpen;
  mouthActivePersona = speakingPersona;
  tileBg.innerHTML = avatarSvg(speakingPersona, FACE_DATA, {
    ...face,
    face_mouth: mouthOpen ? "agape" : face.face_mouth,
  });
}

setInterval(mouthAnimationTick, MOUTH_ANIMATION_INTERVAL_MS);

// Merkt eine Persona als anwesend (fuegt sie ggf. neu hinzu) und
// aktualisiert ihren Aktivitaets-Zeitstempel.
function markPersonaPresentOnStage(personaId, ts = Date.now()) {
  presentPersonas.set(personaId, { lastActiveTs: ts });
}

// Ersetzt die Anwesenheitsliste 1:1 durch das, was die presence-
// Nachricht vom Server meldet (abgleichende Quelle der Wahrheit,
// insbesondere fuers Entfernen nach Timeout, das der Client vorher gar
// nicht wissen konnte). Bestehende Zeitstempel bleiben fuer weiterhin
// anwesende Personas erhalten, damit die Kachel-Reihenfolge nicht bei
// jedem Abgleich springt.
function applyPresenceFromServer(presentIds) {
  const now = Date.now();
  const nextIds = new Set(presentIds);
  for (const id of [...presentPersonas.keys()]) {
    if (!nextIds.has(id)) presentPersonas.delete(id);
  }
  for (const id of presentIds) {
    if (!presentPersonas.has(id)) presentPersonas.set(id, { lastActiveTs: now });
  }
}

function applyUiMode() {
  chatArea.hidden = uiMode !== "text";
  avatarStage.hidden = uiMode !== "avatar";
}

// --- Personas laden und Tabs aufbauen -------------------------------

// Kleine Versions-Anzeige oben neben den Personas - Abgleich per Auge,
// ob der Browser gerade wirklich den erwarteten Stand zeigt (siehe
// main.py's /api/version: der Wert steht beim Prozessstart fest, ein
// blosses "git pull" ohne Neustart aendert ihn nicht). Absichtlich
// leise scheiternd (kein Abbruch), falls /api/version aelter/fehlt.
async function loadVersionBadge() {
  try {
    const res = await fetch("/api/version");
    const { commit } = await res.json();
    versionBadge.textContent = commit;
    // Idee (2026-09-23): die ersten 6 Hex-Stellen des Commit-Kurzhashes
    // direkt als Rahmenfarbe verwenden, statt sie nur als Text zu
    // zeigen - ein Farbwechsel springt sofort ins Auge, wenn sich die
    // Version aendert, ganz ohne die Ziffern vergleichen zu muessen.
    const colorHex = commit.slice(0, 6);
    versionBadge.style.borderColor = /^[0-9a-f]{6}$/i.test(colorHex) ? `#${colorHex}` : "transparent";
  } catch (err) {
    versionBadge.textContent = "";
  }
}

async function loadPersonas() {
  const [res] = await Promise.all([fetch("/api/personas"), loadFaceData(), loadVersionBadge()]);
  const personas = await res.json();
  personaTabs.innerHTML = "";
  personas.forEach((p) => {
    PERSONA_NAMES[p.id] = p.display_name;
    PERSONA_ADDRESS_NAMES[p.id] = p.address_name;
    PERSONA_COLORS[p.id] = { color: p.color, background: p.background_color };
    PERSONA_FACES[p.id] = {
      face_eyebrows: p.face_eyebrows,
      face_eyes: p.face_eyes,
      face_mouth: p.face_mouth,
      face_hairstyle: p.face_hairstyle,
      face_beard: p.face_beard,
    };
    const btn = document.createElement("button");
    btn.className = "persona-tab";
    btn.dataset.persona = p.id;
    btn.innerHTML = `
      <span class="avatar-shape-bg avatar-icon-bg" data-persona="${p.id}">
        ${avatarSvg(p.id, FACE_DATA, faceFor(p.id))}
      </span>
      <span class="tab-label">${p.display_name}</span>
    `;
    applyPersonaColor(btn.querySelector(".avatar-icon-bg"), p.id);
    btn.addEventListener("click", () => addressPersona(p.id));
    personaTabs.appendChild(btn);
  });
  systemReady = true;
  updateChatHint();
  stageIdle();
  applyUiMode();
  connect();
  startListening();
}

// Klick auf ein Taskleisten-Icon spricht die Person an, statt die
// Verbindung neu aufzubauen - im Gruppenchat gibt es nur EINEN Socket
// (siehe connect() unten). Nutzt dieselbe Namens-Ansprache, die der
// Server auch bei getipptem/gesprochenem Text erkennt (room.py),
// deshalb kein zweiter Erkennungsweg noetig.
function addressPersona(personaId) {
  const name = PERSONA_ADDRESS_NAMES[personaId];
  if (!name) return;
  const current = textInput.value.trimStart();
  if (!current.toLowerCase().startsWith(name.toLowerCase())) {
    textInput.value = current ? `${name}, ${current}` : `${name}, `;
  }
  textInput.focus();
  const len = textInput.value.length;
  textInput.setSelectionRange(len, len);
}

function markPersonaActive(personaId) {
  document.querySelectorAll(".persona-tab").forEach((el) => {
    el.classList.toggle("active", el.dataset.persona === personaId);
  });
}

// Kleine Taskleisten-Kachel: .present-Klasse aus derselben Liste
// gespeist wie die grosse Buehne (siehe renderAvatarStage()) - dadurch
// inklusive Entfernen nach Timeout, was vorher gar nicht moeglich war.
function syncPersonaPresentTabs(presentIds) {
  const presentSet = new Set(presentIds);
  document.querySelectorAll(".persona-tab").forEach((tab) => {
    tab.classList.toggle("present", presentSet.has(tab.dataset.persona));
  });
}

// --- WebSocket-Verbindung ---------------------------------------------
//
// Ein gemeinsamer Raum-Socket fuer alle Personas (Gruppenchat, siehe
// /ws/room/{user_id} in main.py) statt einer Verbindung pro Person -
// welche Persona gerade antwortet, steht im "persona"-Feld jeder
// token/done-Nachricht und bestimmt currentPersona von dort aus.

function connect() {
  if (socket) socket.close();
  const proto = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${proto}://${location.host}/ws/room/${USER_ID}`);

  let assistantBubble = null;
  // Wie weit assistantBubble.textContent bereits an die Sprachausgabe
  // gereicht wurde (siehe speakReadyChunks() unten) - satzweises/
  // haeppchenweises Streaming braucht einen Merker, damit derselbe Text
  // nicht mehrfach eingereiht wird.
  let spokenOffset = 0;

  socket.addEventListener("message", (event) => {
    // Pausiert: Sprachausgabe UND Chat stehen still - eingehende
    // Nachrichten (z.B. ein Auto-Turn, der server-seitig laengst nicht
    // weiss, dass gerade pausiert ist) werden einfach ignoriert, bis
    // resumeSystem() wieder aktiviert. Was zuletzt VOR der Pause gesagt
    // wurde, bleibt in lastUtteranceText/lastUtterancePersona erhalten
    // (siehe pauseSystem()) und wird beim Fortsetzen wiederholt.
    if (paused) return;
    const msg = JSON.parse(event.data);
    if (msg.type === "token") {
      if (msg.persona && msg.persona !== currentPersona) {
        currentPersona = msg.persona;
        markPersonaActive(currentPersona);
      }
      if (!assistantBubble) {
        assistantBubble = addBubble("", "assistant", currentPersona);
        spokenOffset = 0;
        // Neue, eigenstaendige Aeusserung - alles vorher noch
        // Wartende/Laufende verwerfen (z.B. ein Auto-Turn, der gerade
        // noch abgespielt wurde).
        stopCurrentSpeech();
      }
      assistantBubble.textContent += msg.content;
      chatArea.scrollTop = chatArea.scrollHeight;
      // Merkt sich den (evtl. noch unvollstaendigen) Text als "zuletzt
      // Gesagtes" - Grundlage fuer den Wiedereinstiegs-Satz nach einer
      // Pause (siehe pauseSystem()/resumeSystem()), unabhaengig davon,
      // ob diese Aeusserung noch fertig wird oder mittendrin
      // unterbrochen wird.
      lastUtteranceText = assistantBubble.textContent;
      lastUtterancePersona = currentPersona;
      // Optimistisch: sofort als anwesend/sprechend anzeigen, noch bevor
      // die naechste presence-Nachricht das bestaetigt (Server schickt
      // presence erst wieder beim naechsten Zug/Tick, siehe main.py) -
      // gleiche gefuehlte Reaktionsgeschwindigkeit wie zuvor.
      speakingPersona = currentPersona;
      markPersonaPresentOnStage(currentPersona);
      renderAvatarStage();
      spokenOffset = speakReadyChunks(assistantBubble.textContent, spokenOffset, false);
    } else if (msg.type === "done") {
      if (msg.persona) currentPersona = msg.persona;
      markPersonaActive(currentPersona);
      markPersonaPresentOnStage(currentPersona);
      renderAvatarStage();
      if (assistantBubble) {
        spokenOffset = speakReadyChunks(assistantBubble.textContent, spokenOffset, true);
      }
      assistantBubble = null;
    } else if (msg.type === "presence") {
      applyPresenceFromServer(msg.present || []);
      if (speakingPersona && !presentPersonas.has(speakingPersona)) {
        speakingPersona = null;
      }
      renderAvatarStage();
    } else if (msg.type === "blocked") {
      addBubble("Diese Nachricht konnte ich so nicht beantworten.", "notice");
      assistantBubble = null;
      // Kein stageIdle() hier - niemandes Anwesenheit ist betroffen
      // (kein persona-Feld), bereits anwesende Personas bleiben
      // sichtbar, nur niemand "spricht" gerade.
      speakingPersona = null;
      renderAvatarStage();
    }
  });
}

// --- Nachrichten senden ----------------------------------------------

function addBubble(text, role, persona) {
  const div = document.createElement("div");
  div.className = `bubble ${role}${persona ? " " + persona : ""}`;
  div.textContent = text;
  chatArea.appendChild(div);
  chatArea.scrollTop = chatArea.scrollHeight;
  return div;
}

function isCurrentlySpeaking() {
  return (
    speechQueue.length > 0
    || currentAudio !== null
    || Boolean(window.speechSynthesis && window.speechSynthesis.speaking)
  );
}

// Kurze, vorab synthetisierte Reaktion einer Persona auf eine
// Gespraechssituation (siehe server/reaction_audio.py) - bewusst
// generisch ueber "situation" gehalten ("interrupted", "resumed"),
// damit spaetere Situationen denselben Weg nutzen koennen. user_id wird
// mitgeschickt, damit Situationen mit Du/Sie-Varianten (z.B. "resumed")
// die richtige Form waehlen (siehe main.py's /api/reaction). Schlaegt
// der Abruf fehl oder hat die Persona nichts hinterlegt (404), bleibt
// sie einfach still - kein Fehlerfall, keine Rueckfallebene noetig.
// Loest sich erst auf, wenn die Wiedergabe TATSAECHLICH beendet ist
// (nicht schon beim Start) - resumeSystem() braucht das, um den
// Wiedereinstiegs-Satz erst NACH der Reaktion zu sprechen, nicht
// gleichzeitig darueber.
async function playReaction(personaId, situation) {
  const myGeneration = speechGeneration;
  try {
    const res = await fetch(`/api/reaction/${personaId}/${situation}?user_id=${encodeURIComponent(USER_ID)}`);
    if (!res.ok) return;
    const blob = await res.blob();
    if (myGeneration !== speechGeneration) return; // laengst ueberholt
    await playPreparedAudio(blob);
  } catch (err) {
    // still scheitern - eine fehlende Reaktion ist kein Problem
  }
}

// Spielt einen bereits geholten Audio-Blob ab (z.B. waehrend einer
// Pause vorab besorgtes "resumed"-Audio, siehe pauseSystem()) - kein
// erneuter Netzwerk-Aufruf noetig, daher ohne Verzoegerung abspielbar.
function playPreparedAudio(blob) {
  return new Promise((resolve) => {
    const audio = new Audio(URL.createObjectURL(blob));
    currentAudio = audio;
    lastSpeechEndTs = null; // eigene Ausgabe beginnt - kein Sprechpausen-Messwert daraus ableiten
    audio.addEventListener("ended", () => {
      if (currentAudio === audio) currentAudio = null;
      resolve();
    });
    audio.play();
  });
}

// Kernlogik des Sendens, unabhaengig davon, ob die Sprechblase gerade
// erst angelegt wurde (manuelles Tippen, sendMessage() unten) oder
// schon laenger als wachsende Diktier-Blase sichtbar war (Ablauf der
// Grace Period, siehe commitPendingUtterance()) - beide Wege sollen
// exakt dieselbe Unterbrechen-/Aufheben-/Senden-Logik durchlaufen,
// unterscheiden sich nur darin, WOHER die Blase kommt.
function sendText(text, bubble) {
  // Aktives Senden ist ein eindeutiges "ich bin wieder da, JETZT" - hebt
  // eine laufende Pause mit auf. Bewusst OHNE resumeSystem()s "Willkommen
  // zurueck"-Aeusserung/Wiederholung des letzten Satzes: die Person hat
  // gerade aktiv etwas Neues gesagt/getippt, braucht keine Wiedereinstiegs-
  // hilfe. Muss VOR dem eigentlichen Senden passieren - sonst wuerde die
  // eintreffende Antwort vom weiterhin "paused" websocket-Handler
  // stillschweigend verworfen (siehe dort).
  if (paused) {
    setPausedUiState(false);
    preparedResumeAudio = null;
    startListening();
  }
  bubble.classList.remove("pending");
  const wasInterrupted = isCurrentlySpeaking();
  const interruptedPersona = currentPersona;
  // Unterbricht eine noch laufende Ansage sofort (auch mitten im Satz) -
  // wer der Person gerade zuhoert, soll aufhoeren zu reden, sobald sie
  // selbst etwas sagt, wie in einem echten Gespraech auch.
  stopCurrentSpeech();
  if (currentPersona) {
    speakingPersona = null;
    renderAvatarStage();
  }
  if (wasInterrupted && interruptedPersona) {
    playReaction(interruptedPersona, "interrupted");
  }
  socket.send(text);
}

function sendMessage() {
  const text = textInput.value.trim();
  if (!text || !socket || socket.readyState !== WebSocket.OPEN) return;
  const bubble = addBubble(text, "user");
  sendText(text, bubble);
  textInput.value = "";
}

sendBtn.addEventListener("click", sendMessage);
textInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// --- Sprache: Geraet (Web Speech API) oder Server (speech-service/) --
// Hinweis Geraet-Modus: Erkennungsqualitaet und Sprachverfuegbarkeit
// haengen vom Geraet/Browser ab; Chrome schickt die Aufnahme dabei an
// Googles Server (siehe docs/ARCHITECTURE.md). Der Server-Modus laeuft
// komplett lokal (Tailnet), siehe README, Abschnitt "Sprache auf dem
// Server".

const latencyBadge = document.getElementById("latencyBadge");
const latencyBadgeIcon = document.getElementById("latencyBadgeIcon");
const latencyBadgeLabel = document.getElementById("latencyBadgeLabel");
const latencyBadgeValue = document.getElementById("latencyBadgeValue");
let latencyBadgeHideTimer = null;

// Ersetzt die fruehere Pro-Haeppchen-Sprechblase (Session-Notiz
// 2026-09-24: erschien/verschwand bei JEDEM einzelnen Sprachausgabe-
// Haeppchen neu und liess die Chat-Ansicht "unruhig" wirken). Ein
// einziges, fest positioniertes Element (siehe .latency-badge,
// index.html) wird nur noch in-place aktualisiert - kein DOM-Auf-/Abbau
// mehr, also kein Sprechblasen-Reflow. Reine Entwicklungs-/Diagnose-
// Anzeige: misst die Laufzeit des jeweiligen /api/tts- bzw.
// /api/stt-Roundtrips zum Server, nicht die Laenge der Aufnahme/Antwort
// selbst.
function showLatencyBadge(icon, label, seconds) {
  latencyBadgeIcon.textContent = icon;
  latencyBadgeLabel.textContent = label;
  latencyBadgeValue.textContent = `${seconds}s`;
  latencyBadge.hidden = false;
  clearTimeout(latencyBadgeHideTimer);
  latencyBadgeHideTimer = setTimeout(() => { latencyBadge.hidden = true; }, 4000);
}

// Dauer-Zuhoeren statt Knopf-gedrueckt-halten (siehe pauseBtn weiter
// unten): der Recognizer laeuft, solange das System aktiv/nicht
// pausiert ist, und startet sich nach jeder erkannten/verworfenen
// Aeusserung ("end") automatisch neu (continuous=true allein reicht in
// der Praxis nicht - viele Browser beenden trotzdem nach jeder
// Aeusserung). WICHTIG gegen ein Feedback-Problem (Geraet hoert seine
// eigene Sprachausgabe mit, da Mikrofon und Lautsprecher meist im
// selben Tablet stecken): "result" wird verworfen, solange isCurrentlySpeaking()
// wahr ist - kein Ergebnis waehrend/kurz nach eigener Ausgabe wird als
// Eingabe gewertet. Nicht auf realer Hardware getestet (siehe Notiz an
// den Nutzer) - Zeitfenster/Empfindlichkeit koennten nach dem ersten
// echten Einsatz noch Anpassung brauchen.
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognizer = null;

// Wie lange zwischen zwei erkannten Sprachphrasen Stille lag - reine
// Rohdaten-Sammlung (siehe server/speech_timing.py), noch OHNE jede
// Verhaltensaenderung. Spaeter Grundlage fuer eine Graceperiod, bevor
// die Companion-Persona eine Pause als "fertig gesprochen" wertet,
// statt der Person ins Wort zu fallen (Session-Notiz 2026-09-22) - bis
// dahin bewusst nur beobachten, nicht schon reagieren.
let lastSpeechEndTs = null;

function reportSpeechPause() {
  if (lastSpeechEndTs === null) return;
  const seconds = (Date.now() - lastSpeechEndTs) / 1000;
  lastSpeechEndTs = null;
  // Grobe Plausibilitaets-Grenze: eine "Pause" ueber 30s ist mit hoher
  // Wahrscheinlichkeit durch eine System-Pause oder etwas anderes
  // Fremdes verzerrt, nicht eine echte Sprechpause - solche Ausreisser
  // wuerden die Verteilung nur verfaelschen, nicht informieren.
  if (seconds <= 0 || seconds >= 30) return;
  fetch(`/api/speech-pause/${encodeURIComponent(USER_ID)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ seconds }),
  }).catch(() => {});
}

// --- Grace-Period-Diktat --------------------------------------------
//
// Erkannter Sprachtext wandert NICHT ins Eingabefeld (ein normales
// <textarea> kann keine mehrfarbige Schrift darstellen), sondern
// direkt in eine wachsende "Entwurfs"-Sprechblase oben im Chat - jeder
// weitere erkannte Fetzen haengt sich an, bis wirklich Stille herrscht
// (Session-Notiz 2026-09-25). Das Eingabefeld bleibt so exklusiv fuers
// manuelle Tippen frei. Bewusst eine FESTE Grace-Period-Dauer statt
// einer adaptiven/statistischen (siehe speech_timing.py - reine
// Rohdaten-Sammlung bisher, keine echte Kalibrierungsgrundlage) - das
// ist die erste tatsaechliche Umsetzung der bisher nur besprochenen
// Grace-Period-Idee, absichtlich einfach gehalten.
const GRACE_PERIOD_MS = 2000;

let pendingUtteranceBubble = null; // DOM-Element der wachsenden Entwurfs-Blase
let pendingUtteranceText = "";     // bisher akkumulierter Text dieser Aeusserung
let graceTimeoutId = null;         // sendet bei Ablauf tatsaechlich
let graceProgressIntervalId = null; // fuellt sendBtn's Fortschrittsanzeige

// Haengt einen neu erkannten (fertigen) Textfetzen an die laufende
// Diktier-Blase an - legt sie beim allerersten Aufruf neu an. Zaehlt
// selbst schon als "neues Wort" und verlaengert daher die Grace Period.
function appendToPendingUtterance(text) {
  if (!text) return;
  if (!pendingUtteranceBubble) {
    pendingUtteranceText = text;
    pendingUtteranceBubble = addBubble(text, "user");
    pendingUtteranceBubble.classList.add("pending");
  } else {
    pendingUtteranceText += " " + text;
    pendingUtteranceBubble.textContent = pendingUtteranceText;
  }
  resetGraceTimer();
}

// Startet die Grace Period neu (bei jedem neuen Wort aufgerufen, ob
// Zwischen- oder Endergebnis) - laesst die Fortschrittsanzeige am
// Senden-Knopf von vorne beginnen und verschiebt den tatsaechlichen
// Sendezeitpunkt.
function resetGraceTimer() {
  clearTimeout(graceTimeoutId);
  clearInterval(graceProgressIntervalId);
  const start = performance.now();
  sendBtn.style.setProperty("--grace-progress", "0");
  graceProgressIntervalId = setInterval(() => {
    const fraction = Math.min(1, (performance.now() - start) / GRACE_PERIOD_MS);
    sendBtn.style.setProperty("--grace-progress", String(fraction));
  }, 80);
  graceTimeoutId = setTimeout(commitPendingUtterance, GRACE_PERIOD_MS);
}

// Die Grace Period ist wirklich abgelaufen (kein neues Wort mehr) -
// jetzt erst geht der akkumulierte Text tatsaechlich raus, exakt wie
// ein manueller Klick auf "Senden" (siehe sendText()).
function commitPendingUtterance() {
  clearTimeout(graceTimeoutId);
  clearInterval(graceProgressIntervalId);
  sendBtn.style.setProperty("--grace-progress", "0");
  if (!pendingUtteranceBubble) return;
  const bubble = pendingUtteranceBubble;
  const text = pendingUtteranceText;
  pendingUtteranceBubble = null;
  pendingUtteranceText = "";
  sendBtn.classList.add("just-sent");
  setTimeout(() => sendBtn.classList.remove("just-sent"), 300);
  sendText(text, bubble);
}

// Fuer den Pause-Fall (siehe pauseSystem()): eine laufende Diktier-Blase
// gilt als verworfen, nicht eingefroren - konsistent damit, dass eine
// Pause sonst auch alles Laufende abraeumt (stopCurrentSpeech() etc.).
// Nach dem Fortsetzen kann die Person einfach neu ansetzen.
function cancelPendingUtterance() {
  clearTimeout(graceTimeoutId);
  clearInterval(graceProgressIntervalId);
  sendBtn.style.setProperty("--grace-progress", "0");
  if (pendingUtteranceBubble) pendingUtteranceBubble.remove();
  pendingUtteranceBubble = null;
  pendingUtteranceText = "";
}

if (SpeechRecognition) {
  recognizer = new SpeechRecognition();
  recognizer.lang = "de-AT";
  recognizer.interimResults = true;
  recognizer.continuous = true;

  recognizer.addEventListener("result", (event) => {
    if (paused || isCurrentlySpeaking()) return; // eigene Stimme nicht als Eingabe werten
    if (sttMode !== "device") return; // Server-Modus: siehe stopServerRecording()/speechstart unten
    // Ab resultIndex iterieren (Standard-Muster der Web Speech API) -
    // event.results kann bei aktivem interimResults mehrere neue
    // Eintraege seit dem letzten Ereignis enthalten.
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i];
      if (result.isFinal) {
        appendToPendingUtterance(result[0].transcript.trim());
      } else if (pendingUtteranceBubble) {
        // Nur verlaengern, wenn schon eine Blase existiert - der
        // allererste Text kommt erst mit dem ersten FINALEN Ergebnis,
        // nicht schon mit einem noch unsicheren Zwischenergebnis.
        resetGraceTimer();
      }
    }
  });
  recognizer.addEventListener("speechstart", () => {
    // Nur melden, wenn weder pausiert noch die eigene Sprachausgabe
    // gerade laeuft - sonst wuerde die Zeit, die das System selbst
    // geredet/pausiert hat, faelschlich als "Sprechpause der Person"
    // gezaehlt (siehe lastSpeechEndTs-Reset bei speakingPersona/pauseSystem()).
    if (!paused && !isCurrentlySpeaking()) reportSpeechPause();
    if (sttMode === "server" && listening && !paused && !isCurrentlySpeaking() && !isServerRecording) {
      startServerRecording();
    }
    // Server-Modus hat keine Zwischenergebnisse (Whisper arbeitet nicht
    // streamend) - "die Person redet wieder" ist hier der Ersatz dafuer,
    // um eine laufende Grace Period rechtzeitig zu verlaengern.
    if (sttMode === "server" && pendingUtteranceBubble) {
      resetGraceTimer();
    }
  });
  recognizer.addEventListener("speechend", () => {
    lastSpeechEndTs = Date.now();
    if (sttMode === "server" && isServerRecording) stopServerRecording();
  });
  recognizer.addEventListener("end", () => {
    // Nach jeder Aeusserung (oder Stille-Timeout) neu starten, solange
    // wir weiter zuhoeren sollen - das IST das Dauer-Zuhoeren.
    if (listening && !paused) {
      try { recognizer.start(); } catch (err) { /* laeuft evtl. schon */ }
    }
  });
  recognizer.addEventListener("error", () => {
    // Manche Browser feuern bei z.B. "no-speech" "error" statt "end" -
    // trotzdem weiter zuhoeren, statt endgueltig stillzustehen.
    if (listening && !paused) {
      try { recognizer.start(); } catch (err) { /* laeuft evtl. schon */ }
    }
  });
}

// --- Mikrofon-Pegelanzeige -------------------------------------------
//
// Im Geraete-Modus greift die Web Speech API selbst (browser-intern)
// aufs Mikrofon zu - unser Code bekommt dabei nie den rohen Pegel zu
// sehen. Einzige Loesung: ein eigener, unabhaengiger getUserMedia-Aufruf
// rein zur Visualisierung, parallel zur Spracherkennung. Mehrfacher
// gleichzeitiger Mikrofonzugriff ist technisch unproblematisch; nicht
// auf echter Hardware verifiziert ist, ob manche Browser dafuer ein
// zweites Berechtigungs-Prompt zeigen statt die schon erteilte Erlaubnis
// stillschweigend wiederzuverwenden (Session-Notiz 2026-09-26).
let micAudioCtx = null;
let micMonitorStream = null;
let micAnalyser = null;
let micLevelRafId = null;

// startListening() (und damit startMicLevelMeter()) laeuft schon beim
// Laden der Seite automatisch los, ganz ohne vorherigen Klick/Tipp -
// Chrome-artige Browser erzwingen dann aber, dass ein frisch erzeugter
// AudioContext im Zustand "suspended" startet (Autoplay-Schutz), bis
// irgendeine echte Nutzer-Interaktion auf der Seite stattfand. Ohne
// diesen Kniff bliebe die Pegelanzeige bei reiner Sprachbedienung (ganz
// ohne Klick) fuer immer flach, obwohl das Mikrofon laengst etwas
// aufnimmt (Session-Notiz 2026-09-26).
function resumeMicAudioCtxIfSuspended() {
  if (micAudioCtx && micAudioCtx.state === "suspended") {
    micAudioCtx.resume().catch(() => {});
  }
}
["click", "touchstart", "keydown"].forEach((evt) => {
  document.addEventListener(evt, resumeMicAudioCtxIfSuspended, { passive: true });
});

async function startMicLevelMeter() {
  if (micMonitorStream) return; // laeuft schon
  try {
    micMonitorStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    return; // stummes Scheitern - STT selbst haengt nicht an dieser Anzeige
  }
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  micAudioCtx = new AudioCtx();
  resumeMicAudioCtxIfSuspended(); // greift, falls schon vorher irgendwo geklickt wurde
  const source = micAudioCtx.createMediaStreamSource(micMonitorStream);
  micAnalyser = micAudioCtx.createAnalyser();
  micAnalyser.fftSize = 512;
  source.connect(micAnalyser); // NICHT an destination haengen - kein Echo
  drawMicLevel();
}

function drawMicLevel() {
  if (!micAnalyser) return;
  const data = new Uint8Array(micAnalyser.fftSize);
  micAnalyser.getByteTimeDomainData(data);
  let sumSquares = 0;
  for (let i = 0; i < data.length; i++) {
    const centered = (data[i] - 128) / 128;
    sumSquares += centered * centered;
  }
  const rms = Math.sqrt(sumSquares / data.length);
  // Verstaerkungsfaktor grob geschaetzt (normale Sprache soll sichtbar
  // ausschlagen) - nicht auf echter Hardware kalibriert, ggf. nach dem
  // ersten echten Einsatz anpassen.
  const level = Math.min(1, rms * 4);
  micLevelMeter.style.setProperty("--mic-level", String(level));
  micLevelRafId = requestAnimationFrame(drawMicLevel);
}

function stopMicLevelMeter() {
  if (micLevelRafId) cancelAnimationFrame(micLevelRafId);
  micLevelRafId = null;
  micAnalyser = null;
  if (micMonitorStream) micMonitorStream.getTracks().forEach((t) => t.stop());
  micMonitorStream = null;
  if (micAudioCtx) micAudioCtx.close().catch(() => {});
  micAudioCtx = null;
  micLevelMeter.style.setProperty("--mic-level", "0");
}

function startListening() {
  if (paused || listening) return;
  listening = true;
  if (recognizer) {
    try { recognizer.start(); } catch (err) { /* laeuft evtl. schon */ }
  }
  startMicLevelMeter();
}

function stopListening() {
  listening = false;
  if (recognizer) {
    try { recognizer.stop(); } catch (err) { /* laeuft evtl. schon nicht */ }
  }
  if (isServerRecording) stopServerRecording();
  stopMicLevelMeter();
}

let mediaRecorder = null;
let mediaStream = null;
let recordedChunks = [];
let isServerRecording = false;

async function startServerRecording() {
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    addBubble("Mikrofonzugriff wurde nicht erlaubt.", "notice");
    return;
  }
  recordedChunks = [];
  mediaRecorder = new MediaRecorder(mediaStream);
  mediaRecorder.addEventListener("dataavailable", (e) => {
    if (e.data.size > 0) recordedChunks.push(e.data);
  });
  mediaRecorder.start();
  isServerRecording = true;
  pauseBtn.classList.add("recording");
}

async function stopServerRecording() {
  isServerRecording = false;
  pauseBtn.classList.remove("recording");

  const stopped = new Promise((resolve) => {
    mediaRecorder.addEventListener("stop", resolve, { once: true });
  });
  mediaRecorder.stop();
  await stopped;
  mediaStream.getTracks().forEach((track) => track.stop());

  const blob = new Blob(recordedChunks, { type: mediaRecorder.mimeType || "audio/webm" });
  const start = performance.now();
  try {
    const form = new FormData();
    form.append("audio", blob, "aufnahme.webm");
    const res = await fetch("/api/stt", { method: "POST", body: form });
    if (!res.ok) throw new Error("Sprachdienst antwortete mit Fehler");
    const data = await res.json();
    const seconds = ((performance.now() - start) / 1000).toFixed(1);
    showLatencyBadge("🎙️", "Server", seconds);
    if (data.text) {
      appendToPendingUtterance(data.text.trim());
    }
  } catch (err) {
    addBubble("Spracherkennung auf dem Server war nicht erreichbar.", "notice");
  }
}

if (!SpeechRecognition && !navigator.mediaDevices) {
  pauseBtn.title = "Spracherkennung wird von diesem Browser nicht unterstützt.";
}

// --- Pause/Fortsetzen ---------------------------------------------------
//
// Ersetzt den frueheren Knopf-gedrueckt-halten-Mikrofonknopf: das
// System hoert per Voreinstellung dauerhaft zu (startListening() in
// loadPersonas()), der Knopf dient nur noch dazu, ALLES kurz
// anzuhalten (z.B. Toilettengang, Telefonanruf) und spaeter wieder
// aufzunehmen - siehe Anfrage vom 2026-09-22.

// Nur der Knopf-/Zustands-Teil, den pauseSystem()/resumeSystem() UND
// das stille Aufheben beim Senden waehrend einer Pause (siehe
// sendMessage()) gemeinsam brauchen - OHNE die "Willkommen zurueck"-
// Aeusserung/Wiederholung, die nur beim expliziten ▶️-Knopfdruck
// passend ist (siehe resumeSystem()).
function setPausedUiState(isPaused) {
  paused = isPaused;
  pauseBtn.textContent = isPaused ? "▶️" : "⏸️";
  pauseBtn.setAttribute("aria-label", isPaused ? "Weiter" : "Pause");
  pauseBtn.classList.toggle("paused", isPaused);
}

function pauseSystem() {
  if (paused) return;
  setPausedUiState(true);
  pauseBtn.classList.remove("recording");
  stopCurrentSpeech();
  stopListening();
  cancelPendingUtterance(); // eine laufende Diktier-Blase gilt als verworfen, nicht eingefroren
  lastSpeechEndTs = null; // Pausendauer selbst ist keine echte Sprechpause der Person

  // Waehrend der Pause schon mal das "Schoen, dass Sie/du wieder da
  // sind"-Audio besorgen (siehe reaction_audio.py) - beim tatsaechlichen
  // Fortsetzen dann ohne Netzwerk-Wartezeit sofort abspielbar. Wird nur
  // uebernommen, falls immer noch pausiert ist, wenn die Antwort
  // eintrifft (sonst koennte ein spaetes Ergebnis einen SPAETEREN
  // Pause-Zyklus verfaelschen).
  preparedResumeAudio = null;
  if (lastUtterancePersona) {
    fetch(`/api/reaction/${lastUtterancePersona}/resumed?user_id=${encodeURIComponent(USER_ID)}`)
      .then((res) => (res.ok ? res.blob() : null))
      .then((blob) => { if (paused && blob) preparedResumeAudio = blob; })
      .catch(() => {});
  }
}

async function resumeSystem() {
  if (!paused) return;
  setPausedUiState(false);

  if (lastUtterancePersona) {
    currentPersona = lastUtterancePersona;
    markPersonaActive(currentPersona);
    if (preparedResumeAudio) {
      await playPreparedAudio(preparedResumeAudio);
    } else {
      await playReaction(lastUtterancePersona, "resumed");
    }
    preparedResumeAudio = null;
    // Der zuletzt begonnene/gesagte Textblock wird von vorne
    // wiederholt, um beim Wiedereinstieg ins Gespraech zu helfen -
    // bewusst der GANZE zuletzt bekannte Text, nicht nur der Rest ab
    // der Unterbrechung (siehe lastUtteranceText oben).
    if (lastUtteranceText) {
      speakReadyChunks(lastUtteranceText, 0, true);
    }
  }
  startListening();
}

pauseBtn.addEventListener("click", () => {
  if (paused) resumeSystem();
  else pauseSystem();
});

// Sicherheitsnetz fuer die Avatar-Buehne: falls "ended"/"end"/"error"
// aus irgendeinem Grund nie feuert (keine Stimme installiert, Geraet
// haengt), darf die Buehne nicht fuer immer im "spricht"-Zustand
// stecken bleiben - spaetestens nach einer grosszuegigen, an der
// Textlaenge orientierten Schaetzung wird sie freigegeben. Per echtem
// Test in einer Headless-Umgebung ohne installierte Stimmen gefunden
// (dort feuert speechSynthesis weder "end" noch "error").
function clearSpeakingAndRender() {
  speakingPersona = null;
  renderAvatarStage();
}

function scheduleStageSafetyNet(text) {
  const timer = setTimeout(clearSpeakingAndRender, Math.max(4000, text.length * 90));
  return () => clearTimeout(timer);
}

// --- Satzweises/haeppchenweises TTS-Streaming --------------------------
//
// Ziel: die Sprachausgabe muss nicht auf die komplette Antwort warten -
// sobald ein Haeppchen (Satz oder feste Wortanzahl, siehe ttsChunkMode)
// fertig ist, geht es schon an die Sprachausgabe, waehrend das LLM den
// Rest noch generiert. Eine Antwort besteht dadurch aus mehreren
// Haeppchen, die der Reihe nach (nicht ueberlappend, aber auch ohne
// sich gegenseitig abzubrechen) abgespielt werden muessen - anders als
// eine wirklich NEUE, unabhaengige Aeusserung (z.B. ein Auto-Turn kurz
// nach einer noch laufenden Antwort), die alles Vorherige verwerfen
// soll. Deshalb zwei getrennte Mechanismen: enqueueSpeech() reiht ein
// Haeppchen OHNE Abbruch ein, stopCurrentSpeech() leert dagegen alles
// (wird nur beim Start einer neuen Sprechblase aufgerufen, siehe
// connect()'s "token"-Handler).

// Sucht ab fromOffset das naechste VOLLSTAENDIGE Haeppchen in text -
// verlangt bei beiden Modi ein echtes, bereits eingetroffenes
// Leerzeichen NACH der Grenze (nicht nur "Text endet gerade hier"),
// sonst wuerde ein Satz/Wort faelschlich als fertig gelten, nur weil
// das naechste Token noch nicht angekommen ist. Den letzten,
// unvollstaendigen Rest holt sich speakReadyChunks() beim "done"-Flush.
function findNextChunkBoundary(text, fromOffset) {
  const remaining = text.slice(fromOffset);
  if (ttsChunkMode === "sentence") {
    const match = remaining.match(/^[\s\S]*?[.!?]+\s+/);
    if (!match) return null;
    return fromOffset + match[0].length;
  }
  const wordsNeeded = Number(ttsChunkMode);
  if (!Number.isFinite(wordsNeeded) || wordsNeeded <= 0) return null;
  const words = remaining.match(/\S+\s+/g);
  if (!words || words.length < wordsNeeded) return null;
  return fromOffset + words.slice(0, wordsNeeded).join("").length;
}

// Reiht alle seit fromOffset neu vollstaendig gewordenen Haeppchen ein;
// bei flush=true (beim "done") wird zusaetzlich ein etwaiger Rest ohne
// abschliessendes Leerzeichen/Satzzeichen als letztes Haeppchen
// eingereiht. Gibt den neuen "bereits eingereiht bis"-Offset zurueck.
function speakReadyChunks(text, fromOffset, flush) {
  let offset = fromOffset;
  for (;;) {
    const boundary = findNextChunkBoundary(text, offset);
    if (boundary === null) break;
    enqueueSpeech(text.slice(offset, boundary));
    offset = boundary;
  }
  if (flush && offset < text.length) {
    enqueueSpeech(text.slice(offset));
    offset = text.length;
  }
  return offset;
}

let currentAudio = null;
const speechQueue = [];
let queueRunning = false;

// Wettlauf-Schutz, analog zum bisherigen Mechanismus: jedes Haeppchen
// merkt sich seine Generation und bricht still ab, falls
// stopCurrentSpeech() inzwischen (waehrend eines fetch()-Wartens)
// eine neue Generation begonnen hat.
let speechGeneration = 0;

function stopCurrentSpeech() {
  speechGeneration++;
  speechQueue.length = 0;
  window.speechSynthesis?.cancel();
  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }
}

function enqueueSpeech(text) {
  if (!text || !text.trim()) return;
  speechQueue.push({ text, generation: speechGeneration });
  if (!queueRunning) runSpeechQueue();
}

async function runSpeechQueue() {
  queueRunning = true;
  while (speechQueue.length > 0) {
    const item = speechQueue.shift();
    if (item.generation !== speechGeneration) continue; // laengst ueberholt
    await speakChunk(item.text, item.generation);
  }
  queueRunning = false;
  clearSpeakingAndRender();
}

function speakChunk(text, myGeneration) {
  if (ttsMode === "server") return speakChunkOnServer(text, myGeneration);
  return speakChunkOnDevice(text, myGeneration);
}

async function speakChunkOnServer(text, myGeneration) {
  const start = performance.now();
  try {
    const res = await fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, persona_id: currentPersona }),
    });
    if (!res.ok) throw new Error("Sprachdienst antwortete mit Fehler");
    const blob = await res.blob();
    if (myGeneration !== speechGeneration) return; // ueberholt waehrend des Wartens
    const seconds = ((performance.now() - start) / 1000).toFixed(1);
    showLatencyBadge("🔊", "Server", seconds);
    await playAudio(new Audio(URL.createObjectURL(blob)), text, myGeneration);
  } catch (err) {
    if (myGeneration !== speechGeneration) return; // ueberholt waehrend des Wartens
    // Stiller Fallback aufs Geraet - das Haeppchen soll trotzdem
    // hoerbar sein, auch wenn der Sprachdienst gerade nicht laeuft.
    await speakChunkOnDevice(text, myGeneration);
  }
}

function playAudio(audio, text, myGeneration) {
  return new Promise((resolve) => {
    if (myGeneration !== speechGeneration) { resolve(); return; }
    currentAudio = audio;
    speakingPersona = currentPersona;
    lastSpeechEndTs = null; // eigene Ausgabe beginnt - kein Sprechpausen-Messwert daraus ableiten
    renderAvatarStage();
    const clearSafetyNet = scheduleStageSafetyNet(text);
    audio.addEventListener("ended", () => {
      if (currentAudio === audio) currentAudio = null;
      clearSafetyNet();
      resolve();
    });
    audio.play();
  });
}

function speakChunkOnDevice(text, myGeneration) {
  return new Promise((resolve) => {
    if (!window.speechSynthesis || !text || myGeneration !== speechGeneration) {
      resolve();
      return;
    }
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = "de-AT";
    speakingPersona = currentPersona;
    lastSpeechEndTs = null; // eigene Ausgabe beginnt - kein Sprechpausen-Messwert daraus ableiten
    renderAvatarStage();
    const clearSafetyNet = scheduleStageSafetyNet(text);
    const finish = () => { clearSafetyNet(); resolve(); };
    utter.addEventListener("end", finish);
    utter.addEventListener("error", finish);
    window.speechSynthesis.speak(utter);
  });
}

// --- Transparenz-Panel -------------------------------------------------

const panelOverlay = document.getElementById("panelOverlay");
document.getElementById("transparencyBtn").addEventListener("click", openPanel);
document.getElementById("panelClose").addEventListener("click", () => {
  panelOverlay.hidden = true;
});

const sttModeSelect = document.getElementById("sttModeSelect");
const ttsModeSelect = document.getElementById("ttsModeSelect");
const ttsChunkModeSelect = document.getElementById("ttsChunkModeSelect");
const uiModeSelect = document.getElementById("uiModeSelect");
sttModeSelect.addEventListener("change", (e) => {
  sttMode = e.target.value;
  localStorage.setItem("senior_companion_stt_mode", sttMode);
});
ttsModeSelect.addEventListener("change", (e) => {
  ttsMode = e.target.value;
  localStorage.setItem("senior_companion_tts_mode", ttsMode);
});
ttsChunkModeSelect.addEventListener("change", (e) => {
  ttsChunkMode = e.target.value;
  localStorage.setItem("senior_companion_tts_chunk_mode", ttsChunkMode);
});
uiModeSelect.addEventListener("change", (e) => {
  uiMode = e.target.value;
  localStorage.setItem("senior_companion_ui_mode", uiMode);
  applyUiMode();
});
async function openPanel() {
  panelOverlay.hidden = false;

  document.getElementById("panelUser").textContent = `Profil auf diesem Gerät: ${USER_ID}`;
  sttModeSelect.value = sttMode;
  ttsModeSelect.value = ttsMode;
  ttsChunkModeSelect.value = ttsChunkMode;
  uiModeSelect.value = uiMode;

  const logRes = await fetch(`/api/transparency/${USER_ID}`);
  const log = await logRes.json();
  const logList = document.getElementById("transparencyList");
  logList.innerHTML = log.length
    ? ""
    : "<li>Heute wurde noch nichts nach draußen geschickt.</li>";
  log.forEach((entry) => {
    const li = document.createElement("li");
    const date = new Date(entry.ts * 1000).toLocaleString("de-AT");
    li.textContent = `${date} — ${entry.plugin}: ${entry.purpose}`;
    logList.appendChild(li);
  });

  const pluginRes = await fetch("/api/plugins");
  const pluginsData = await pluginRes.json();
  const pluginList = document.getElementById("pluginList");
  pluginList.innerHTML = "";
  pluginsData.forEach((p) => {
    const li = document.createElement("li");
    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = p.enabled;
    checkbox.addEventListener("change", () => togglePlugin(p.id, checkbox.checked));
    label.appendChild(checkbox);
    label.append(` ${p.name} — ${p.description}`);
    if (p.needs_internet) {
      const domains = document.createElement("div");
      domains.style.color = "var(--ink-soft)";
      domains.style.fontSize = "16px";
      domains.textContent = `Braucht Internet: ${p.internet_domains.join(", ")}`;
      li.appendChild(label);
      li.appendChild(domains);
    } else {
      li.appendChild(label);
    }
    pluginList.appendChild(li);
  });
}

async function togglePlugin(id, enabled) {
  await fetch(`/api/plugins/${id}/toggle`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
}

// --- Lookahead-KPI (temporaeres Entwicklungs-Werkzeug) -----------------
// Siehe index.html's Kommentar neben #lookaheadKpi - zeigt live, wie
// viele Saetze lookahead.py im Voraus gebaut hat. Reine Poll-Anzeige,
// keine neue WebSocket-Nachricht - haelt main.py's/lookahead.py's
// bestehende Mechanik unangetastet, nur der neue, unauthentifizierte
// GET /api/lookahead-debug/{user_id} wird dafuer abgefragt.
const LOOKAHEAD_POLL_INTERVAL_MS = 500;

async function pollLookaheadDebug() {
  try {
    const res = await fetch(`/api/lookahead-debug/${encodeURIComponent(USER_ID)}`);
    const data = await res.json();
    if (!data || data.levels_built === undefined) {
      lookaheadKpi.hidden = true;
      return;
    }
    const audioMark = data.head_has_audio ? " 🔊" : "";
    lookaheadKpi.textContent = `Lookahead: ${data.levels_built}/${data.target_depth}${audioMark}`;
    lookaheadKpi.hidden = false;
  } catch (err) {
    // still scheitern - reine Entwicklungs-Anzeige, kein Fehlerfall
  }
}

setInterval(pollLookaheadDebug, LOOKAHEAD_POLL_INTERVAL_MS);

// --- Service Worker registrieren (PWA) --------------------------------
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/service-worker.js");
  });
}

loadPersonas();

// Wiedereinstieg vom Persona-Designer (admin.js's "Abmelden" fuehrt
// hierher zurueck, siehe dort) - Panel automatisch wieder oeffnen,
// statt die Person auf der leeren Chat-Ansicht stehen zu lassen. Hash
// sofort entfernen, sonst wuerde ein blosses Neuladen dieser Seite das
// Panel jedes Mal erneut aufreissen.
if (location.hash === "#einstellungen") {
  history.replaceState(null, "", location.pathname + location.search);
  openPanel();
}
