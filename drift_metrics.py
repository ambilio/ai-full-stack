import json, pandas as pd

rows = [json.loads(l) for l in open("logs/audit.jsonl")]
df = pd.DataFrame({
    "prompt_length_words": [len(r["prompt_redacted"].split()) for r in rows],
    "latency_seconds":     [r["latency_ms"] / 1000 for r in rows],
})
baseline, current = df.iloc[:len(df)//2], df.iloc[len(df)//2:]   # two time windows
