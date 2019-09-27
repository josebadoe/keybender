#!/bin/bash

NAME=${0##*/}
function usage {
	cat <<EOF
${NAME} [option]... [windows] focused-window

If only the focused-window is specified, the window list will be build from this
single element.

Options:
  -r --run=CMD          Command to run if no window found
  -a --action=ACTION    Execute named action after running the command. It will
                        be evaluated and may use the variable \$PID to identify the
                        executed process.
  -m --minimize         Minimize focused window if it's one of the windows list
  -M --maximize         Maximize focused window if it's one of the windows list
  -g --geometry         Set geometry of the focused window if it's one of the windows list"
  -c --close            Gracefully close focused window if it's one of the windows list
  -F --fullscreen       Toggle fullscreen
  -h --help             Display this help and exit.
  -k --key=KEYS         Send these keys to the focused window if it's one of the window list
  -A --activate         Activate the specified (probablye single) window
EOF
}

ARGS=$(getopt --name="$NAME" \
			  --options=r:a:mMcfg:w:k:Ah \
			  --longoptions=run,action,minimize,maximize,close,fullscreen,geometry:,window:,keys:,activate,help -- "$@")
if [ $? -ne 0 ]; then
    echo "Try $NAME --help for more information." >&2
    exit 1
fi

eval set -- $ARGS

run=""
ops=""
action=""
geometry=""
window_selector=""
keys=""

confirm() {
  expect="$1"
  if [[ -z "$expect" ]]; then
    expect="OK"
  fi
  while read r; do
    if [[ "$r" == "$expect" ]]; then
      return 0
    else
      return 1
    fi
  done
}

DONE=false
while [[ "$DONE" == false ]]; do
  case "$1" in
    -h|--help)
      usage
      shift
      exit 0
      ;;
    -A|--activate)
      ops="$ops activate"
      shift
      ;;
    -c|--close)
      ops="$ops close"
      shift
      ;;
    -m|--minimize)
      ops="$ops minimize"
      shift
      ;;
    -M|--maximize)
      ops="$ops maximize"
      shift
      ;;
    -g|--geometry)
      ops="$ops geometry"
      geometry="$2"
      shift 2
      ;;
    -r|--run)
      run="$2"
      shift 2
      ;;
    -w|--window)
      window_selector="$2"
      shift 2
      ;;
    -a|--action)
      action="$2"
      shift 2
      ;;
    -f|--fullscreen)
      ops="$ops fullscreen"
      shift
      ;;
    -k|--keys)
      ops="$ops send_keys"
      keys="$2"
      shift 2
      ;;
    --)
      DONE=true
      shift
      ;;
  esac
done

if [[ -z "$ops" ]]; then
  ops=" minimize "
fi

windows=($1)
if [[ "$#" == 1 ]]; then
  focused="$1"
else
  focused="$2"
fi

if [[ -z "$windows" ]]; then
  if [[ -n "$run" ]]; then
    # make stdout and stderr closeable on exiting from this script, without HUPing
    # the child process
    setsid -w $run </dev/null >/dev/null 2>/dev/null &
    PID=$!
    if [[ -n "$action" ]]; then
      echo $(eval echo $action) >&2
      echo $(eval echo $action)
    fi
    if [[ -n "$window_selector" ]]; then
      DONE=false
      echo "select-windows: $window_selector waiting 3s"
      while [[ "$DONE" != "true" ]] && read question value; do
        case "$question::$value" in
          bye::)
            DONE=true
            break
            ;;
          select-windows:$window_selector:?*)
            win_id=($value)
            if [[ -n "$geometry" ]]; then
              echo geometry: $win_id "$geometry"
            fi
            if [[ -n "$keys" ]]; then
              echo send_keys: $win_id "$keys"
            fi
            echo bye
            break
            ;;
          *)
            echo wtf "$question $value" >&2
        esac
      done
    fi
  fi
elif [[ -n "$focused" && " ${windows[@]} " == *" $focused "* ]]; then
  for op in $ops; do
    case "$op" in
      geometry)
        echo geometry: "$focused $geometry" && confirm
        ;;
      send_keys)
        echo send_keys: "$focused $keys" && confirm
        ;;
      minimize|maximize|close|fullscreen|activate)
        echo $op: "$focused" && confirm
        ;;
      *)
        echo "unhandled operation: '$op'" >&2
        exit 1
        ;;
    esac
  done
else
  echo activate:"$windows" && confirm &&
    echo raise:"$windows" && confirm &&
    echo focus:"$windows" && confirm
fi
