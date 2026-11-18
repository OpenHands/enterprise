#!/usr/bin/env bats
# scripts/setup.sh against the real `docker compose config` and this repository's Compose files.
# The daemon check and `up` stay simulated, so these tests need the docker CLI and Compose but no daemon.

bats_require_minimum_version 1.5.0

setup() {
  bats_load_library bats-support
  bats_load_library bats-assert
  load test_helper
  real_docker=${REAL_DOCKER:-$(command -v docker || true)}
  if [ -z "$real_docker" ] || ! "$real_docker" compose version >/dev/null 2>&1; then
    if [ "${SHELL_TESTS_REQUIRE_COMPOSE:-0}" = 1 ]; then
      fail "docker compose is required (SHELL_TESTS_REQUIRE_COMPOSE=1)"
    fi
    skip "docker compose is not installed"
  fi
  make_sandbox
  cp -p "$REPO_ROOT"/docker-compose*.yml "$repo/"
  mock_env+=(MOCK_DOCKER_REAL="$real_docker")
}

# Writes a minimal Compose project in DIR (relative to the repository) whose app URL follows the
# same rule as docker-compose.yml.
write_alt_project() {
  mkdir -p "$repo/$1"
  # shellcheck disable=SC2016 # Compose interpolates these, not the shell.
  printf 'services:\n  app:\n    image: example\n    environment:\n      OH_WEB_URL: ${WEB_URL:-http://localhost:${APP_PORT:-9043}}\n' \
    >"$repo/$1/docker-compose.yml"
}

# Asserts the script ran the expected docker calls for these options from the repository root, and
# exited 0. `up` is simulated; `config` is the real Compose.
assert_started_and_rendered() {
  local opts=""
  [ "$#" -gt 0 ] && opts=" $*"
  assert_success
  assert_docker_calls "compose version" "info" "compose$opts up --wait" "compose$opts config"
  assert_docker_ran_in_repo
}

@test "with no settings the URL is localhost:9043" {
  run_setup
  assert_started_and_rendered
  assert_printed_url http://localhost:9043
}

@test "APP_PORT in the repository's .env is used even when run from another directory" {
  echo APP_PORT=8181 >"$repo/.env"
  run_setup
  assert_started_and_rendered
  assert_printed_url http://localhost:8181
}

@test "WEB_URL in .env takes precedence over APP_PORT" {
  printf 'APP_PORT=8181\nWEB_URL=https://env.example.test\n' >"$repo/.env"
  run_setup
  assert_started_and_rendered
  assert_printed_url https://env.example.test
}

@test "a shell variable overrides the same setting in .env" {
  echo APP_PORT=8181 >"$repo/.env"
  mock_env+=(APP_PORT=8282)
  run_setup
  assert_started_and_rendered
  assert_printed_url http://localhost:8282
}

@test "--env-file is read relative to the repository root, instead of .env" {
  echo APP_PORT=8181 >"$repo/.env"
  echo APP_PORT=8383 >"$repo/other.env"
  run_setup --env-file other.env
  assert_started_and_rendered --env-file other.env
  assert_printed_url http://localhost:8383
}

@test "a first -f file in another directory makes Compose read that directory's .env" {
  echo APP_PORT=8181 >"$repo/.env"
  write_alt_project alt
  echo APP_PORT=8484 >"$repo/alt/.env"
  run_setup -f alt/docker-compose.yml
  assert_started_and_rendered -f alt/docker-compose.yml
  assert_printed_url http://localhost:8484
}

@test "--project-directory makes Compose read that directory's .env" {
  echo APP_PORT=8181 >"$repo/.env"
  mkdir -p "$repo/alt"
  echo APP_PORT=8484 >"$repo/alt/.env"
  run_setup --project-directory alt -f docker-compose.yml
  assert_started_and_rendered --project-directory alt -f docker-compose.yml
  assert_printed_url http://localhost:8484
}

@test "COMPOSE_FILE in .env selects the overlays the URL is read from" {
  printf 'services:\n  app:\n    environment:\n      OH_WEB_URL: https://overlay.example.test\n' \
    >"$repo/url-override.yml"
  echo COMPOSE_FILE=docker-compose.yml:url-override.yml >"$repo/.env"
  run_setup
  assert_started_and_rendered
  assert_printed_url https://overlay.example.test
}

# Each shipped overlay must still render with the base file, or the script silently falls back to
# localhost:9043. The .env values are placeholders that satisfy the overlays' required variables.
render_with_overlay() {
  printf 'APP_PORT=8686\nWEB_HOST=demo.example.test\nANTHROPIC_API_KEY=placeholder\n' >"$repo/.env"
  run_setup -f docker-compose.yml -f "$1"
  assert_started_and_rendered -f docker-compose.yml -f "$1"
  assert_printed_url http://localhost:8686
}

@test "renders with the HTTPS overlay" {
  render_with_overlay docker-compose.https.yml
}

@test "renders with the own-certificate HTTPS overlay" {
  render_with_overlay docker-compose.https-own-cert.yml
}

@test "renders with the local build overlay" {
  render_with_overlay docker-compose.build.yml
}

@test "renders with the LiteLLM overlay" {
  render_with_overlay docker-compose.litellm.yml
}
