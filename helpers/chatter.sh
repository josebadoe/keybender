#!/bin/bash

NAME=${0##*/}

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


display_count=""
display_type=""
wanted_dt=""
WID=""

get_display_count() {
  DONE=false
  echo "display_count"
  while [[ "$DONE" != "true" ]] && read question value; do
    case "$question::$value" in
      display_count::?*)
        display_count=$value
        DONE=true
        ;;
      *)
        echo "Strange display_count: '$value'" >&2
        display_count=1
        DONE=true
        ;;
    esac
  done
}


for a in "$@"; do
  echo "SeJ: '$a' $WID" >&2
  if [[ "$wanted_dt" && "$display_type" == "" ]]; then
    get_display_count
    if [[ "$display_count" -gt 1 ]]; then
      display_type="multi"
    else
      display_type="single"
    fi
  fi

  case "$a" in
    -windows=*)
      windows=(${a/*=})
      WID="$windows"
      ;;
    -dual*|-multi*)
      wanted_dt=multi
      ;;
    -single*)
      wanted_dt=single
      ;;
    -any)
      wanted_dt=""
      ;;
    -*)
      echo "Unrecognized option '$a'" >&2
      exit 1
      ;;
    *)
      if [[ "$WID" && ("$wanted_dt" == "" || "$wanted_dt" == "$display_type") ]]; then
        echo $(eval echo $a) && confirm
      fi
      ;;
  esac

done
