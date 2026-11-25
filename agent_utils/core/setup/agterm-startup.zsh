# Source from ~/.zshrc. Keeps zsh as agterm's configured shell (Live compatible).
# The first prompt, rather than .zshrc execution itself, triggers the menu:
# noninteractive explicit commands never reach this hook.
if [[ -o interactive && ${AGTERM_ENABLED:-} == 1 && -n ${AGTERM_SESSION_ID:-} && -z ${ZELLIJ:-} && ${AGTERM_UTILS_PLAIN_SHELL:-} != 1 && ${AGTERM_UTILS_MENU_SHOWN:-} != 1 ]]; then
  autoload -Uz add-zsh-hook
  _agterm_utils_first_prompt() {
    add-zsh-hook -d precmd _agterm_utils_first_prompt
    export AGTERM_UTILS_MENU_SHOWN=1
    local launcher="${AGTERM_UTILS_LAUNCHER:-$HOME/.bin/agents}"
    # No script-installed copy: use the one on PATH (a Homebrew install).
    [[ -x "$launcher" ]] || launcher="$(command -v agents 2>/dev/null)"
    if [[ -x "$launcher" ]]; then
      AGTERM_UTILS_STARTUP_HANDOFF=1 "$launcher" agterm startup
      local launch_result=$?
      # Only a successful coordinator handoff requests closing this menu shell.
      # Failed launches and all other menu outcomes keep the shell available.
      if (( launch_result == 10 )); then
        exit 0
      fi
    else
      print -u2 -- "agterm-utils: launcher unavailable: $launcher"
    fi
  }
  add-zsh-hook -d precmd _agterm_utils_first_prompt
  add-zsh-hook precmd _agterm_utils_first_prompt
fi
