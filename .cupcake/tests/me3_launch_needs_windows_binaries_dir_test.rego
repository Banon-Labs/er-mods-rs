# OPA unit tests for me3_launch_needs_windows_binaries_dir. Run with:
#   opa test .cupcake/system/commands.rego \
#            .cupcake/policies/claude/me3_launch_needs_windows_binaries_dir.rego \
#            .cupcake/tests/me3_launch_needs_windows_binaries_dir_test.rego
package cupcake.policies.claude.me3_launch_needs_windows_binaries_dir_test

import rego.v1

import data.cupcake.policies.claude.me3_launch_needs_windows_binaries_dir as guard

bash_event(cmd) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Bash",
	"tool_input": {"command": cmd, "timeout": 30000, "description": "test case"},
}

denied(cmd) if {
	denials := guard.deny with input as bash_event(cmd)
	"ER-EFFECTS-ME3-WINDOWS-BINARIES-DIR" in {d.rule_id | some d in denials}
}

allowed(cmd) if {
	denials := guard.deny with input as bash_event(cmd)
	count(denials) == 0
}

# The working form from 2026-10-22.
test_allow_the_working_form if {
	allowed("/home/banon/.local/bin/me3 --windows-binaries-dir /home/banon/.local/share/me3/windows-bin launch --auto-detect -p /home/banon/Elden/quicksave.me3")
}

test_allow_bare_name_with_the_option if {
	allowed("me3 --windows-binaries-dir /home/banon/.local/share/me3/windows-bin launch -g eldenring -p x.me3")
}

test_allow_equals_spelling if {
	allowed("me3 --windows-binaries-dir=$HOME/.local/share/me3/windows-bin launch -g eldenring -p x.me3")
}

test_allow_option_beside_other_global_options if {
	allowed(`$HOME/.local/bin/me3 --steam-dir "$HOME/.local/share/Steam" --windows-binaries-dir "$HOME/.local/share/me3/windows-bin" launch -g eldenring -p x.me3`)
}

test_allow_option_after_steam_dir_with_a_space if {
	allowed(`me3 --steam-dir "/home/banon/a b/Steam" --windows-binaries-dir /home/banon/.local/share/me3/windows-bin launch -p x.me3`)
}

test_allow_from_the_repo_root_with_the_option if {
	allowed("cd /home/banon/projects/er-mods-rs && /home/banon/.local/bin/me3 --windows-binaries-dir /home/banon/.local/share/me3/windows-bin launch --auto-detect -p x.me3")
}

test_allow_inside_bash_c_with_the_option if {
	allowed(`bash -c 'me3 --windows-binaries-dir /home/banon/.local/share/me3/windows-bin launch -p x.me3'`)
}

test_allow_backgrounded_with_the_option if {
	allowed("nohup env TMPDIR=/home/banon/.cache/me3/tmp /home/banon/.local/bin/me3 --windows-binaries-dir /home/banon/.local/share/me3/windows-bin launch -p x.me3 > /tmp/me3.log 2>&1 &")
}

# The measured hang: launched from the repo root with no option.
test_deny_the_measured_launch if {
	denied("cd /home/banon/projects/er-mods-rs && /home/banon/.local/bin/me3 launch --auto-detect -p /home/banon/Elden/quicksave.me3")
}

test_deny_bare_me3_launch if {
	denied("me3 launch --auto-detect -p x.me3")
}

test_deny_absolute_path_without_the_option if {
	denied("/home/banon/.local/bin/me3 launch -g eldenring -p x.me3")
}

test_deny_home_variable_path if {
	denied("$HOME/.local/bin/me3 launch -g eldenring -p x.me3")
}

test_deny_braced_home_variable_path if {
	denied("${HOME}/.local/bin/me3 launch -g eldenring -p x.me3")
}

test_deny_tilde_path if {
	denied("~/.local/bin/me3 launch -g eldenring -p x.me3")
}

test_deny_quoted_program if {
	denied(`"$HOME/.local/bin/me3" launch -g eldenring -p x.me3`)
}

test_deny_other_global_options_but_not_this_one if {
	denied("$HOME/.local/bin/me3 --steam-dir $HOME/.local/share/Steam -q launch -g eldenring -p x.me3")
}

test_deny_crash_reporting_value_before_launch if {
	denied("me3 --crash-reporting false launch -p x.me3")
}

# After `launch` me3 rejects it, so it does not count.
test_deny_option_after_launch if {
	denied("me3 launch --windows-binaries-dir /home/banon/.local/share/me3/windows-bin --auto-detect -p x.me3")
}

test_deny_equals_spelling_after_launch if {
	denied("me3 launch --windows-binaries-dir=/home/banon/.local/share/me3/windows-bin -p x.me3")
}

test_deny_empty_value if {
	denied(`me3 --windows-binaries-dir "" launch -p x.me3`)
}

test_deny_empty_equals_value if {
	denied("me3 --windows-binaries-dir= launch -p x.me3")
}

test_deny_inside_bash_c if {
	denied(`bash -c 'me3 launch -p x.me3'`)
}

test_deny_inside_fish_c if {
	denied(`fish -c "/home/banon/.local/bin/me3 launch -p x.me3"`)
}

test_deny_behind_env_and_nohup if {
	denied("nohup env FOO=1 /home/banon/.local/bin/me3 launch -p x.me3 > /tmp/me3.log 2>&1 &")
}

test_deny_behind_timeout if {
	denied("timeout 30 me3 launch -p x.me3")
}

test_deny_on_the_second_line if {
	denied("cd /home/banon/projects/er-mods-rs\nme3 launch -p x.me3")
}

test_deny_second_launch_without_the_option if {
	denied("me3 --windows-binaries-dir /x launch -p a.me3; me3 launch -p b.me3")
}

test_deny_behind_gamescope if {
	denied("gamescope -W 1920 -H 1080 -- /home/banon/.local/bin/me3 launch -p x.me3")
}

test_allow_behind_gamescope_with_the_option if {
	allowed("gamescope -W 1920 -H 1080 -- /home/banon/.local/bin/me3 --windows-binaries-dir /home/banon/.local/share/me3/windows-bin launch -p x.me3")
}

test_deny_inside_command_substitution if {
	denied("pid=$(me3 launch -p x.me3)")
}

# Nothing launches.
test_allow_top_level_help if {
	allowed("me3 --help")
}

test_allow_launch_help if {
	allowed("me3 launch --help")
}

test_allow_launch_short_help if {
	allowed("/home/banon/.local/bin/me3 launch -h")
}

test_allow_version if {
	allowed("me3 -V")
}

test_allow_profile_list if {
	allowed("/home/banon/.local/bin/me3 profile list")
}

test_allow_info if {
	allowed("me3 info")
}

test_allow_help_subcommand if {
	allowed("me3 help launch")
}

test_allow_windows_me3_exe if {
	allowed("/mnt/c/Users/x/AppData/Local/garyttierney/me3/bin/me3.exe launch -g eldenring -p x.me3")
}

# Text that only mentions me3.
test_allow_grep_pattern if {
	allowed(`grep -rn "me3 launch" scripts/`)
}

test_allow_rg_single_quoted_pattern if {
	allowed("rg 'me3 launch -p' scripts")
}

test_allow_rg_after_double_dash if {
	allowed("rg -n -- 'me3 launch' scripts")
}

test_allow_echo if {
	allowed("echo me3 launch -p x.me3")
}

test_allow_git_commit_message if {
	allowed(`git commit -m "fix: me3 launch from the repo root hung in wait_for_partner"`)
}

test_allow_bd_remember_body if {
	allowed(`$HOME/.local/bin/bd remember --key k "/home/banon/.local/bin/me3 launch --auto-detect -p x hangs from the repo root"`)
}

test_allow_heredoc_body if {
	allowed("cat > /tmp/notes.md <<'EOF'\nme3 launch -p x.me3\nEOF")
}

test_allow_heredoc_body_delivered_shape if {
	allowed("cat > /tmp/notes.md <<'EOF' me3 launch -p x.me3 EOF")
}

test_allow_listing_the_windows_bin_dir if {
	allowed("ls ~/.local/share/me3/windows-bin")
}

test_allow_reading_a_launch_script if {
	allowed("sed -n 1,40p /home/banon/Elden/launch.sh")
}

test_allow_other_tool if {
	denials := guard.deny with input as {
		"hook_event_name": "PreToolUse",
		"tool_name": "Write",
		"tool_input": {"file_path": "/tmp/x.sh", "content": "me3 launch -p x.me3"},
	}
	count(denials) == 0
}
