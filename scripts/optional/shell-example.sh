#!/bin/sh
# Prints only. Does not modify startup files or launch a multiplexer.
cat <<'CONFIG'
# Example zsh integration; merge manually after testing.
# Keep your current local Zellij config; remote keys belong in config-ssh.kdl.
if [[ -o interactive && -t 0 ]]; then
  stty -ixon
  if [[ -z "${ZELLIJ:-}" && "${AGTERM_ENABLED:-}" != 1 && "${AGTERM_SKIP_ZELLIJ:-}" != 1 ]]; then
    if [[ -n "${SSH_CONNECTION:-}${SSH_TTY:-}" ]]; then
      zellij --config "$HOME/.config/zellij/config-ssh.kdl" attach -c main-ssh
    else
      zellij attach -c main
    fi
  fi
fi
CONFIG
