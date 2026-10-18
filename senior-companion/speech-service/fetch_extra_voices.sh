#!/usr/bin/env bash
# Laedt ein paar zusaetzliche, hoeherwertige deutsche Piper-Stimmen zum
# Ausprobieren nach - separat von setup.sh (die nur die eine
# Basis-Stimme fuers Ersteinrichten holt). Ueberschreibt/loescht nichts
# Bestehendes, alles landet einfach zusaetzlich in voices/.
#
# Nur "thorsten" hat aktuell ueberhaupt eine medium/high-Stufe in
# Piper (siehe https://github.com/rhasspy/piper/blob/master/VOICES.md) -
# kerstin/ramona/pavoque (Robin/Alex/Professor Wallner) gibt es bislang
# nur als "low". Diese drei hier sind also fuers Erste testweise fuer
# Toni (technikerin, config.py) gedacht, plus die emotionale Variante
# als zusaetzliche Geschmacksprobe.
#
# de_DE-mls-medium ist die einzige weitere deutsche Piper-Stimme jenseits
# von "low" - UND ein Mehrsprecher-Modell (mehrere Stimmen in einer
# Datei, ueber speaker_id waehlbar, siehe piper.config.SynthesisConfig).
# Koennte also potenziell 2-3 unterschiedliche, hoeherwertige Stimmen
# fuer Robin/Alex/Wallner aus EINER Datei liefern - der Standard-Sprecher
# (speaker_id 0) laesst sich schon heute wie jede andere Stimme nutzen,
# ein ANDERER Sprecher braucht noch eine kleine Code-Erweiterung (heute
# ist speaker_id nirgends durchgereicht, siehe main.py::synthesize).
# Session-Notiz 2026-09-23: Stimmen-Konsistenz zwischen Personas ist in
# dieser fruehen Testphase (noch kein Rollout, keine Nutzer:innen mit
# Gewoehnungseffekt) ausdruecklich egal - frei experimentieren erlaubt.
#
# de_DE-miro-medium (Session-Notiz 2026-10-18) liegt NICHT im offiziellen
# rhasspy/piper-voices-Katalog, sondern in einem eigenen Repo
# (ufozone/piper-de_DE-miro-medium) mit anderen Dateinamen (model.onnx/
# config.json statt de_DE-miro-medium.onnx/.onnx.json) - technisch aber
# dasselbe Piper-Format, nur umbenannt. WICHTIG: Lizenz ist CC BY-NC-SA
# 4.0 (nicht-kommerziell, siehe README dort) - anders als die uebrigen
# Stimmen hier. Ausserdem auf SYNTHETISCHEN Trainingsdaten trainiert
# (Jarbas/tts-train-synthetic-miro_de-DE), nicht auf einer echten
# menschlichen Aufnahme wie Thorsten.
set -euo pipefail

cd "$(dirname "$0")"
mkdir -p voices

# Datei-Groesse ermitteln (Bytes) - portabel fuer GNU (Linux, gmxtec)
# UND BSD (macOS) stat, falls das je jemand lokal testet.
_file_size() {
  stat -c%s "$1" 2>/dev/null || stat -f%z "$1" 2>/dev/null || echo ""
}

# Session-Notiz 2026-09-24: ein fruehes "Permission denied" beim
# allerersten Lauf (bzw. spaeter ein stiller Netzwerk-Abbruch mitten im
# Download) hat schon ZWEIMAL eine unvollstaendige Datei hinterlassen,
# die "ueberspringe, existiert schon" dann faelschlich als erledigt
# behandelt hat - klang wie ein Sprachsynthesizer aus den 80ern mit
# Klickgeraeuschen. Darum IMMER die vom Server gemeldete Soll-Groesse
# gegen die tatsaechliche Datei-Groesse pruefen, nicht nur "existiert
# die Datei ueberhaupt". Generischer Download-Helfer (Session-Notiz
# 2026-10-18): urspruenglich nur in fetch_voice() fuer den offiziellen
# Piper-Katalog, jetzt auch fuer Stimmen aus anderen Repos (siehe
# fetch_voice_from_url() unten) wiederverwendet.
_download_with_size_check() {
  local url="$1"
  local dest="$2"
  local label="$3"

  local expected_size
  expected_size="$(curl -sIL --max-time 10 "$url" 2>/dev/null \
    | grep -i '^content-length:' | tail -1 | tr -dc '0-9' || true)"

  if [ -f "$dest" ]; then
    local actual_size
    actual_size="$(_file_size "$dest")"
    if [ -n "$expected_size" ] && [ "$actual_size" != "$expected_size" ]; then
      echo "== ${label} unvollstaendig (${actual_size:-?} statt ${expected_size} Bytes) - lade neu =="
      rm -f "$dest"
    else
      echo "== ${label} schon vollstaendig vorhanden, ueberspringe =="
      return 0
    fi
  fi

  echo "== Lade ${label} =="
  curl -fL --retry 3 --retry-all-errors -o "$dest" "$url"
  local downloaded_size
  downloaded_size="$(_file_size "$dest")"
  if [ -n "$expected_size" ] && [ "$downloaded_size" != "$expected_size" ]; then
    echo "FEHLER: ${label} unvollstaendig heruntergeladen (${downloaded_size} statt ${expected_size} Bytes)." >&2
    exit 1
  fi
}

fetch_voice() {
  local rel_path="$1"     # z.B. thorsten/high/de_DE-thorsten-high
  local base="https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE"
  local filename
  filename="$(basename "$rel_path")"

  _download_with_size_check "${base}/${rel_path}.onnx" "voices/${filename}.onnx" "${filename}.onnx"
  if [ ! -f "voices/${filename}.onnx.json" ]; then
    curl -fL --retry 3 --retry-all-errors -o "voices/${filename}.onnx.json" "${base}/${rel_path}.onnx.json"
  fi
}

# Fuer Stimmen ausserhalb des offiziellen rhasspy/piper-voices-Katalogs,
# mit eigenen (von "<name>.onnx"/"<name>.onnx.json" abweichenden)
# Quell-Dateinamen - z.B. Miro (siehe Kommentar oben).
fetch_voice_from_url() {
  local onnx_url="$1"
  local json_url="$2"
  local target_name="$3"  # z.B. de_DE-miro-medium

  _download_with_size_check "$onnx_url" "voices/${target_name}.onnx" "${target_name}.onnx"
  if [ ! -f "voices/${target_name}.onnx.json" ]; then
    curl -fL --retry 3 --retry-all-errors -o "voices/${target_name}.onnx.json" "$json_url"
  fi
}

fetch_voice "thorsten/high/de_DE-thorsten-high"
fetch_voice "thorsten_emotional/medium/de_DE-thorsten_emotional-medium"
fetch_voice "mls/medium/de_DE-mls-medium"
fetch_voice_from_url \
  "https://huggingface.co/ufozone/piper-de_DE-miro-medium/resolve/main/model.onnx" \
  "https://huggingface.co/ufozone/piper-de_DE-miro-medium/resolve/main/config.json" \
  "de_DE-miro-medium"

echo ""
echo "== Fertig. Neu geladene Stimmen: =="
ls -1 voices/*.onnx

echo ""
echo "== Sprachdienst neu starten, damit GET /voices/Persona-Designer sie sieht =="
