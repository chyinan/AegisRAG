"""Session defaults protect optional evaluators between per-test fixtures."""

import os


def test_ragas_tracking_is_disabled_outside_metric_fixture_scope() -> None:
    assert os.environ["RAGAS_DO_NOT_TRACK"] == "true"
