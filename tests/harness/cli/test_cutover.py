import subprocess


def test_koder_help_runs_via_new_runtime():
    proc = subprocess.run(["uv", "run", "koder", "--help"], capture_output=True, text=True)
    assert proc.returncode == 0
    assert "koder" in proc.stdout.lower()
