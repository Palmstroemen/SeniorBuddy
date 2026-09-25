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
set -euo pipefail

cd "$(dirname "$0")"
mkdir -p voices

# Datei-Groesse ermitteln (Bytes) - portabel fuer GNU (Linux, gmxtec)
# UND BSD (macOS) stat, falls das je jemand lokal testet.
_file_size() {
  stat -c%s "$1" 2>/dev/null || stat -f%z "$1" 2>/dev/null || echo ""
}

fetch_voice() {
  local rel_path="$1"     # z.B. thorsten/high/de_DE-thorsten-high
  local base="https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE"
  local filename
  filename="$(basename "$rel_path")"
  local url="${base}/${rel_path}.onnx"
  local dest="voices/${filename}.onnx"

  # Session-Notiz 2026-09-24: ein fruehes "Permission denied" beim
  # allerersten Lauf (bzw. spaeter ein stiller Netzwerk-Abbruch mitten
  # im Download) hat schon ZWEIMAL eine unvollstaendige .onnx-Datei
  # hinterlassen, die "ueberspringe, existiert schon" dann faelschlich
  # als erledigt behandelt hat - klang wie ein Sprachsynthesizer aus
  # den 80ern mit Klickgeraeuschen. Darum jetzt IMMER die von
  # Hugging Face gemeldete Soll-Groesse gegen die tatsaechliche
  # Datei-Groesse pruefen, nicht nur "existiert die Datei ueberhaupt".
  local expected_size
  expected_size="$(curl -sIL --max-time 10 "$url" 2>/dev/null \
    | grep -i '^content-length:' | tail -1 | tr -dc '0-9' || true)"

  if [ -f "$dest" ]; then
    local actual_size
    actual_size="$(_file_size "$dest")"
    if [ -n "$expected_size" ] && [ "$actual_size" != "$expected_size" ]; then
      echo "== ${filename}.onnx unvollstaendig (${actual_size:-?} statt ${expected_size} Bytes) - lade neu =="
      rm -f "$dest"
    else
      echo "== ${filename}.onnx schon vollstaendig vorhanden, ueberspringe =="
    fi
  fi

  if [ ! -f "$dest" ]; then
    echo "== Lade ${filename}.onnx =="
    curl -fL --retry 3 --retry-all-errors -o "$dest" "$url"
    local downloaded_size
    downloaded_size="$(_file_size "$dest")"
    if [ -n "$expected_size" ] && [ "$downloaded_size" != "$expected_size" ]; then
      echo "FEHLER: ${filename}.onnx unvollstaendig heruntergeladen (${downloaded_size} statt ${expected_size} Bytes)." >&2
      exit 1
    fi
  fi

  if [ ! -f "voices/${filename}.onnx.json" ]; then
    curl -fL --retry 3 --retry-all-errors -o "voices/${filename}.onnx.json" "${base}/${rel_path}.onnx.json"
  fi
}

fetch_voice "thorsten/high/de_DE-thorsten-high"
fetch_voice "thorsten_emotional/medium/de_DE-thorsten_emotional-medium"
fetch_voice "mls/medium/de_DE-mls-medium"

echo ""
echo "== Fertig. Neu geladene Stimmen: =="
ls -1 voices/*.onnx

echo ""
echo "== Sprachdienst neu starten, damit GET /voices/Persona-Designer sie sieht =="
