#!/usr/bin/env bash
# Route a video meeting (Meet, Zoom, Teams) into the test call, so a remote interviewer can
# play the borrower. PulseAudio only; everything stays on this laptop. Wear headphones.
#
#   ./scripts/meeting_audio.sh up       create the devices and make them the defaults
#   ./scripts/meeting_audio.sh mic off  the meeting stops hearing you (the agent never hears you)
#   ./scripts/meeting_audio.sh mic on
#   ./scripts/meeting_audio.sh status
#   ./scripts/meeting_audio.sh down     remove everything and restore your old defaults
#
# Wiring after `up`:
#   meeting speaker  -> lumen_from_meeting --monitor--> browser client mic (system default source)
#   client speaker   -> lumen_agent_out    --monitor--> lumen_to_meeting --monitor--> meeting mic
#   your mic         -------------------------------->  lumen_to_meeting  (so you can talk too)
#   lumen_from_meeting and lumen_agent_out are also copied to your headphones, so you hear both.
#
# In the meeting app's settings pick: Speaker "Lumen_from_meeting", Microphone "Lumen_meeting_mic".
# Leave the call page on the system defaults.
set -euo pipefail

STATE="${XDG_RUNTIME_DIR:-/tmp}/lumen_meeting_audio.state"
LAT=latency_msec=30

die() { echo "Error: $*" >&2; exit 1; }
command -v pactl >/dev/null || die "pactl not found (PulseAudio or PipeWire-pulse is needed)"

sink() {  # name description
  pactl load-module module-null-sink sink_name="$1" sink_properties=device.description="$2" >/dev/null
}
loop() {  # source sink
  pactl load-module module-loopback source="$1" sink="$2" "$LAT" >/dev/null
}
mic_module() {  # id of the loopback carrying your mic into the meeting, if any
  pactl list short modules | awk -v m="source=$1 sink=lumen_to_meeting" 'index($0, m) {print $1}'
}

up() {
  [[ -f "$STATE" ]] && die "already up (run: $0 down)"
  local old_sink old_source
  old_sink=$(pactl get-default-sink); old_source=$(pactl get-default-source)
  printf 'old_sink=%s\nold_source=%s\n' "$old_sink" "$old_source" > "$STATE"

  sink lumen_from_meeting Lumen_from_meeting
  sink lumen_agent_out Lumen_agent_out
  sink lumen_to_meeting Lumen_to_meeting
  loop lumen_agent_out.monitor lumen_to_meeting   # agent voice -> meeting
  loop "$old_source" lumen_to_meeting             # your voice -> meeting
  loop lumen_from_meeting.monitor "$old_sink"     # you hear the interviewer
  loop lumen_agent_out.monitor "$old_sink"        # you hear the agent

  # Chrome does not list "Monitor of ..." sources as microphones, so expose real sources.
  pactl load-module module-remap-source master=lumen_to_meeting.monitor \
    source_name=lumen_meeting_mic source_properties=device.description=Lumen_meeting_mic >/dev/null
  pactl load-module module-remap-source master=lumen_from_meeting.monitor \
    source_name=lumen_agent_ears source_properties=device.description=Lumen_agent_ears >/dev/null

  pactl set-default-sink lumen_agent_out        # the call page plays here
  pactl set-default-source lumen_agent_ears     # the call page listens here
  echo "Up. In the meeting: Speaker = Lumen_from_meeting, Mic = Lumen_meeting_mic."
  echo "Then reload the call page (it picks devices when it connects). Use headphones."
}

down() {
  [[ -f "$STATE" ]] || { echo "Not up."; return; }
  # shellcheck disable=SC1090
  source "$STATE"
  pactl list short modules | awk '/lumen_/ {print $1}' | sort -rn | while read -r id; do
    pactl unload-module "$id" || true
  done
  pactl set-default-sink "$old_sink"; pactl set-default-source "$old_source"
  rm -f "$STATE"
  echo "Down. Defaults restored: $old_sink / $old_source"
}

mic() {
  [[ -f "$STATE" ]] || die "not up"
  # shellcheck disable=SC1090
  source "$STATE"
  case "${1:-}" in
    off) for id in $(mic_module "$old_source"); do pactl unload-module "$id"; done; echo "Meeting no longer hears you." ;;
    on)  [[ -n "$(mic_module "$old_source")" ]] || loop "$old_source" lumen_to_meeting; echo "Meeting hears you." ;;
    *)   die "usage: $0 mic on|off" ;;
  esac
}

status() {
  echo "default sink:   $(pactl get-default-sink)"
  echo "default source: $(pactl get-default-source)"
  pactl list short modules | grep lumen_ || echo "no lumen devices"
}

case "${1:-}" in
  up) up ;; down) down ;; mic) mic "${2:-}" ;; status) status ;;
  *) sed -n '2,17p' "$0"; exit 1 ;;
esac
