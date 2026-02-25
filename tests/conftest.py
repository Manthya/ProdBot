"""Pytest collection controls for script-style integration runners.

These files are executable scripts intended to be run directly, not collected
as fixture-driven pytest tests.
"""

collect_ignore = [
    "test_all_pipelines.py",
    "test_media_pipeline.py",
    "test_model_integration.py",
    "test_web_search.py",
]
