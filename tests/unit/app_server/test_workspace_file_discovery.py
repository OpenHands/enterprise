"""Unit tests for the bounded workspace file-discovery contract.

Covers the shared typed contract the SDK will publish
(software-agent-sdk#6079) as produced by Enterprise: option normalization
against server ceilings, shell-injection-safe command building, mandatory
exclusion independence, status discrimination, and truncation detection.
"""

from __future__ import annotations

import pytest

from openhands.app_server.app_conversation.workspace_file_discovery import (
    MANDATORY_EXCLUDED_DIRS,
    WORKSPACE_LIST_MAX_FILES_CEILING,
    FileDiscoveryStatus,
    WorkspaceFileDiscoveryOptions,
    build_workspace_list_command,
    classify_listing_status,
    count_unreadable_subtrees,
    normalize_options,
    parse_workspace_file_list,
    resolved_excluded_dirs,
)


class TestNormalizeOptions:
    def test_none_yields_bounded_defaults(self):
        opts = normalize_options(None)
        assert opts.max_files == WORKSPACE_LIST_MAX_FILES_CEILING
        assert opts.include_symlinks is False
        assert opts.excluded_patterns == []

    def test_unlimited_cap_is_clamped_to_ceiling(self):
        opts = normalize_options({'max_files': 0})
        assert opts.max_files == WORKSPACE_LIST_MAX_FILES_CEILING

    def test_oversized_cap_is_clamped_to_ceiling(self):
        opts = normalize_options({'max_files': 9_999_999})
        assert opts.max_files == WORKSPACE_LIST_MAX_FILES_CEILING

    def test_small_cap_is_honored(self):
        opts = normalize_options({'max_files': 50})
        assert opts.max_files == 50

    def test_negative_cap_falls_back_to_ceiling(self):
        opts = normalize_options({'max_files': -5})
        assert opts.max_files == WORKSPACE_LIST_MAX_FILES_CEILING

    def test_non_integer_cap_falls_back_to_ceiling(self):
        opts = normalize_options({'max_files': 'lots'})
        assert opts.max_files == WORKSPACE_LIST_MAX_FILES_CEILING

    def test_bool_cap_is_rejected(self):
        # bool is an int subclass; it must not be accepted as a cap.
        opts = normalize_options({'max_files': True})
        assert opts.max_files == WORKSPACE_LIST_MAX_FILES_CEILING

    def test_corrupt_shape_falls_back_safely(self):
        opts = normalize_options('not-a-dict')
        assert opts.max_files == WORKSPACE_LIST_MAX_FILES_CEILING
        assert opts.excluded_patterns == []

    def test_patterns_are_deduplicated_and_sanitized(self):
        opts = normalize_options(
            {
                'excluded_patterns': [
                    'coverage',
                    'coverage',
                    "x' || rm -rf / || '",
                    'a;b',
                    'src/generated',
                    '',
                    123,
                ]
            }
        )
        # Safe bare name + path-style pattern kept; injection/dupes/empties dropped.
        assert opts.excluded_patterns == ['coverage', 'src/generated']

    def test_options_object_round_trips_through_normalize(self):
        opts = WorkspaceFileDiscoveryOptions(
            excluded_patterns=['coverage'], max_files=10, include_symlinks=True
        )
        renormalized = normalize_options(opts)
        assert renormalized.excluded_patterns == ['coverage']
        assert renormalized.max_files == 10
        assert renormalized.include_symlinks is True


class TestResolvedExcludedDirs:
    def test_mandatory_dirs_always_present(self):
        opts = normalize_options(None)
        effective = set(resolved_excluded_dirs(opts))
        assert MANDATORY_EXCLUDED_DIRS <= effective

    def test_caller_cannot_remove_mandatory_exclusion(self):
        # Even if a caller somehow passes an empty pattern list, the mandatory
        # set is still applied.
        opts = normalize_options({'excluded_patterns': []})
        effective = set(resolved_excluded_dirs(opts))
        assert '.git' in effective
        assert 'node_modules' in effective

    def test_caller_additions_are_merged(self):
        opts = normalize_options({'excluded_patterns': ['coverage']})
        effective = set(resolved_excluded_dirs(opts))
        assert 'coverage' in effective
        assert MANDATORY_EXCLUDED_DIRS <= effective

    def test_path_style_additions_are_reported(self):
        opts = normalize_options({'excluded_patterns': ['skipme', 'src/lib']})
        effective = resolved_excluded_dirs(opts)
        assert effective[-2:] == ['skipme', 'src/lib']


class TestBuildWorkspaceListCommand:
    def test_command_is_always_bounded_by_head(self):
        opts = normalize_options(None)
        cmd = build_workspace_list_command(opts)
        assert f'head -n {WORKSPACE_LIST_MAX_FILES_CEILING + 1}' in cmd

    def test_command_overfetches_by_one_for_truncation_detection(self):
        opts = normalize_options({'max_files': 5})
        cmd = build_workspace_list_command(opts)
        assert 'head -n 6' in cmd

    def test_mandatory_exclusions_are_pruned(self):
        opts = normalize_options(None)
        cmd = build_workspace_list_command(opts)
        for name in ('.git', 'node_modules', '__pycache__'):
            assert f"-name '{name}'" in cmd

    @pytest.mark.parametrize(
        'pattern',
        [
            "foo' || rm -rf / || '",
            'x$(whoami)',
            'a;rm -rf /',
            'foo`echo`',
            'bar|cat',
            'baz&whoami',
            '*',
            '?',
        ],
    )
    def test_shell_injection_patterns_are_dropped(self, pattern):
        opts = normalize_options({'excluded_patterns': [pattern]})
        cmd = build_workspace_list_command(opts)
        # The dangerous substring never reaches the command line.
        assert pattern not in cmd
        assert 'rm -rf' not in cmd
        assert 'whoami' not in cmd

    def test_path_style_pattern_uses_path_predicate(self):
        opts = normalize_options({'excluded_patterns': ['src/generated']})
        cmd = build_workspace_list_command(opts)
        assert "-path './src/generated'" in cmd

    def test_bare_name_uses_name_predicate(self):
        opts = normalize_options({'excluded_patterns': ['coverage']})
        cmd = build_workspace_list_command(opts)
        assert "-name 'coverage'" in cmd

    def test_symlinks_default_off(self):
        opts = normalize_options(None)
        cmd = build_workspace_list_command(opts)
        assert '-type f' in cmd
        assert '-type l' not in cmd

    def test_symlinks_on_adds_link_test(self):
        opts = normalize_options({'include_symlinks': True})
        cmd = build_workspace_list_command(opts)
        assert '-type l -exec test -f' in cmd
        # find's -exec placeholder is a single pair of braces, not the literal
        # ``{{}}`` (a regression that made the symlink branch always fail).
        assert '-exec test -f {} \\;' in cmd
        assert '{{}}' not in cmd

    def test_symlinks_on_actually_lists_symlink_to_regular_file(self, tmp_path):
        """End-to-end: the generated find lists a symlink that resolves to a
        regular file when ``include_symlinks`` is on, and omits it when off."""
        import subprocess

        (tmp_path / 'a.txt').write_text('hi')
        (tmp_path / 'link_a.txt').symlink_to('a.txt')
        node_modules = tmp_path / 'node_modules'
        node_modules.mkdir()
        (node_modules / 'pkg.js').write_text('x')

        opts_on = normalize_options({'include_symlinks': True})
        out_on = subprocess.run(
            ['bash', '-lc', build_workspace_list_command(opts_on)],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        ).stdout
        files_on, _ = parse_workspace_file_list(out_on, opts_on.max_files)
        assert 'link_a.txt' in files_on
        assert 'a.txt' in files_on
        # mandatory exclusion still applies with symlinks on
        assert 'node_modules/pkg.js' not in files_on

        opts_off = normalize_options({'include_symlinks': False})
        out_off = subprocess.run(
            ['bash', '-lc', build_workspace_list_command(opts_off)],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        ).stdout
        files_off, _ = parse_workspace_file_list(out_off, opts_off.max_files)
        assert 'link_a.txt' not in files_off
        assert 'a.txt' in files_off

    def test_command_starts_with_find_in_cwd(self):
        opts = normalize_options(None)
        cmd = build_workspace_list_command(opts)
        assert cmd.startswith('find . ')

    def test_find_stderr_is_kept_to_detect_unreadable_subtrees(self):
        opts = normalize_options(None)
        cmd = build_workspace_list_command(opts)
        assert '2>/dev/null' not in cmd


class TestCountUnreadableSubtrees:
    def test_counts_find_permission_denied_lines(self):
        stderr = (
            'find: ‘./locked’: Permission denied\n'
            "find: './other/secret': Permission denied\n"
        )
        assert count_unreadable_subtrees(stderr) == 2

    def test_ignores_other_stderr(self):
        stderr = (
            "find: './gone': No such file or directory\n"
            'bash: warning: permission denied elsewhere\n'
        )
        assert count_unreadable_subtrees(stderr) == 0

    def test_empty_stderr(self):
        assert count_unreadable_subtrees('') == 0

    def test_root_denial_is_not_a_nested_subtree(self):
        assert count_unreadable_subtrees("find: '.': Permission denied\n") == 0


class TestClassifyListingStatus:
    """Inputs mirror what ``openhands-agent-server`` actually returns from
    ``execute_bash_command``: ``exit_code``/``stdout``/``stderr`` only."""

    def test_zero_exit_is_ok(self):
        assert classify_listing_status(0, '') is FileDiscoveryStatus.OK

    def test_missing_root_from_runtime_chdir_error(self):
        stderr = (
            'Error executing command: [Errno 2] No such file or directory: '
            "'/workspace/project/nope'"
        )
        assert classify_listing_status(-1, stderr) is FileDiscoveryStatus.MISSING

    def test_forbidden_root_from_runtime_chdir_error(self):
        stderr = (
            'Error executing command: [Errno 13] Permission denied: '
            "'/workspace/project/locked'"
        )
        assert classify_listing_status(-1, stderr) is FileDiscoveryStatus.FORBIDDEN

    def test_other_runtime_exec_error_is_command_error(self):
        stderr = "Error executing command: [Errno 20] Not a directory: '/x/f.txt'"
        assert classify_listing_status(-1, stderr) is FileDiscoveryStatus.COMMAND_ERROR

    @pytest.mark.parametrize('quoted', ["'.'", '\u2018.\u2019'])
    def test_find_reporting_root_unreadable_is_forbidden(self, quoted):
        # Root is traversable but not readable: chdir succeeds, ``find`` cannot
        # list ``.``, and the pipeline still exits with ``head``'s 0.
        stderr = f'find: {quoted}: Permission denied\n'
        assert classify_listing_status(0, stderr) is FileDiscoveryStatus.FORBIDDEN

    def test_nested_denial_with_zero_exit_is_ok(self):
        stderr = "find: './locked': Permission denied\n"
        assert classify_listing_status(0, stderr) is FileDiscoveryStatus.OK

    def test_nested_denial_with_nonzero_exit_is_not_forbidden(self):
        stderr = "find: './locked': Permission denied\n"
        assert classify_listing_status(1, stderr) is FileDiscoveryStatus.COMMAND_ERROR

    def test_runtime_timeout_is_timeout_even_with_nested_denial(self):
        # The agent-server kills the command at its timeout and returns exit -1
        # with whatever stderr it had buffered.
        stderr = "find: './locked': Permission denied\n"
        assert classify_listing_status(-1, stderr) is FileDiscoveryStatus.TIMEOUT

    def test_runtime_timeout_with_empty_stderr(self):
        assert classify_listing_status(-1, '') is FileDiscoveryStatus.TIMEOUT

    def test_other_nonzero_is_command_error(self):
        assert classify_listing_status(2, 'boom') is FileDiscoveryStatus.COMMAND_ERROR

    def test_missing_exit_code_is_invalid_response(self):
        assert classify_listing_status(None, '') is FileDiscoveryStatus.INVALID_RESPONSE


class TestParseWorkspaceFileList:
    def test_strips_leading_dot_slash(self):
        paths, truncated = parse_workspace_file_list('./a\n./b\n', 10)
        assert paths == ['a', 'b']
        assert truncated is False

    def test_deduplicates(self):
        paths, _ = parse_workspace_file_list('./a\n./a\n./b\n', 10)
        assert paths == ['a', 'b']

    def test_truncation_detected_via_overfetch(self):
        paths, truncated = parse_workspace_file_list('./a\n./b\n./c\n', 2)
        assert paths == ['a', 'b']
        assert truncated is True

    def test_exact_size_is_not_truncation(self):
        paths, truncated = parse_workspace_file_list('./a\n./b\n', 2)
        assert paths == ['a', 'b']
        assert truncated is False

    def test_unlimited_cap_never_truncates(self):
        paths, truncated = parse_workspace_file_list('./a\n./b\n./c\n', 0)
        assert paths == ['a', 'b', 'c']
        assert truncated is False

    def test_empty_stdout(self):
        paths, truncated = parse_workspace_file_list('', 10)
        assert paths == []
        assert truncated is False
