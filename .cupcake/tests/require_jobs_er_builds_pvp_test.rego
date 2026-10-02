# OPA unit tests for require_jobs_er_builds_pvp. Run with:
#   opa test .cupcake/policies/claude/require_jobs_er_builds_pvp.rego \
#            .cupcake/tests/require_jobs_er_builds_pvp_test.rego
package cupcake.policies.claude.require_jobs_er_builds_pvp_test

import rego.v1

import data.cupcake.policies.claude.require_jobs_er_builds_pvp as guard

bash_event(cmd) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Bash",
	"tool_input": {"command": cmd, "timeout": 30000, "description": "test case"},
}

denied(cmd) if {
	denials := guard.deny with input as bash_event(cmd)
	"ER-EFFECTS-REQUIRE-PVP-JOBS" in {d.rule_id | some d in denials}
}

allowed(cmd) if {
	denials := guard.deny with input as bash_event(cmd)
	count(denials) == 0
}

# The run measured on 2026-10-01: 17 processes, load 33 on 16 cores.
test_deny_the_measured_run if {
	denied("python3 scripts/er-builds-pvp.py --rl 150 --top 1000 --json")
}

test_deny_absolute_path if {
	denied("python3 /home/banon/projects/er-mods-rs/scripts/er-builds-pvp.py --rl 150 --sort score --json")
}

test_deny_weapon_scoped_without_jobs if {
	denied(`python3 scripts/er-builds-pvp.py --rl 150 --weapon Halberd --one-handed --setup --setup-lefts "Iron Cleaver" --json`)
}

test_deny_inside_bash_c if {
	denied(`bash -c 'python3 scripts/er-builds-pvp.py --rl 150 --json'`)
}

test_deny_chained_after_cd if {
	denied("cd /home/banon/projects/er-mods-rs && python3 scripts/er-builds-pvp.py --rl 150 > out.json")
}

test_deny_scratchpad_copy if {
	denied("python3 /tmp/claude-1000/x/scratchpad/next/head/scripts/er-builds-pvp.py --rl 150 --sort score --json")
}

# The count is spelled out, never an argparse prefix or a zero.
test_deny_jobs_prefix if {
	denied("python3 scripts/er-builds-pvp.py --rl 150 --job 4")
}

test_deny_jobs_zero if {
	denied("python3 scripts/er-builds-pvp.py --rl 150 --jobs 0")
}

test_deny_jobs_without_value if {
	denied("python3 scripts/er-builds-pvp.py --rl 150 --jobs")
}

test_allow_jobs_space if {
	allowed("python3 scripts/er-builds-pvp.py --rl 150 --sort score --json --jobs 4")
}

test_allow_jobs_equals if {
	allowed("python3 scripts/er-builds-pvp.py --jobs=2 --rl 150 --weapon Halberd")
}

test_allow_jobs_then_redirect if {
	allowed("python3 scripts/er-builds-pvp.py --rl 150 --jobs 4 --json > /tmp/claude-1000/x/rank.json")
}

test_allow_selftest if {
	allowed("python3 scripts/er-builds-pvp.py --selftest")
}

test_allow_help if {
	allowed("python3 scripts/er-builds-pvp.py --help")
}

test_allow_other_scripts if {
	allowed("python3 scripts/er-builds-setup-rank.py --setup a.json --loop b.json")
}

test_allow_bd_text_mention if {
	allowed(`$HOME/.local/bin/bd remember --key k "ran er-builds-pvp.py --rl 150 without jobs"`)
}

test_allow_git_commit_message if {
	allowed(`git commit -m "fix: er-builds-pvp.py --rl 150 default"`)
}

test_allow_reading_the_file if {
	allowed("sed -n 1,40p scripts/er-builds-pvp.py")
}

test_allow_python_reading_the_file if {
	allowed(`python3 -c "print(open('scripts/er-builds-pvp.py').read()[:200])"`)
}

# The false positive reported 2026-10-01: a process filter that names the run in a string.
test_allow_process_filter_naming_the_run if {
	allowed(`ps -u x -o args= | python3 -c "import sys; [print(l) for l in sys.stdin if 'python3 scripts/er-builds-pvp.py' in l]"`)
}

test_deny_bash_lc if {
	denied(`bash -lc "python3 scripts/er-builds-pvp.py --rl 150 --json"`)
}

test_deny_uv_run if {
	denied("uv run --with numpy python3 scripts/er-builds-pvp.py --rl 150 --json")
}

test_deny_unbuffered_flag if {
	denied("python3 -u scripts/er-builds-pvp.py --rl 150 --json")
}

test_deny_direct_exec if {
	denied("./scripts/er-builds-pvp.py --rl 150 --json")
}
