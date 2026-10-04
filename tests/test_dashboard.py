from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_dashboard_renders_task_inputs_and_history():
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    dashboard = AppTest.from_file(str(app_path)).run()

    assert not dashboard.exception
    assert any(title.value == "Autonomous Invoice Worker" for title in dashboard.title)
    assert any(widget.label == "Instruction" for widget in dashboard.text_area)
    assert any(widget.label == "Invoice folder" for widget in dashboard.text_input)
    assert any(widget.label == "Recent runs" for widget in dashboard.selectbox)