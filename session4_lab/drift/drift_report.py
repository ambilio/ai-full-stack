# drift_report.py — Session 4, Step 5: detect data drift with Evidently.
#
# Scenario: we log simple features about production traffic (prompt length,
# latency, a satisfaction score). The BASELINE window is what traffic looked
# like when the service was validated. The CURRENT window has shifted —
# users now send much longer prompts and latency has crept up.
# The model code did not change; the WORLD did. That is drift.
#
# Run (inside your venv):
#   pip install "evidently==0.4.40" pandas numpy
#   python drift/drift_report.py
#   -> opens/writes drift/drift_report.html
import numpy as np
import pandas as pd
from evidently.report import Report
from evidently.metric_preset import DataDriftPreset

rng = np.random.default_rng(42)
N = 1000

# Baseline window: the traffic profile the service was tested against
baseline = pd.DataFrame({
    "prompt_length_words":  rng.normal(60, 15, N).clip(5, 200).round(),
    "latency_seconds":      rng.normal(1.5, 0.4, N).clip(0.2, 10).round(2),
    "satisfaction_score":   rng.choice([1, 2, 3, 4, 5], N, p=[.05, .10, .20, .35, .30]),
})

# Current window: users now paste long documents; latency has crept up;
# satisfaction is slipping. No code changed — the input distribution did.
current = pd.DataFrame({
    "prompt_length_words":  rng.normal(95, 25, N).clip(5, 400).round(),
    "latency_seconds":      rng.normal(2.6, 0.8, N).clip(0.2, 15).round(2),
    "satisfaction_score":   rng.choice([1, 2, 3, 4, 5], N, p=[.12, .18, .25, .28, .17]),
})

report = Report(metrics=[DataDriftPreset()])
report.run(reference_data=baseline, current_data=current)
report.save_html("drift/drift_report.html")

print("Wrote drift/drift_report.html — open it in a browser.")
print("Read it as: which columns drifted, by how much, and is the dataset")
print("as a whole flagged? In production this runs on a schedule and a")
print("drift breach raises an alert / opens a retraining ticket (Session 5).")
