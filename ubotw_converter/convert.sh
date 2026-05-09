#!/bin/bash

cd "$(dirname "$0")/.."

normalize_path() {
  local path="$1"
  path="${path%\'}"
  path="${path#\'}"
  path="${path%\"}"
  path="${path#\"}"
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$path" 2>/dev/null || printf '%s\n' "$path"
  else
    printf '%s\n' "$path"
  fi
}

manual_loop() {
  while true; do
    echo "Enter the path to a mod. Use quotes if the path contains spaces:"
    read -r -p "> " manual_path
    echo

    if [ -z "$manual_path" ]; then
      exit 0
    fi

    manual_path="$(normalize_path "$manual_path")"

    echo "+++++++++++++++++++++++++++"
    echo "+ Ultimate BotW Converter +"
    echo "+++++++++++++++++++++++++++"
    echo
    echo "Attempting to convert manually entered path, please wait..."
    echo
    python -m ubotw_converter.converter "$manual_path"
    status=$?
    if [ "$status" -ne 0 ]; then
      echo
      echo "Conversion failed with exit code $status."
    fi
    echo
    echo ===========
  done
}

# Manual
if [ "$#" -eq 0 ]; then
  echo "No BNP file was provided."
  echo "Drag and drop one or more .bnp files onto this script,"
  echo "or run:"
  echo "./convert.sh path/to/your.bnp [more.bnp ...]"
  manual_loop
fi

# Drag-and-drop
paths=("$@")
total=${#paths[@]}
counter=0

echo "+++++++++++++++++++++++++++"
echo "+ Ultimate BotW Converter +"
echo "+++++++++++++++++++++++++++"

for path in "${paths[@]}"; do
  counter=$((counter + 1))
  path="$(normalize_path "$path")"
  echo
  echo "Attempting to convert $counter of $total mods, please wait..."
  echo
  python -m ubotw_converter.converter "$path"
  status=$?
  if [ "$status" -ne 0 ]; then
    echo
    echo "Conversion failed with exit code $status."
  fi
done

echo
echo "Processed $counter mods."
echo
echo ===========
manual_loop
