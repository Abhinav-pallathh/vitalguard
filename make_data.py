"""Generate the five synthetic recordings and print what they look like."""
import numpy as np
from vitalguard import synth
from vitalguard.replay import windows
from vitalguard.schema import SAMPLE_RATE_HZ, accel_magnitude, to_arrays, write_csv

print(f"{'scenario':<13} {'rows':>6} {'HR':>6} {'accel σ':>9} {'GSR slope':>11} {'lead-off':>9}")
print("-" * 62)
for s in synth.SCENARIOS:
    samples = synth.generate(s, duration_s=60.0, seed=7)
    write_csv(f"data/synth_{s}.csv", samples)
    c = to_arrays(samples)
    g = c["gsr_raw"].astype(float)
    t = np.arange(g.size) / SAMPLE_RATE_HZ
    slope = np.polyfit(t, g, 1)[0]
    n_win = len(list(windows(samples)))
    print(f"{s:<13} {len(samples):>6} {synth.true_hr(s):>6.0f} "
          f"{accel_magnitude(c).std():>9.3f} {slope:>11.2f} "
          f"{int(c['lead_off'].astype(int).sum()):>9}")
print(f"\n{n_win} windows per 60s recording at 10s/1s hop")
