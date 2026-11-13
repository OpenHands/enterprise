#!/usr/bin/env bats
# Proves the pinned bats-core, bats-support and bats-assert are fetched, pinned and load correctly.
# A feature's own shell-script coverage replaces this once it exists.

setup() {
  bats_load_library bats-support
  bats_load_library bats-assert
}

@test "bats-assert is loaded and can make an assertion" {
  run echo "hello"
  assert_success
  assert_output "hello"
}
