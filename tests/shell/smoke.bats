#!/usr/bin/env bats
# Checks that both Bats helper libraries load and assertions work.

setup() {
  bats_load_library bats-support
  bats_load_library bats-assert
}

@test "bats-assert is loaded and can make an assertion" {
  run echo "hello"
  assert_success
  assert_output "hello"
}
