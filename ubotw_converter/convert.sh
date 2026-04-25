#!/bin/bash

set -e
cd "$(dirname "$0")/.."
if [ "$#" -eq 0 ]; then
  echo "No BNP file was provided."
  echo "Usage: ./ubotw_converter/convert.sh path/to/your.bnp [more.bnp ...]"
  exit 1
fi
python -m ubotw_converter.converter "$@"
