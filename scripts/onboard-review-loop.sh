#!/usr/bin/env bash
#
# A wizard walks a human through a manual procedure, step by step.
# Generated with the wizard skill; the library below is the stock one.
#
# Everything above the "STAGES" marker is the wizard library: do not hand-edit
# it. Author the per-step stages below the marker.

set -euo pipefail

# ──────────────────────────────────────────────────────────────────────────
# Wizard library: delightful, consistent UX, identical across every wizard.
# ──────────────────────────────────────────────────────────────────────────

if [[ -t 1 ]] && command -v tput >/dev/null 2>&1 && [[ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]]; then
  BOLD=$(tput bold); DIM=$(tput dim); RESET=$(tput sgr0)
  BLUE=$(tput setaf 4); GREEN=$(tput setaf 2); YELLOW=$(tput setaf 3); RED=$(tput setaf 1)
else
  BOLD=""; DIM=""; RESET=""; BLUE=""; GREEN=""; YELLOW=""; RED=""
fi

# Author sets this at the top of the stages section.
TOTAL_STAGES=0

_STAGE_INDEX=0
ENV_FILE="${ENV_FILE:-.env}"
WRITTEN_ENV=()    # KEYs written to ENV_FILE this run
WRITTEN_SECRET=() # secret NAMEs set this run
SKIPPED=()        # things we couldn't do (e.g. gh missing)

# _clear wipes the terminal so only the current step is on screen. No-op when
# output isn't a terminal, so piped logs stay readable.
_clear() {
  [[ -t 1 ]] || return 0
  if command -v tput >/dev/null 2>&1; then tput clear; else printf '\033[2J\033[3J\033[H'; fi
}

# banner "Title" shows the opening frame: what this wizard does.
banner() {
  _clear
  printf '\n%s%s  %s%s\n' "$BOLD" "$BLUE" "$1" "$RESET"
  printf '%s  %s stages%s\n\n' "$DIM" "$TOTAL_STAGES" "$RESET"
  printf '%s  You drive the browser; this wizard tells you exactly what to do and\n' "$DIM"
  printf '  captures the values you copy back. Stop any time with Ctrl-C and re-run\n'
  printf '  later, since it remembers values already saved.%s\n' "$RESET"
  pause "Ready to start?"
}

# stage "Name" clears the screen, then announces a stage and shows progress.
# Clearing keeps only the current step on screen.
stage() {
  _clear
  _STAGE_INDEX=$((_STAGE_INDEX + 1))
  printf '\n%s%s▸ Stage %s/%s · %s%s\n' \
    "$BOLD" "$BLUE" "$_STAGE_INDEX" "$TOTAL_STAGES" "$1" "$RESET"
}

# say "..." prints a plain instruction line.
say()  { printf '  %s\n' "$1"; }
# step "..." is a numbered-feeling action the human takes in the browser.
step() { printf '  %s•%s %s\n' "$BLUE" "$RESET" "$1"; }
note() { printf '  %s%s%s\n' "$DIM" "$1" "$RESET"; }
warn() { printf '  %s⚠ %s%s\n' "$YELLOW" "$1" "$RESET"; }

# open_url URL opens it in the human's browser, cross-platform incl. WSL.
open_url() {
  local url="$1"
  printf '  %s↗ opening%s %s\n' "$GREEN" "$RESET" "$url"
  { if   command -v wslview     >/dev/null 2>&1; then wslview "$url"
    elif command -v explorer.exe >/dev/null 2>&1; then explorer.exe "$url"
    elif command -v xdg-open    >/dev/null 2>&1; then xdg-open "$url"
    elif command -v open        >/dev/null 2>&1; then open "$url"
    else warn "couldn't open a browser; visit it manually: $url"; fi
  } >/dev/null 2>&1 || warn "couldn't open a browser, so visit it manually: $url"
}

# pause "msg" waits for the human to confirm they've done the manual part.
pause() {
  printf '  %s%s%s ' "$DIM" "${1:-Press Enter to continue}" "$RESET"
  read -r _ || true
}

# confirm "question" is a y/N gate; returns success on yes.
confirm() {
  local reply=""
  printf '  %s? %s [y/N] ' "$YELLOW" "$1"
  read -r reply || true
  [[ "$reply" =~ ^[Yy] ]]
}

# _existing KEY: current value of KEY in ENV_FILE, if any.
_existing() {
  [[ -f "$ENV_FILE" ]] || return 1
  local line; line=$(grep -E "^${1}=" "$ENV_FILE" | tail -n1) || return 1
  printf '%s' "${line#*=}"
}

# ask KEY "Prompt" reads a value into $KEY. Offers the existing .env value as
# a default on re-runs (Enter keeps it). Visible input (non-secret).
ask() {
  local key="$1" prompt="$2" current input
  current=$(_existing "$key" || true)
  if [[ -n "$current" ]]; then
    printf '  %s%s%s %s[Enter keeps current]%s ' "$BOLD" "$prompt" "$RESET" "$DIM" "$RESET"
  else
    printf '  %s%s%s ' "$BOLD" "$prompt" "$RESET"
  fi
  read -r input || true
  [[ -z "$input" && -n "$current" ]] && input="$current"
  printf -v "$key" '%s' "$input"
}

# ask_secret KEY "Prompt" is like ask, but input is hidden.
ask_secret() {
  local key="$1" prompt="$2" current input
  current=$(_existing "$key" || true)
  if [[ -n "$current" ]]; then
    printf '  %s%s%s %s[Enter keeps current]%s ' "$BOLD" "$prompt" "$RESET" "$DIM" "$RESET"
  else
    printf '  %s%s%s ' "$BOLD" "$prompt" "$RESET"
  fi
  read -rs input || true
  printf '\n'
  [[ -z "$input" && -n "$current" ]] && input="$current"
  printf -v "$key" '%s' "$input"
}

# write_env KEY VALUE upserts KEY=VALUE into ENV_FILE (creates it; replaces
# any existing line). Idempotent.
write_env() {
  local key="$1" value="$2" tmp
  touch "$ENV_FILE"
  tmp=$(mktemp)
  grep -vE "^${key}=" "$ENV_FILE" > "$tmp" || true
  printf '%s=%s\n' "$key" "$value" >> "$tmp"
  mv "$tmp" "$ENV_FILE"
  WRITTEN_ENV+=("$key")
  printf '  %s✓ wrote%s %s → %s\n' "$GREEN" "$RESET" "$key" "$ENV_FILE"
}

# set_secret NAME VALUE sets a GitHub Actions repo secret via gh. Falls back
# to a warning (and records it) if gh is unavailable or unauthenticated.
set_secret() {
  local name="$1" value="$2"
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    if printf '%s' "$value" | gh secret set "$name" >/dev/null 2>&1; then
      WRITTEN_SECRET+=("$name")
      printf '  %s✓ set%s GitHub secret %s\n' "$GREEN" "$RESET" "$name"
      return
    fi
  fi
  SKIPPED+=("GitHub secret $name (set it manually: gh secret set $name)")
  warn "skipped GitHub secret $name: gh not ready; set it later"
}

# set_var NAME VALUE sets a GitHub Actions repo variable (non-secret).
set_var() {
  local name="$1" value="$2"
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    if gh variable set "$name" --body "$value" >/dev/null 2>&1; then
      printf '  %s✓ set%s GitHub variable %s\n' "$GREEN" "$RESET" "$name"
      return
    fi
  fi
  SKIPPED+=("GitHub variable $name")
  warn "skipped GitHub variable $name, gh not ready; set it later"
}

# finish clears, then shows a closing summary of everything configured.
finish() {
  _clear
  printf '\n%s%s  ✓ Setup complete%s\n' "$BOLD" "$GREEN" "$RESET"
  (( ${#WRITTEN_ENV[@]} ))    && note "wrote ${#WRITTEN_ENV[@]} value(s) to $ENV_FILE: ${WRITTEN_ENV[*]}"
  (( ${#WRITTEN_SECRET[@]} )) && note "set ${#WRITTEN_SECRET[@]} GitHub secret(s): ${WRITTEN_SECRET[*]}"
  if (( ${#SKIPPED[@]} )); then
    printf '\n'; warn "still to do by hand:"
    for s in "${SKIPPED[@]}"; do note "  - $s"; done
  fi
  printf '\n'
}


# ──────────────────────────────────────────────────────────────────────────
# STAGES: onboard a repository onto the reusable Claude code-review loop.
# Every name below must match .github/workflows/claude-code-review.yml and
# examples/caller.yml; tests/test_onboard_wizard.py fails if they drift.
# Nothing here is specific to one person or host: every coordinate is asked.
# ──────────────────────────────────────────────────────────────────────────

TOTAL_STAGES=9

# Nothing in this wizard is persisted to a dotenv file: the only secrets it handles go straight to
# `gh secret set` over stdin, and the rest are answers it re-asks on a re-run.
ENV_FILE=/dev/null
# `gh` reads the target repository from GH_REPO, so no command depends on the current directory.
unset GH_REPO

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXAMPLES_DIR="$SCRIPT_DIR/../examples"
WORKFLOW_REPO="datacrew-space/ci-workflows"

# Placeholders in examples/caller.yml, replaced from the answers.
PH_SHA='<COMMIT_SHA>'
PH_DOMAIN='<https://your-infisical-host>'
PH_SLUG='<your-project-slug>'

APPS=(ci-code-reviewer ci-review-responder ci-review-orchestrator)
CLAUDE_TOKEN_SECRET="CLAUDE_CODE_OAUTH_TOKEN"
VARIABLE_NAME="CLAUDE_REVIEW_ENABLED"

# The exact permission set each App must be granted. An installation token can only be narrowed from
# the App's own grant, so each list is the union over the tokens the workflow mints with that App.
app_perms() {
  case "$1" in
    ci-code-reviewer)        echo "contents:read pull-requests:write checks:read actions:read statuses:read" ;;
    ci-review-responder)     echo "contents:write pull-requests:write issues:write checks:read actions:read statuses:read" ;;
    ci-review-orchestrator)  echo "statuses:write pull-requests:write issues:write" ;;
    *) return 1 ;;
  esac
}

app_role() {
  case "$1" in
    ci-code-reviewer)        echo "posts the review" ;;
    ci-review-responder)     echo "answers the review and pushes small fixes to the PR branch" ;;
    ci-review-orchestrator)  echo "owns the review-verdict status, the loop comment and the needs-human label" ;;
  esac
}

app_prefix() { local p="${1^^}"; echo "${p//-/_}"; }

perm_label() {
  case "$1" in
    contents) echo "Contents" ;;
    pull-requests) echo "Pull requests" ;;
    issues) echo "Issues" ;;
    checks) echo "Checks" ;;
    actions) echo "Actions" ;;
    statuses) echo "Commit statuses" ;;
    *) echo "$1" ;;
  esac
}

# need KEY "Prompt" REGEX "what is valid": ask until the answer matches REGEX.
need() {
  local key="$1" prompt="$2" re="$3" hint="$4"
  while :; do
    ask "$key" "$prompt"
    if [[ "${!key}" =~ $re ]]; then return 0; fi
    warn "not valid. Expected: $hint"
  done
}

# need_secret KEY "Prompt": hidden entry, re-asked until non-empty. Never echoed.
need_secret() {
  local key="$1" prompt="$2"
  while :; do
    ask_secret "$key" "$prompt"
    if [[ -n "${!key}" ]]; then return 0; fi
    warn "empty. Paste the value (it is hidden as you type)."
  done
}

# app_stage NAME: create one GitHub App and install it on the target repository.
app_stage() {
  local app="$1" perm name access
  stage "GitHub App: $app"
  say "$app $(app_role "$app")."
  if [[ "$GH_OWNER_KIND" == org ]]; then
    open_url "https://github.com/organizations/${REPO_OWNER}/settings/apps/new"
  else
    open_url "https://github.com/settings/apps/new"
  fi
  step "GitHub App name: exactly '$app' (the workflow matches the bot login, so a variant breaks it)."
  note "Names are global on GitHub. If it is taken, create the App under a different owner."
  step "Homepage URL: any URL, for example the target repository's page."
  step "Webhook: untick 'Active'. Leave every account and organization permission at 'No access'."
  step "Repository permissions: set exactly these, and nothing else:"
  for perm in $(app_perms "$app"); do
    name="${perm%%:*}"; access="${perm##*:}"
    if [[ "$access" == write ]]; then access="Read and write"; else access="Read-only"; fi
    printf '      %s%-16s%s %s\n' "$BOLD" "$(perm_label "$name")" "$RESET" "$access"
  done
  step "Where can this App be installed: 'Only on this account'. Click 'Create GitHub App'."
  step "On the App's settings page, copy the 'App ID' (a number, shown near the top)."
  step "Scroll to 'Private keys' → 'Generate a private key'. A .pem file downloads."
  step "Left sidebar → 'Install App' → install on the account that owns ${REPO}, that repository only."
  warn "Keep the App ID and the .pem handy: stage 6 stores them in your secret store."
  note "This wizard never reads the .pem. After storing it, delete the downloaded copy."
  pause "App $app created, key generated and installed? Press Enter."
}

banner "Claude code-review loop: onboard a repository"

# ── 1: prerequisites ─────────────────────────────────────────────────────
stage "Prerequisites and target repository"
if ! command -v gh >/dev/null 2>&1; then
  warn "the GitHub CLI (gh) is required: https://cli.github.com"
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  warn "gh is not authenticated. Run: gh auth login   (then re-run this wizard)"
  exit 1
fi
say "gh is authenticated. You need admin rights on the repository: secrets and variables need them."
need REPO "Target repository (OWNER/NAME):" '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$' "OWNER/NAME"
REPO_OWNER="${REPO%%/*}"
export GH_REPO="$REPO"
if ! gh repo view "$REPO" >/dev/null 2>&1; then
  warn "gh cannot see $REPO with the current login. Check the name and 'gh auth status'."
  exit 1
fi
GH_OWNER_KIND=user
if [[ "$(gh api "users/$REPO_OWNER" --jq .type 2>/dev/null || true)" == Organization ]]; then
  GH_OWNER_KIND=org
fi
say "target: $REPO ($GH_OWNER_KIND-owned)"
if [[ ! -f "$EXAMPLES_DIR/caller.yml" ]]; then
  warn "examples/caller.yml not found next to this script. Run it from a checkout of $WORKFLOW_REPO."
  exit 1
fi
note "Status: the loop is v0 and unproven end to end. Try it on a test repository first."
pause "Continue?"

# ── 2: secret store coordinates ──────────────────────────────────────────
stage "Secret store (Infisical) coordinates"
say "The workflow fetches each secret from Infisical at run time. These answers go in the caller workflow."
need INFISICAL_DOMAIN "Infisical base URL (https://...):" '^https://[A-Za-z0-9._:/-]+$' "an https:// URL with no spaces"
need INFISICAL_PROJECT_SLUG "Infisical project slug:" '^[A-Za-z0-9._-]+$' "letters, digits, . _ -"
need INFISICAL_ENV_SLUG "Environment slug (Enter for the default, prod):" '^[A-Za-z0-9._-]*$' "letters, digits, . _ -"
INFISICAL_ENV_SLUG="${INFISICAL_ENV_SLUG:-prod}"
need INFISICAL_CLAUDE_PATH "Folder for the Claude token (Enter for /claude):" '^[A-Za-z0-9._/-]*$' "a folder like /claude"
INFISICAL_CLAUDE_PATH="${INFISICAL_CLAUDE_PATH:-/claude}"
need INFISICAL_GITHUB_PATH "Folder for the three Apps (Enter for /github):" '^[A-Za-z0-9._/-]*$' "a folder like /github"
INFISICAL_GITHUB_PATH="${INFISICAL_GITHUB_PATH:-/github}"

# ── 3-5: the three GitHub Apps ───────────────────────────────────────────
for _app in "${APPS[@]}"; do
  app_stage "$_app"
done

# ── 6: store keys and the Claude token ───────────────────────────────────
stage "Store the App credentials and the Claude token"
open_url "$INFISICAL_DOMAIN"
say "In project '$INFISICAL_PROJECT_SLUG', environment '$INFISICAL_ENV_SLUG', create these secrets."
say "Paste each value into Infisical yourself; this wizard never sees them."
printf '\n  %sFolder %s%s\n' "$BOLD" "$INFISICAL_GITHUB_PATH" "$RESET"
for _app in "${APPS[@]}"; do
  _p="$(app_prefix "$_app")"
  step "${_p}_APP_ID           the App ID number"
  step "${_p}_PRIVATE_KEY      the full contents of the .pem file, BEGIN and END lines included"
done
printf '\n  %sFolder %s%s\n' "$BOLD" "$INFISICAL_CLAUDE_PATH" "$RESET"
step "$CLAUDE_TOKEN_SECRET     run 'claude setup-token' in a terminal and paste what it prints"
note "Create the folders first if they do not exist. Names are case-sensitive and must match exactly."
pause "All seven secrets stored? Press Enter."

# ── 7: machine identity, repo secrets ────────────────────────────────────
stage "Machine identity and repository secrets"
say "CI authenticates to Infisical with a machine identity (Universal Auth). Labels vary by version."
step "Organization → Access Control → Identities (or Machine Identities) → Create identity."
step "Add 'Universal Auth' as its authentication method. If it offers an IP allowlist, narrow it."
step "Add the identity to project '$INFISICAL_PROJECT_SLUG' with a role that can READ secrets in"
step "  environment '$INFISICAL_ENV_SLUG', folders $INFISICAL_GITHUB_PATH and $INFISICAL_CLAUDE_PATH only."
step "On the identity's Universal Auth page: copy the Client ID, then 'Add client secret' and copy it."
note "Both values are hidden as you paste, and go to GitHub over stdin. They are not written to disk."
need_secret INFISICAL_CLIENT_ID "Paste the Client ID:"
need_secret INFISICAL_CLIENT_SECRET "Paste the Client Secret:"
set_secret INFISICAL_CLIENT_ID "$INFISICAL_CLIENT_ID"
set_secret INFISICAL_CLIENT_SECRET "$INFISICAL_CLIENT_SECRET"
unset INFISICAL_CLIENT_ID INFISICAL_CLIENT_SECRET

# ── 8: caller workflow and agent prompts ─────────────────────────────────
stage "Caller workflow and agent prompts"
say "These go into a checkout of $REPO. Commit them on a branch and open a PR; do not push to main."
need TARGET_DIR "Path to a local checkout of $REPO:" '^.+$' "a directory"
if [[ ! -e "$TARGET_DIR/.git" ]]; then
  warn "$TARGET_DIR is not a git checkout."
  exit 1
fi
say "Pin the workflow and the tooling to one commit SHA of $WORKFLOW_REPO (a release, or its main tip):"
note "  git ls-remote https://github.com/$WORKFLOW_REPO refs/heads/main"
need COMMIT_SHA "Commit SHA (40 hex characters):" '^[0-9a-f]{40}$' "a full 40-character lowercase hex SHA"

# Plain parameter expansion, not sed: the answers are validated above and need no escaping.
shopt -u patsub_replacement 2>/dev/null || true
_caller="$(<"$EXAMPLES_DIR/caller.yml")"
_slug_block="$INFISICAL_PROJECT_SLUG"
[[ "$INFISICAL_ENV_SLUG" != prod ]] && _slug_block+=$'\n      infisical-env-slug: '"$INFISICAL_ENV_SLUG"
[[ "$INFISICAL_CLAUDE_PATH" != /claude ]] && _slug_block+=$'\n      infisical-claude-path: '"$INFISICAL_CLAUDE_PATH"
[[ "$INFISICAL_GITHUB_PATH" != /github ]] && _slug_block+=$'\n      infisical-github-path: '"$INFISICAL_GITHUB_PATH"
_caller="${_caller//"$PH_SHA"/$COMMIT_SHA}"
_caller="${_caller//"$PH_DOMAIN"/$INFISICAL_DOMAIN}"
_caller="${_caller//"$PH_SLUG"/$_slug_block}"

_dest_caller="$TARGET_DIR/.github/workflows/claude-code-review.yml"
_dest_agents="$TARGET_DIR/.agents/agents"
# _write_file DEST SRC ("-" reads stdin). Refuses to overwrite without a yes.
_write_file() {
  local dest="$1" src="$2"
  if [[ -e "$dest" ]] && ! confirm "$dest exists. Overwrite?"; then
    SKIPPED+=("$dest left as it was")
    return 0
  fi
  mkdir -p "$(dirname "$dest")"
  if [[ "$src" == - ]]; then cat >"$dest"; else cp "$src" "$dest"; fi
  printf '  %s✓ wrote%s %s\n' "$GREEN" "$RESET" "$dest"
}
printf '%s\n' "$_caller" | _write_file "$_dest_caller" -
_write_file "$_dest_agents/code-reviewer.md" "$EXAMPLES_DIR/code-reviewer.md"
_write_file "$_dest_agents/review-responder.md" "$EXAMPLES_DIR/review-responder.md"
if grep -qE '<COMMIT_SHA>|<https://|<your-' "$_dest_caller" 2>/dev/null; then
  warn "placeholders remain in $_dest_caller. Fix them before merging."
fi
step "Edit the two prompts: replace the 'Hard violations' rules with this repository's own."
note "The sections marked 'CI contract' are read by the workflow; keep them."
note "The responder only runs if the caller sets push-runs-on (a runner label different from runs-on)."
pause "Prompts edited, caller reviewed? Press Enter."

# ── 9: switch it on ──────────────────────────────────────────────────────
stage "Enable the loop"
say "The caller only runs when the repository variable $VARIABLE_NAME is 'true'."
say "It is set last, so nothing runs until the secrets and the caller exist."
note "Merge the caller workflow to the default branch through a normal PR for it to take effect."
if confirm "Set $VARIABLE_NAME=true on $REPO now?"; then
  set_var "$VARIABLE_NAME" "true"
else
  SKIPPED+=("Variable $VARIABLE_NAME not set (when ready: gh variable set $VARIABLE_NAME --body true --repo $REPO)")
fi

finish
printf '  %sRollback%s  turn the loop off at any time by unsetting the variable:\n' "$BOLD" "$RESET"
printf '      gh variable delete %s --repo %s\n' "$VARIABLE_NAME" "$REPO"
note "The Apps and secrets can stay; with the variable unset the caller job is skipped."
printf '\n'
