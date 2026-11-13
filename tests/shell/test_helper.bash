# Shared helpers for the scripts/setup.sh tests.
#
# Each test gets a throwaway repository under BATS_TEST_TMPDIR holding a copy of the script, because
# the script changes into its own repository root and Compose would otherwise read the developer's
# .env from the real checkout. SETUP_SH_UNDER_TEST points at a different copy for mutation checks.

REPO_ROOT=$(cd "$BATS_TEST_DIRNAME/../.." && pwd)
SETUP_SH_UNDER_TEST=${SETUP_SH_UNDER_TEST:-$REPO_ROOT/scripts/setup.sh}

make_sandbox() {
  repo="$BATS_TEST_TMPDIR/repo with space"
  elsewhere="$BATS_TEST_TMPDIR/elsewhere"
  docker_config="$BATS_TEST_TMPDIR/home/.docker"
  mkdir -p "$repo/scripts" "$elsewhere" "$docker_config" "$BATS_TEST_TMPDIR/docker_calls"
  cp -p "$SETUP_SH_UNDER_TEST" "$repo/scripts/setup.sh"
  if [ -n "${COMPOSE_PLUGIN:-}" ]; then
    mkdir -p "$docker_config/cli-plugins"
    ln -s "$COMPOSE_PLUGIN" "$docker_config/cli-plugins/docker-compose"
  fi
  make_fake_docker
  export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
  mock_env=()
  cd "$elsewhere" || return 1
}

# The fake docker logs every call before answering it, so a call whose failure the script swallows
# still shows up. docker_calls.log has one line per call for reading; docker_calls/NNN.args keeps the
# exact arguments. Calls the script is not expected to make exit 97.
# MOCK_DOCKER_EXIT sets the exit code of every expected call; MOCK_DOCKER_<VERSION|INFO|UP|CONFIG>_EXIT
# override it per call. With MOCK_DOCKER_REAL set, `compose version` and `compose ... config` run the
# real CLI; the daemon check and `up` are always simulated, so no container is ever started.
make_fake_docker() {
  mkdir -p "$BATS_TEST_TMPDIR/bin"
  cat >"$BATS_TEST_TMPDIR/bin/docker" <<'FAKE'
#!/usr/bin/env bash
set -u
calls=$BATS_TEST_TMPDIR/docker_calls
n=$(($(wc -l <"$BATS_TEST_TMPDIR/docker_calls.log" 2>/dev/null || echo 0) + 1))
printf '%s\n' "$*" >>"$BATS_TEST_TMPDIR/docker_calls.log"
printf '%s\0' "$@" >"$(printf '%s/%03d.args' "$calls" "$n")"
pwd -P >>"$BATS_TEST_TMPDIR/docker_cwd.log"

exit_code() { local v="MOCK_DOCKER_${1}_EXIT"; echo "${!v:-${MOCK_DOCKER_EXIT:-0}}"; }

if [ "$#" -eq 2 ] && [ "$1" = compose ] && [ "$2" = version ]; then
  if [ -n "${MOCK_DOCKER_REAL:-}" ]; then exec "$MOCK_DOCKER_REAL" "$@"; fi
  exit "$(exit_code VERSION)"
elif [ "$#" -eq 1 ] && [ "$1" = info ]; then
  exit "$(exit_code INFO)"
elif [ "$#" -ge 3 ] && [ "$1" = compose ] && [ "${*: -2:1}" = up ] && [ "${*: -1}" = --wait ]; then
  if [ -n "${MOCK_DOCKER_UP_STDOUT:-}" ]; then printf '%s\n' "$MOCK_DOCKER_UP_STDOUT"; fi
  if [ -n "${MOCK_DOCKER_UP_STDERR:-}" ]; then printf '%s\n' "$MOCK_DOCKER_UP_STDERR" >&2; fi
  exit "$(exit_code UP)"
elif [ "$#" -ge 2 ] && [ "$1" = compose ] && [ "${*: -1}" = config ]; then
  if [ -n "${MOCK_DOCKER_REAL:-}" ]; then exec "$MOCK_DOCKER_REAL" "$@"; fi
  if [ -n "${MOCK_DOCKER_CONFIG_STDOUT_FILE:-}" ]; then cat "$MOCK_DOCKER_CONFIG_STDOUT_FILE"; fi
  if [ -n "${MOCK_DOCKER_CONFIG_STDERR:-}" ]; then printf '%s\n' "$MOCK_DOCKER_CONFIG_STDERR" >&2; fi
  exit "$(exit_code CONFIG)"
fi
echo "fake docker: unexpected call: docker $*" >&2
exit 97
FAKE
  chmod +x "$BATS_TEST_TMPDIR/bin/docker"
}

# Runs the copied script from an unrelated directory with a minimal environment, so a developer's
# APP_PORT, COMPOSE_FILE or DOCKER_HOST cannot leak in. Settings for the fake (or Compose) go in mock_env.
run_setup() {
  run --separate-stderr env -i \
    HOME="$BATS_TEST_TMPDIR/home" \
    PATH="$PATH" \
    BATS_TEST_TMPDIR="$BATS_TEST_TMPDIR" \
    DOCKER_CONFIG="$docker_config" \
    DOCKER_HOST=unix:///nonexistent/docker.sock \
    "${mock_env[@]}" \
    "$repo/scripts/setup.sh" "$@"
}

# Asserts the fake saw exactly these calls, in order, one argument string per call.
assert_docker_calls() {
  local expected=""
  [ "$#" -gt 0 ] && expected=$(printf '%s\n' "$@")
  assert_equal "$(cat "$BATS_TEST_TMPDIR/docker_calls.log" 2>/dev/null)" "$expected"
}

# Asserts the NUL-separated arguments in FILE are exactly the remaining arguments.
assert_args_file() {
  local file=$1 actual=() arg
  shift
  while IFS= read -r -d '' arg; do actual+=("$arg"); done <"$file"
  assert_equal "$(printf '<%s> ' "${actual[@]}")" "$(printf '<%s> ' "$@")"
}

# Asserts docker call number N got exactly the remaining arguments, word boundaries included.
assert_docker_args() {
  local n=$1
  shift
  assert_args_file "$(printf '%s/docker_calls/%03d.args' "$BATS_TEST_TMPDIR" "$n")" "$@"
}

assert_docker_ran_in_repo() {
  local expected cwd
  expected=$(cd "$repo" && pwd -P)
  while IFS= read -r cwd; do
    assert_equal "$cwd" "$expected"
  done <"$BATS_TEST_TMPDIR/docker_cwd.log"
}

# Prints what follows LABEL on the first line of TEXT that contains it, minus leading spaces.
text_after() {
  local label=$1 text=$2 line rest
  while IFS= read -r line; do
    case $line in
      *"$label"*)
        rest=${line#*"$label"}
        printf '%s' "${rest#"${rest%%[! ]*}"}"
        return 0
        ;;
    esac
  done <<<"$text"
  fail "no line containing '$label' in: $text"
}

# Runs a command line the script printed for the user, with docker replaced by an argument
# recorder, and asserts docker would get exactly the remaining arguments.
assert_printed_command_runs() {
  local command=$1 recorded="$BATS_TEST_TMPDIR/replayed"
  shift
  rm -f "$recorded"
  (cd "$BATS_TEST_TMPDIR" && bash -c 'docker() { printf "%s\0" "$@" >"$0"; }; '"$command" "$recorded") || true
  [ -e "$recorded" ] || fail "running '$command' never called docker"
  assert_args_file "$recorded" "$@"
}

# Asserts the success message points at BASE_URL.
assert_printed_url() {
  local base_url=$1
  assert_equal "$(text_after "running at" "$output")" "$base_url"
}
