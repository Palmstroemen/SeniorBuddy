// TV-/Zweitbildschirm-Anzeige: reine Empfangs-Logik, kein Avatar,
// keine Eingabe. Verbindet sich ueber /ws/display/{user_id} und zeigt
// gepushte Bilder/Videos gross an. Eigener localStorage-Schluessel,
// bewusst getrennt vom Tablet (client/js/app.js) - auch wenn in der
// Praxis derselbe ?user=-Wert auf beiden Geraeten verwendet wird, ist
// der Fernseher eine andere Geraete-Identitaet als das Tablet.
function resolveDisplayUserId() {
  const fromUrl = new URLSearchParams(location.search).get("user");
  if (fromUrl) {
    localStorage.setItem("senior_companion_display_user_id", fromUrl);
    return fromUrl;
  }
  return localStorage.getItem("senior_companion_display_user_id") || "testnutzer_1";
}
const USER_ID = resolveDisplayUserId();

const tvIdle = document.getElementById("tvIdle");
const tvImage = document.getElementById("tvImage");
const tvVideo = document.getElementById("tvVideo");

const VIDEO_EXTENSIONS = ["mp4", "webm", "mov", "m4v", "ogg"];

function isVideoUrl(url) {
  const clean = url.split("?")[0].split("#")[0];
  return VIDEO_EXTENSIONS.includes(clean.split(".").pop().toLowerCase());
}

// Beim Wechsel IMMER das jeweils andere Medienelement vollstaendig
// verstecken+leeren - sonst koennte ein Video im Hintergrund mit Ton
// weiterlaufen, waehrend gerade ein Bild gezeigt wird.
function showMedia(url) {
  tvIdle.hidden = true;
  if (isVideoUrl(url)) {
    tvImage.hidden = true;
    tvImage.removeAttribute("src");
    tvVideo.src = url;
    tvVideo.hidden = false;
    tvVideo.play().catch((err) => console.warn("Video-Autoplay blockiert:", err));
  } else {
    tvVideo.pause();
    tvVideo.hidden = true;
    tvVideo.removeAttribute("src");
    tvImage.src = url;
    tvImage.hidden = false;
  }
}

// Auto-Reconnect mit Backoff: ein Fernseher-Tab laeuft ggf.
// wochenlang und muss einen Server-Neustart/Netzwerk-Haenger ohne
// manuelles Neuladen ueberstehen - anders als app.js, dessen Seite bei
// jeder Sitzung neu geladen wird.
let socket = null;
let reconnectDelayMs = 1000;
const MAX_RECONNECT_DELAY_MS = 30000;

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${proto}://${location.host}/ws/display/${USER_ID}`);

  socket.addEventListener("open", () => {
    reconnectDelayMs = 1000;
  });

  socket.addEventListener("message", (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "display" && msg.url) {
      showMedia(msg.url);
    }
  });

  // Ein WS-"error"-Ereignis wird immer von "close" gefolgt - ein
  // Handler reicht, verhindert doppelt geplante Reconnects.
  socket.addEventListener("close", () => {
    setTimeout(connect, reconnectDelayMs);
    reconnectDelayMs = Math.min(reconnectDelayMs * 2, MAX_RECONNECT_DELAY_MS);
  });
}

connect();
