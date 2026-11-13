#!/usr/bin/env bats
# scripts/setup.sh against a fake docker CLI: preflight checks, startup, failures and printed output.

bats_require_minimum_version 1.5.0

setup() {
  bats_load_library bats-support
  bats_load_library bats-assert
  load test_helper
  make_sandbox
}

# Makes the fake's `compose config` print an app service whose OH_WEB_URL line is $1.
config_with_url_line() {
  printf 'services:\n  app:\n    environment:\n%s\n    image: example\n' "$1" >"$BATS_TEST_TMPDIR/config.yml"
  mock_env+=(MOCK_DOCKER_CONFIG_STDOUT_FILE="$BATS_TEST_TMPDIR/config.yml")
}

TRICKY_OPTS=(-f "file with space.yml" -p "it's \"quoted\"; \$HOME *" --profile "")

@test "stops before starting anything when the Compose plugin is missing" {
  mock_env+=(MOCK_DOCKER_VERSION_EXIT=1)
  run_setup
  assert_failure 1
  assert_stderr --partial "Docker Compose is not available"
  assert_output ""
  assert_docker_calls "compose version"
}

@test "stops before starting anything when the Docker daemon is unreachable" {
  mock_env+=(MOCK_DOCKER_INFO_EXIT=1)
  run_setup
  assert_failure 1
  assert_stderr --partial "Cannot reach Docker"
  assert_docker_calls "compose version" "info"
}

@test "starts the stack from the repository root and prints where to go" {
  config_with_url_line '      OH_WEB_URL: http://localhost:8123'
  mock_env+=(MOCK_DOCKER_UP_STDOUT="pulling app image" MOCK_DOCKER_UP_STDERR="Container app Healthy")
  run_setup
  assert_success
  assert_docker_calls "compose version" "info" "compose up --wait" "compose config"
  assert_docker_ran_in_repo
  assert_line "pulling app image"
  assert_stderr --partial "Container app Healthy"
  assert_line --partial "OpenHands Enterprise is running at"
  assert_printed_url http://localhost:8123
  assert_line --partial "./scripts/support-bundle.sh"
}

@test "a failed startup keeps Compose's exit code and prints how to investigate" {
  mock_env+=(MOCK_DOCKER_UP_EXIT=42 MOCK_DOCKER_UP_STDERR="dependency failed to start: container app is unhealthy")
  run_setup
  assert_failure 42
  assert_docker_calls "compose version" "info" "compose up --wait"
  assert_stderr --partial "container app is unhealthy"
  assert_stderr --partial "did not start (docker compose exited with code 42)"
  refute_output --partial "is running"
  assert_printed_command_runs "$(text_after "read the logs:" "$stderr")" compose logs
  assert_printed_command_runs "$(text_after "Which container failed:" "$stderr")" compose ps --all
}

@test "passes every option through to both up and config, unchanged" {
  config_with_url_line '      OH_WEB_URL: http://localhost:8123'
  run_setup "${TRICKY_OPTS[@]}"
  assert_success
  assert_docker_args 3 compose "${TRICKY_OPTS[@]}" up --wait
  assert_docker_args 4 compose "${TRICKY_OPTS[@]}" config
}

@test "the commands it prints after a successful start reproduce the same options" {
  config_with_url_line '      OH_WEB_URL: http://localhost:8123'
  run_setup "${TRICKY_OPTS[@]}"
  assert_success
  assert_printed_command_runs "$(text_after "Starting OpenHands Enterprise with:" "$output")" compose "${TRICKY_OPTS[@]}" up --wait
  assert_printed_command_runs "$(text_after "View the logs:" "$output")" compose "${TRICKY_OPTS[@]}" logs -f
  assert_printed_command_runs "$(text_after "One service:" "$output")" compose "${TRICKY_OPTS[@]}" logs -f app
}

@test "the commands it prints after a failed start reproduce the same options" {
  mock_env+=(MOCK_DOCKER_UP_EXIT=1)
  run_setup "${TRICKY_OPTS[@]}"
  assert_failure 1
  assert_printed_command_runs "$(text_after "read the logs:" "$stderr")" compose "${TRICKY_OPTS[@]}" logs
  assert_printed_command_runs "$(text_after "Which container failed:" "$stderr")" compose "${TRICKY_OPTS[@]}" ps --all
}

@test "reads a double-quoted URL from the rendered config" {
  config_with_url_line '      OH_WEB_URL: "https://double.example.test"'
  run_setup
  assert_success
  assert_printed_url https://double.example.test
}

@test "reads a single-quoted URL from the rendered config" {
  config_with_url_line "      OH_WEB_URL: 'https://single.example.test'"
  run_setup
  assert_success
  assert_printed_url https://single.example.test
}

@test "ignores keys that only end in OH_WEB_URL" {
  config_with_url_line "$(printf '      X_OH_WEB_URL: https://wrong.example.test\n      OH_WEB_URL: https://right.example.test')"
  run_setup
  assert_success
  assert_printed_url https://right.example.test
}

@test "a trailing slash on the URL does not double the slash in the printed links" {
  config_with_url_line '      OH_WEB_URL: https://slash.example.test/'
  run_setup
  assert_success
  assert_printed_url https://slash.example.test
}

@test "falls back to localhost:9043 when the config has no OH_WEB_URL" {
  config_with_url_line '      OTHER_SETTING: value'
  run_setup
  assert_success
  assert_printed_url http://localhost:9043
}

@test "falls back to localhost:9043 when OH_WEB_URL is empty" {
  config_with_url_line '      OH_WEB_URL: ""'
  run_setup
  assert_success
  assert_printed_url http://localhost:9043
}

@test "a config failure after a successful start still reports success, with the default URL" {
  mock_env+=(MOCK_DOCKER_CONFIG_EXIT=3 MOCK_DOCKER_CONFIG_STDERR="config exploded")
  run_setup
  assert_success
  assert_docker_calls "compose version" "info" "compose up --wait" "compose config"
  assert_line --partial "OpenHands Enterprise is running at"
  assert_printed_url http://localhost:9043
  refute_stderr --partial "config exploded"
}

@test "a URL printed before config fails is still used" {
  config_with_url_line '      OH_WEB_URL: http://partial.example.test'
  mock_env+=(MOCK_DOCKER_CONFIG_EXIT=3)
  run_setup
  assert_success
  assert_printed_url http://partial.example.test
}
