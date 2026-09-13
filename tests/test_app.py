"""UI smoke tests - these actually execute app.py through Streamlit's harness."""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from config import ROOT

TIMEOUT = 180


def _submit(app: AppTest):
    """The review form's submit button (AppTest exposes it as a plain button)."""
    return next(button for button in app.button if button.label == "Submit decisions")


def _app() -> AppTest:
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=TIMEOUT)
    return app.run()


def test_landing_page_renders():
    app = _app()
    assert not app.exception
    assert "SprintPulse" in app.title[0].value
    assert app.sidebar.selectbox(key="sprint").value == 5, "defaults to the newest valid sprint"


def test_run_then_review_then_report():
    app = _app()
    app.sidebar.button(key="run").click().run()
    assert not app.exception

    # Human review blocks the rest of the UI until decisions are submitted.
    assert any("Human review required" in h.value for h in app.subheader)
    radios = app.radio
    assert radios, "each escalation offers a decision"
    for radio in radios:
        radio.set_value("approve")
    _submit(app).click().run()
    assert not app.exception

    labels = [tab.label for tab in app.tabs]
    assert labels == ["Overview", "Blockers", "Risks", "Trends & memory", "Weekly report", "Ask memory"]
    assert any("Weekly Status Report" in md.value for md in app.markdown)


def test_malformed_sprint_shows_an_error_not_a_crash():
    app = _app()
    app.sidebar.selectbox(key="sprint").set_value(6).run()
    app.sidebar.button(key="run").click().run()

    assert not app.exception
    assert app.error, "the UI reports the halt"
    assert "end_date" in app.error[0].value


def test_data_fault_is_surfaced_in_the_ui():
    app = _app()
    app.sidebar.checkbox(key="fault_data").check().run()
    app.sidebar.button(key="run").click().run()

    assert not app.exception
    assert "Run halted" in app.error[0].value


@pytest.mark.parametrize("fault_key", ["fault_llm", "fault_memory"])
def test_non_critical_faults_still_produce_a_report(fault_key):
    app = _app()
    app.sidebar.checkbox(key=fault_key).check().run()
    app.sidebar.button(key="run").click().run()
    for radio in app.radio:
        radio.set_value("approve")
    _submit(app).click().run()

    assert not app.exception
    assert not app.error
    assert app.warning, "degraded mode is announced to the user"
    assert any("Weekly Status Report" in md.value for md in app.markdown)


def test_memory_question_tab_answers_from_memory():
    app = _app()
    app.sidebar.button(key="run").click().run()
    for radio in app.radio:
        radio.set_value("approve")
    _submit(app).click().run()

    app.button(key="example_0").click().run()
    assert app.text_input(key="question").value == "What has been stuck for more than one sprint?"

    app.button(key="ask").click().run()
    assert not app.exception
    answers = [md.value for md in app.markdown]
    assert any("ATL-204" in value for value in answers), "the answer cites the long-running blocker"


def test_seed_button_replays_earlier_sprints():
    app = _app()
    app.sidebar.button(key="seed").click().run()
    assert not app.exception
    assert any("Seeded" in message.value for message in app.success)
