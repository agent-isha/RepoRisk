"""Column groups of the prediction dataset.

Kept in a dependency-free module so the training code can load datasets
without importing the git-mining stack (PyDriller).
"""

ID_COLUMNS = ("snapshot_date", "path")
# Model inputs. Every value is computed from commits dated <= the snapshot.
FEATURE_COLUMNS = (
    "commits",
    "lines_added",
    "lines_deleted",
    "churn",
    "churn_30d",
    "churn_90d",
    "churn_180d",
    "authors",
    "author_hhi",
    "coupled_files",
    "strongest_coupling",
    "age_days",
    "days_since_last_change",
    "past_bug_fixes",
)
# Derived from commits after the snapshot. Never use these as model inputs.
LABEL_COLUMNS = (
    "label",
    "window_complete",
    "label_fix_count",
    "label_fix_commits",
    "deleted_in_window",
)
COLUMNS = ID_COLUMNS + FEATURE_COLUMNS + LABEL_COLUMNS
