#!/bin/bash

google_music=($1)
gmpc=($2)

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

if [[ -n "$google_music" ]]; then
  echo save_state
  while read q v; do
    state=""
    case "$q::$v" in
      save_state::?*)
        state="$v"
        echo Got state >&2
        ;;
      *)
        echo "wtf: $q::$v" >&2
        ;;
    esac
    break
  done

  echo "activate: $google_music" && confirm &&
    echo "send_keys: $google_music space" && confirm &&
    echo "restore_state: $state" && confirm
    # xdotool \
    #   key --window $google_music --clearmodifiers space

  echo bye && confirm bye
elif [[ -n "$gmpc" ]]; then
		mpc toggle
fi
