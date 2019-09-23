#!/bin/bash

socket_name=$HOME/.keybender-ctl
config=$HOME/.config/keybender.ini

# without this, screen in xfce4-terminal goes berserk...
export LANG=en_US.UTF-8

HELPERS="$(cd "$(dirname $0)" && pwd)"

NAME=${0##*/}
function usage {
  cat <<EOF
${NAME} [option]... [--] KEYBENDER-ARGUMENTS...

Options:
  -s --socket=PATH  Communication socket to use, instead of the default '$socket_name'.
  -c --config=FILE  Configuration file to use, instead of '$config'.
  -p --helpers=DIR  Directory path where to look for helpers instead of '$HELPERS'.
  -h --help         Display this help and exit.
EOF
}

ARGS=$(getopt --name="$NAME" \
			  --options=s:c:p:h \
			  --longoptions=socket:,config:,helpers:,help -- "$@")
if [ $? -ne 0 ]; then
    echo "Try $NAME --help for more information." >&2
    exit 1
fi

eval set -- $ARGS

DONE=false
while [[ "$DONE" == false ]]; do
  case "$1" in
    -h|--help)
      usage
      shift
      exit 0
      ;;
    -s|--socket)
      socket_name="$2"
      shift 2
      ;;
    -c|--config)
      config="$2"
      shift 2
      ;;
    -p|--helpers)
      HELPERS="$2"
      shift 2
      ;;
    --)
      DONE=true
      shift
      ;;
    *)
      echo "Unrecognized option '$1'"
      shift
  esac
done


function gofn {
  PID=$1
  echo select-windows:app-terminal with pid=$PID waiting 3s
  DONE=false
  while [[ "$DONE" != "true" ]] && read question value; do
    case "$question::$value" in
      OK::)
        echo ok >&2
        ;;
      bye::)
        echo "it byed me!" >&2
        DONE=true
        break
        ;;
      select-windows:app-terminal::?*)
        win_id=($value)
        echo Asked for "$question" and got "$value" >&2
        echo below:+$win_id
        echo frame:-$win_id
        echo geometry:$win_id -0!-0
        echo sticky:+$win_id
        echo skip_pager:+$win_id
        echo skip_taskbar:+$win_id
        echo bye
        ;;
      select-windows:app-terminal::)
        echo "no window found..." >&2
        echo bye
        DONE=true
        ;;
      *)
        echo wtf >&2
    esac
    echo "and i'm here again" >&2
    sleep 0.2
  done
  echo "and now i'm out of it!" >&2
}

BASE_DIR="$HOME/Documents/Org/Code"

if ! [[ -e "$config" ]]; then
  echo "Config file '$config' is missing"
  exit 3
fi

rm -f "$socket_name"
urxvt -icon /usr/share/icons/hicolor/48x48/status/input-keyboard.png \
      -title KeyBender \
      -fn xft:Monospace:pixelsize=12,style=Regular -fb xft:Monospace:pixelsize=12,style=bold \
      -bg "rgba:0000/0000/0000/5200" \
      -e /bin/bash -c "$BASE_DIR/bin/keybender/keybender -c '$config' -s '$socket_name' -o base:helpers='$HELPERS' $@ || (echo 'Press enter to close this window...'; read)" &
#urxvt &
PID=$!

for i in {0..3}; do
  if [[ -S "$socket_name" ]]; then
    break
  fi
  sleep 0.5
done

if ! [[ -S "$socket_name" ]]; then
  echo "baad"
  exit 3
fi

coproc CONNECTOR { nc -NU "$socket_name"; }

gofn $PID <&${CONNECTOR[0]} >&${CONNECTOR[1]}
