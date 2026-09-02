"""What the gate concludes about every scenario. The demo, in a table."""
from collections import Counter
from vitalguard import synth
from vitalguard.gate import assess
from vitalguard.replay import windows

print(f"{'scenario':<13} {'windows':>8} {'trusted':>9} {'degraded':>9} {'unscored':>9}   top reason")
print("-" * 88)
for sc in synth.SCENARIOS:
    verdicts = [assess(w) for w in windows(synth.generate(sc, duration_s=60.0, seed=7))]
    c = Counter(v.ppg.value for v in verdicts)
    reasons = Counter(r for v in verdicts for r in v.reasons)
    top = reasons.most_common(1)[0][0] if reasons else "-"
    print(f"{sc:<13} {len(verdicts):>8} {c['trusted']:>9} {c['degraded']:>9} "
          f"{c['unscored']:>9}   {top}")
