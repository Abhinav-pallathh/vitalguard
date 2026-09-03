"""The Behaviour Channel -- what the person DID, never what they felt.

This module exists because of a feature we deliberately did not build. The
obvious version of "stress detection with a camera" is facial emotion
recognition, and it is not defensible: the mapping from facial movement to
emotional state is not consistent across people, contexts or cultures, and
every off-the-shelf model is trained on posed, acted faces. Shipping one
inside a project whose entire thesis is "refuse to show a number you don't
trust" would have been self-refuting.

So the camera -- and the keyboard, and the accelerometer -- are allowed to
report only MEASUREMENTS:

    "broke focus 14 times, up from 2 during practice"     <- a fact
    "appeared anxious (0.81)"                             <- not ours to say

Everything here is a count, a duration or a rate that a human could verify by
watching a recording of the session. If a metric cannot be checked that way it
does not belong in this file.

Load-bearing design decisions:

  B1. Behaviour metrics NEVER feed the physiology scorer. This is the same trap
      as the quality-metrics rule in features.py, one signal further along. The
      test gets harder over time by construction, so typing slows over time by
      construction; a classifier given both would learn "slow typing = stress"
      and score the CLOCK while looking like it scored the body. The two
      channels are reported side by side and integrated by the human reading
      the report. That separation is the interesting result, not a limitation:
      the question worth answering is whether performance held while arousal
      climbed, and that question only exists if the two are measured
      independently.

      Enforcement is by omission -- there is deliberately no `to_features()`
      here and there must never be one. See baseline.py's `deviation()` for the
      same trick used with a required parameter.

  B2. Every metric is reported in units of THIS person's own practice-round
      variability, for exactly the reasons baseline.py gives for heart rate.
      Typing speed varies by an order of magnitude between two people. A fixed
      "over 400ms between keystrokes means hesitation" rule is the same mistake
      as "over 100 bpm means concern".

  B3. The baseline block MUST contain an easy practice round -- untimed,
      unscored. Nobody types while sitting still, so a sit-still-only baseline
      leaves every keystroke metric with nothing to compare against, and the
      module silently falls back to being a fixed-threshold system. If the
      practice round is missing, the keystroke metrics report None rather than
      guessing. Calibrating is an honest answer; a wrong reference is not.

  B4. Provenance is carried on every metric. The camera is not wired up yet;
      when it is, its metrics slot into the same summary with Channel.CAMERA
      and nothing downstream changes. A report must be able to say which
      channels were actually present, because "no gaze data" and "gaze normal"
      are very different sentences.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

# Reuse the physiology model's constants so the two channels speak the same
# language. If MAD scaling is ever revised it must be revised in one place.
from .baseline import HISTORY, MAD_TO_SIGMA


class Channel(str, Enum):
    """Where an observation came from. B4."""

    INPUT = "input"      # keyboard / mouse, emitted by the test itself
    MOTION = "motion"    # MPU6050, already in the 100 Hz recording
    CAMERA = "camera"    # not wired up yet


class Event(str, Enum):
    """The vocabulary the test may emit. Anything not here is not measurable."""

    QUESTION_SHOWN = "question_shown"
    KEYDOWN = "keydown"
    BACKSPACE = "backspace"
    ANSWER_COMMITTED = "answer_committed"
    ANSWER_CHANGED = "answer_changed"
    FOCUS_LOST = "focus_lost"       # window blur: the software gaze proxy
    FOCUS_REGAINED = "focus_regained"


@dataclass(slots=True, frozen=True)
class BehaviourEvent:
    """One timestamped thing that happened, on the device clock.

    `t_ms` is the SAME monotonic millisecond clock as schema.Sample.t_ms. That
    is the only reason behaviour and physiology can be laid on one timeline
    later; if the test uses wall-clock and the device uses millis(), the two
    channels are unalignable and the whole report is a guess.
    """

    t_ms: int
    event: Event
    channel: Channel = Channel.INPUT
    detail: str = ""


# Metric name -> (unit, human-readable phrasing of what was literally measured).
# The phrasing is the honesty check: if you cannot write this sentence without
# naming a feeling, the metric does not belong here.
METRICS: dict[str, tuple[str, str]] = {
    "first_key_latency_ms": ("ms", "time from question appearing to first key"),
    "iki_cv":               ("ratio", "irregularity of typing rhythm"),
    "idle_max_ms":          ("ms", "longest pause while answering"),
    "backspace_rate":       ("per key", "corrections per keystroke"),
    "answer_changes":       ("count", "answers changed after committing"),
    "focus_losses":         ("count", "times the window lost focus"),
    "fidget":               ("g", "hand movement, accelerometer"),
}

CHANNEL_OF: dict[str, Channel] = {
    "fidget": Channel.MOTION,
}
"""Metrics not from the keyboard. Absent means Channel.INPUT. B4."""

MIN_KEYSTROKES = 12
"""Below this, typing-rhythm metrics are noise and report None.

An inter-keystroke CV over four keys is not a measurement of anything. This
mirrors baseline.py's refusal to report a baseline under MIN_COVERAGE_S: a
number computed from too little evidence is worse than no number, because it
looks identical to a real one.
"""

MIN_PRACTICE_ANSWERS = 3
"""B3. Fewer practice questions than this and there is no usable reference."""


@dataclass(slots=True)
class BehaviourSummary:
    """What one block of the session looked like. Raw units, no comparison."""

    metrics: dict[str, float] = field(default_factory=dict)
    n_keystrokes: int = 0
    n_answers: int = 0

    def channels_present(self) -> set[Channel]:
        return {CHANNEL_OF.get(m, Channel.INPUT) for m in self.metrics}


def summarise(events: list[BehaviourEvent], fidget: float | None = None) -> BehaviourSummary:
    """Reduce a block of raw events to per-block metrics.

    `fidget` comes from the existing 100 Hz accelerometer pipeline
    (schema.accel_magnitude) rather than from an event, because motion is
    sampled continuously and events are sparse. It is optional so a session
    with no device attached still produces a valid input-only summary.
    """
    ev = sorted(events, key=lambda e: e.t_ms)
    keys = [e for e in ev if e.event is Event.KEYDOWN]
    shown = [e for e in ev if e.event is Event.QUESTION_SHOWN]
    committed = [e for e in ev if e.event is Event.ANSWER_COMMITTED]

    m: dict[str, float] = {}

    # Hesitation: median over questions, so one long think does not dominate.
    latencies = []
    for q in shown:
        nxt = next((k.t_ms for k in keys if k.t_ms >= q.t_ms), None)
        if nxt is not None:
            latencies.append(nxt - q.t_ms)
    if latencies:
        m["first_key_latency_ms"] = float(np.median(latencies))

    if len(keys) >= MIN_KEYSTROKES:
        gaps = np.diff([k.t_ms for k in keys]).astype(float)
        # Gaps spanning a question boundary are think-time, not typing rhythm.
        within = np.array([
            g for g, a in zip(gaps, [k.t_ms for k in keys[:-1]])
            if not any(a < s.t_ms <= a + g for s in shown)
        ], dtype=float)
        if within.size >= 2 and within.mean() > 0:
            m["iki_cv"] = float(within.std() / within.mean())
            m["idle_max_ms"] = float(within.max())
        m["backspace_rate"] = sum(
            1 for e in ev if e.event is Event.BACKSPACE) / len(keys)

    m["answer_changes"] = float(sum(1 for e in ev if e.event is Event.ANSWER_CHANGED))
    m["focus_losses"] = float(sum(1 for e in ev if e.event is Event.FOCUS_LOST))

    if fidget is not None:
        m["fidget"] = float(fidget)

    return BehaviourSummary(metrics=m, n_keystrokes=len(keys), n_answers=len(committed))


@dataclass(slots=True)
class BehaviourDeviation:
    """One metric, compared to the person's own practice round."""

    metric: str
    value: float
    baseline: float
    personal_sigma: float
    channel: Channel

    def __str__(self) -> str:
        unit, what = METRICS[self.metric]
        return (f"{what}: {self.value:.0f} {unit} "
                f"(practice {self.baseline:.0f}, {self.personal_sigma:+.1f}sd)")


class BehaviourBaseline:
    """Learns one person's normal input behaviour from the practice round.

    Deliberately the same shape as PersonalBaseline: feed it the low-pressure
    block, ask for deviations during the high-pressure block, and get None
    until there is enough evidence. The symmetry is the point -- one discipline
    applied to both channels.
    """

    def __init__(self, min_answers: int = MIN_PRACTICE_ANSWERS) -> None:
        self._obs: dict[str, deque[float]] = {}
        self._answers = 0
        self._min_answers = min_answers

    def update(self, summary: BehaviourSummary) -> None:
        """Offer one practice-round summary. Call once per practice question."""
        for name, val in summary.metrics.items():
            self._obs.setdefault(name, deque(maxlen=HISTORY)).append(val)
        self._answers += summary.n_answers

    @property
    def calibrated(self) -> bool:
        return self._answers >= self._min_answers

    def reference(self, metric: str) -> tuple[float, float] | None:
        """(centre, spread) for one metric, or None if not yet learnable."""
        vals = self._obs.get(metric)
        if not self.calibrated or vals is None or len(vals) < 2:
            return None
        a = np.fromiter(vals, dtype=float)
        centre = float(np.median(a))
        mad = float(np.median(np.abs(a - centre)))
        # Floor scales with the metric's own centre rather than a hardcoded
        # constant, because these metrics span milliseconds to unit ratios and
        # one absolute floor cannot serve both. A perfectly steady practice
        # round must not make every later observation a 40-sigma event -- the
        # same failure MIN_SPREAD_BPM guards against for heart rate.
        spread = max(mad * MAD_TO_SIGMA, abs(centre) * 0.10, 1e-6)
        return centre, spread

    def deviation(self, summary: BehaviourSummary, metric: str) -> BehaviourDeviation | None:
        """Compare one metric under load to the practice round. None if unsafe.

        Returns None -- rather than a zero, or a population guess -- when the
        practice round was missing or too short (B3). The report must be able
        to print "not measured" instead of a confident wrong number.
        """
        if metric not in summary.metrics:
            return None
        ref = self.reference(metric)
        if ref is None:
            return None
        centre, spread = ref
        value = summary.metrics[metric]
        return BehaviourDeviation(
            metric=metric,
            value=value,
            baseline=centre,
            personal_sigma=(value - centre) / spread,
            channel=CHANNEL_OF.get(metric, Channel.INPUT),
        )

    def report(self, summary: BehaviourSummary) -> list[BehaviourDeviation]:
        """Every metric that can be honestly compared, largest change first."""
        out = [d for m in summary.metrics if (d := self.deviation(summary, m))]
        return sorted(out, key=lambda d: abs(d.personal_sigma), reverse=True)
