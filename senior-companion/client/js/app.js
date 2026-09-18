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
let ttsMode = loadSetting("senior_companion_tts_mode", "device");

let currentPersona = null;
let socket = null;

const chatArea = document.getElementById("chatArea");
const personaTabs = document.getElementById("personaTabs");
const textInput = document.getElementById("textInput");
const sendBtn = document.getElementById("sendBtn");
const micBtn = document.getElementById("micBtn");

// --- Personas laden und Tabs aufbauen -------------------------------

async function loadPersonas() {
  const res = await fetch("/api/personas");
  const personas = await res.json();
  personaTabs.innerHTML = "";
  personas.forEach((p, i) => {
    const btn = document.createElement("button");
    btn.className = "persona-tab";
    btn.dataset.persona = p.id;
    btn.textContent = p.display_name;
    btn.addEventListener("click", () => switchPersona(p.id));
    personaTabs.appendChild(btn);
    if (i === 0) switchPersona(p.id);
  });
}

function switchPersona(personaId) {
  currentPersona = personaId;
  document.querySelectorAll(".persona-tab").forEach((el) => {
    el.classList.toggle("active", el.dataset.persona === personaId);
  });
  chatArea.innerHTML = "";
  connect(personaId);
}

// --- WebSocket-Verbindung -------------------------------------------

function connect(personaId) {
  if (socket) socket.close();
  const proto = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${proto}://${location.host}/ws/chat/${USER_ID}/${personaId}`);

  let assistantBubble = null;

  socket.addEventListener("message", (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "token") {
      if (!assistantBubble) {
        assistantBubble = addBubble("", "assistant", currentPersona);
      }
      assistantBubble.textContent += msg.content;
      chatArea.scrollTop = chatArea.scrollHeight;
    } else if (msg.type === "done") {
      if (assistantBubble) speak(assistantBubble.textContent);
      assistantBubble = null;
    } else if (msg.type === "blocked") {
      addBubble("Diese Nachricht konnte ich so nicht beantworten.", "notice");
      assistantBubble = null;
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

function sendMessage() {
  const text = textInput.value.trim();
  if (!text || !socket || socket.readyState !== WebSocket.OPEN) return;
  addBubble(text, "user");
  socket.send(text);
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

function showLatencyNotice(text) {
  const bubble = addBubble(text, "notice");
  setTimeout(() => bubble.remove(), 4000);
}

const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognizer = null;

if (SpeechRecognition) {
  recognizer = new SpeechRecognition();
  recognizer.lang = "de-AT";
  recognizer.interimResults = false;

  recognizer.addEventListener("result", (event) => {
    textInput.value = event.results[0][0].transcript;
    sendMessage();
  });
  recognizer.addEventListener("end", () => micBtn.classList.remove("recording"));
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
  micBtn.classList.add("recording");
}

async function stopServerRecording() {
  isServerRecording = false;
  micBtn.classList.remove("recording");

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
    showLatencyNotice(`Spracherkennung (Server): ${seconds}s`);
    if (data.text) {
      textInput.value = data.text;
      sendMessage();
    }
  } catch (err) {
    addBubble("Spracherkennung auf dem Server war nicht erreichbar.", "notice");
  }
}

if (SpeechRecognition || navigator.mediaDevices) {
  micBtn.addEventListener("click", () => {
    if (sttMode === "server") {
      if (!isServerRecording) startServerRecording();
      else stopServerRecording();
    } else if (recognizer) {
      micBtn.classList.add("recording");
      recognizer.start();
    }
  });
} else {
  micBtn.disabled = true;
  micBtn.title = "Spracherkennung wird von diesem Browser nicht unterstützt.";
}

async function speak(text) {
  if (!text) return;
  if (ttsMode === "server") {
    const start = performance.now();
    try {
      const res = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, persona_id: currentPersona }),
      });
      if (!res.ok) throw new Error("Sprachdienst antwortete mit Fehler");
      const blob = await res.blob();
      const seconds = ((performance.now() - start) / 1000).toFixed(1);
      showLatencyNotice(`Sprachausgabe (Server): ${seconds}s`);
      new Audio(URL.createObjectURL(blob)).play();
    } catch (err) {
      // Stiller Fallback aufs Geraet - die Antwort soll trotzdem
      // hoerbar sein, auch wenn der Sprachdienst gerade nicht laeuft.
      speakOnDevice(text);
    }
    return;
  }
  speakOnDevice(text);
}

function speakOnDevice(text) {
  if (!window.speechSynthesis || !text) return;
  const utter = new SpeechSynthesisUtterance(text);
  utter.lang = "de-AT";
  window.speechSynthesis.speak(utter);
}

// --- Transparenz-Panel -------------------------------------------------

const panelOverlay = document.getElementById("panelOverlay");
document.getElementById("transparencyBtn").addEventListener("click", openPanel);
document.getElementById("panelClose").addEventListener("click", () => {
  panelOverlay.hidden = true;
});

const sttModeSelect = document.getElementById("sttModeSelect");
const ttsModeSelect = document.getElementById("ttsModeSelect");
sttModeSelect.addEventListener("change", (e) => {
  sttMode = e.target.value;
  localStorage.setItem("senior_companion_stt_mode", sttMode);
});
ttsModeSelect.addEventListener("change", (e) => {
  ttsMode = e.target.value;
  localStorage.setItem("senior_companion_tts_mode", ttsMode);
});

async function openPanel() {
  panelOverlay.hidden = false;

  document.getElementById("panelUser").textContent = `Profil auf diesem Gerät: ${USER_ID}`;
  sttModeSelect.value = sttMode;
  ttsModeSelect.value = ttsMode;

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

// --- Service Worker registrieren (PWA) --------------------------------
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/service-worker.js");
  });
}

loadPersonas();
