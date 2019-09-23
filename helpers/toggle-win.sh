#!/bin/bash

NAME=${0##*/}
function usage {
	cat <<EOF
${NAME} [option]... windows focused-window

Options:
  -r --run=CMD          Command to run if no window found
  -a --action=ACTION    Execute named action after running the command. It will
                        be evaluated and may use the variable \$PID to identify the
                        executed process.
  -m --minimize         Minimize focused window if it's one of the windows list
  -M --maximize         Maximize focused window if it's one of the windows list
  -c --close            Gracefully close focused window if it's one of the windows list
  -F --fullscreen       Toggle fullscreen
  -h --help             Display this help and exit.
EOF
}

ARGS=$(getopt --name="$NAME" \
			  --options=r:a:mMcfg:w:h \
			  --longoptions=run,action,minimize,maximize,close,fullscreen,geometry,window,help -- "$@")
if [ $? -ne 0 ]; then
    echo "Try $NAME --help for more information." >&2
    exit 1
fi

eval set -- $ARGS

run=""
op="minimize"
action=""
geometry=""
window_select=""

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
    -c|--close)
      op="close"
      shift
      ;;
    -m|--minimize)
      op="minimize"
      shift
      ;;
    -M|--maximize)
      op="maximize"
      shift
      ;;
    -g|--geometry)
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
      op="fullscreen"
      shift
      ;;
    --)
      DONE=true
      shift
      ;;
  esac
done


windows=($1)
focused="$2"

if [[ -z "$windows" ]]; then
  if [[ -n "$run" ]]; then
    # make stdout and stderr closeable on exiting from this script
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
            echo bye
            break
            ;;
          *)
            echo wtf "$question $value" >&2
        esac
      done
    fi
  fi
elif [[ -z "$focused" ]]; then
  echo activate:"$windows" && confirm &&
    echo raise:"$windows" && confirm &&
    echo focus:"$windows" && confirm
elif [[ " ${windows[@]} " =~ " $focused " ]]; then
  echo $op:"$focused" && confirm
else
  echo activate:"$windows" && confirm &&
    echo raise:"$windows" && confirm &&
    echo focus:"$windows" && confirm
fi
