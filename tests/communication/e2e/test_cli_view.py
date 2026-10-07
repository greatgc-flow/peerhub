"""Rich monitor view: literal-output checks on a real read-only snapshot."""
from peerhub.cli.view import bar, human_duration, render_view, use_color


def _report(items, tmp_path):
    from peerhub.cli.app import main
    db = str(tmp_path / "v.db")
    assert main(["--db", db, "peer", "register", "--id", "a"]) == 0
    from peerhub.extensions.diag import ReadonlyDiag
    return ReadonlyDiag(db).render(["peers", "streams", "resource_pools", "observations"])


def test_empty_report_is_all_clear_and_ascii_safe(tmp_path):
    out = render_view(_report([], tmp_path), width=64, unicode=False, tz=None)
    assert "QUOTA" in out and "all clear" in out
    assert all(ord(c) < 128 for c in out) and max(map(len, out.split("\n"))) <= 80


def test_same_report_renders_identically(tmp_path):
    r = _report([], tmp_path)
    assert render_view(r, width=80) == render_view(r, width=80)


def test_helpers():
    assert human_duration(3700) == "1h01m"
    assert len(bar(0.5, 10, True)) == 10
    assert use_color(True, {"NO_COLOR": "1", "FORCE_COLOR": "1"}) is False
    assert use_color(False, {"FORCE_COLOR": "1"}) is True
    assert use_color(False, {}) is False
