import shutil
from peerhub.telemetry.presenter import TelemetryPresenter

presenter = TelemetryPresenter(use_color=False)
snapshot = presenter.collect_live_snapshot()

for (w, h) in [(80, 24), (120, 40), (70, 18), (60, 15)]:
    print(f"\n========================================================")
    print(f" TESTING TERMINAL SIZE: {w} cols x {h} rows")
    print(f"========================================================")
    # mock shutil.get_terminal_size
    shutil.get_terminal_size = lambda fallback=(80, 24): (w, h)
    out = presenter.render(snapshot)
    print(out)
    lines = out.splitlines()
    print(f">> Total Lines Rendered: {len(lines)} (Max allowed height: {h})")
    assert any("SUMMARY" in line for line in lines), "SUMMARY section missing!"
    print(">> SUMMARY IS VISIBLE!")
