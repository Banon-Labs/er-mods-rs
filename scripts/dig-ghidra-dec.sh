#!/bin/bash
# Print one Ghidra MCP daemon answer for an address as plain text.
# usage: dig-ghidra-dec.sh <hexaddr without 0x> [method=getDecompiledCode] [port=8765]
m=${2:-getDecompiledCode}; p=${3:-8765}
python3 /home/banon/projects/er-mods-rs/scripts/ghidra/mcp_query.py "$m" --port "$p" --params "{\"address\":\"0x$1\"}" \
  | jq -r 'if (.result|type)=="string" then .result else .result|tostring end'
