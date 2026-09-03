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
    # -- the two clocks of a decision -------------------------------------
    "decision_latency_ms":  ("ms", "time from the question appearing to the first option touched"),
    "commit_latency_ms":    ("ms", "time from the last option touched to committing it"),
    # -- changing your mind ------------------------------------------------
    "switches_per_q":       ("count", "options touched after the first, per question"),
    "first_choice_kept":    ("0-1", "fraction of questions committed on the first option touched"),
    # -- stopping ----------------------------------------------------------
    "idle_max_ms":          ("ms", "longest gap between any two actions"),
    "timeout_fraction":     ("0-1", "fraction of questions with no answer when the clock ran out"),
    "focus_losses":         ("count", "times the window lost focus"),
    # -- other channels, same report --------------------------------------
    "fidget":               ("g", "hand movement, accelerometer"),
    "head_motion_px_s":     ("px/s", "median landmark travel per second, scaled by face width"),
    "face_absent_fraction": ("0-1", "fraction of camera frames in which no face was found"),
    "turn_fraction":        ("0-1", "fraction of camera frames with the nose off-centre between the eyes"),
    "head_tilt_range_deg":  ("deg", "largest minus smallest eye-line angle"),
}

CHANNEL_OF: dict[str, Channel] = {
    "fidget": Channel.MOTION,
    "head_motion_px_s": Channel.CAMERA,
    "face_absent_fraction": Channel.CAMERA,
    "turn_fraction": Channel.CAMERA,
    "head_tilt_range_deg": Channel.CAMERA,
}
"""Metrics not from the keyboard. Absent means Channel.INPUT. B4."""

MIN_ANSWERS = 2
"""Below this, per-question rates are noise and report None.

A "fraction of questions" over one question is not a measurement of anything.
Mirrors baseline.py's refusal to report a baseline under MIN_COVERAGE_S: a
number from too little evidence is worse than no number, because it looks
identical to a real one.
"""

# ⚠ WHY THESE METRICS AND NOT TYPING ONES.
# The test used to take typed answers, and this module measured keystroke
# rhythm, backspaces and first-key latency. The game is four options now, so
# nobody types and every one of those read zero forever -- live code measuring
# something that no longer happens, which is worse than no code at all.
#
# The replacement is better, not merely different, because multiple choice
# separates two things typing conflated:
#   decision_latency  -- how long before you touch anything (reading + instinct)
#   commit_latency    -- how long you sit on your choice before confirming it
# The second is the doubt window, and it has no equivalent in a typed answer.
# `first_choice_kept` is the same signal counted rather than timed, and it is
# the one a person understands instantly: "you went back on a third of them."

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


@dataclass(slots=True)
class QuestionRecord:
    """One question, from appearing to committed. The unit everything is built on."""

    qid: str
    shown_ms: int
    first_touch_ms: int | None = None
    first_key: str | None = None
    last_touch_ms: int | None = None
    committed_ms: int | None = None
    committed_key: str | None = None
    timed_out: bool = False
    switches: int = 0

    @property
    def decision_latency_ms(self) -> int | None:
        if self.first_touch_ms is None:
            return None
        return self.first_touch_ms - self.shown_ms

    @property
    def commit_latency_ms(self) -> int | None:
        """The doubt window: chosen, but not yet committed."""
        if self.committed_ms is None or self.last_touch_ms is None or self.timed_out:
            return None
        return max(0, self.committed_ms - self.last_touch_ms)

    @property
    def kept_first(self) -> bool | None:
        if self.committed_key is None or self.first_key is None:
            return None
        return self.committed_key == self.first_key


def parse(events: list[BehaviourEvent]) -> list[QuestionRecord]:
    """Segment a raw event stream into one record per question.

    Events whose detail the game did not write in the agreed shape are skipped
    rather than guessed at -- an unparseable commit means we do not know what
    was chosen, and inventing a key would put a fabricated answer into a
    behaviour report.
    """
    out: list[QuestionRecord] = []
    cur: QuestionRecord | None = None
    for e in sorted(events, key=lambda x: x.t_ms):
        if e.event is Event.QUESTION_SHOWN:
            cur = QuestionRecord(qid=e.detail or f"q{len(out)}", shown_ms=e.t_ms)
            out.append(cur)
        elif cur is None:
            continue                                  # events before any question
        elif e.event is Event.KEYDOWN:
            if cur.first_touch_ms is None:
                cur.first_touch_ms, cur.first_key = e.t_ms, e.detail or None
            cur.last_touch_ms = e.t_ms
        elif e.event is Event.ANSWER_CHANGED:
            cur.switches += 1
        elif e.event is Event.ANSWER_COMMITTED:
            cur.committed_ms = e.t_ms
            parts = (e.detail or "").split(":")
            if len(parts) >= 2:
                cur.timed_out = parts[-1] == "timeout"
                cur.committed_key = None if cur.timed_out else (parts[1] or None)
    return out


def summarise(events: list[BehaviourEvent], fidget: float | None = None,
              camera: dict | None = None) -> BehaviourSummary:
    """Reduce a block of raw events to per-block metrics.

    `fidget` comes from the existing 100 Hz accelerometer pipeline rather than
    from an event, because motion is sampled continuously and events are sparse.
    `camera` is a camera.summarise() dict, merged in so one report spans all
    three channels. Both optional: a session with no device and no phone still
    produces a valid input-only summary rather than a broken one.

    A metric absent from the returned dict means NOT MEASURABLE from this block.
    It is never defaulted to zero -- "did not hesitate" and "we could not tell"
    are different claims, and only one of them is evidence.
    """
    qs = parse(events)
    ev = sorted(events, key=lambda e: e.t_ms)
    m: dict[str, float] = {}

    answered = [q for q in qs if q.committed_ms is not None]
    n = len(answered)

    lat = [q.decision_latency_ms for q in qs if q.decision_latency_ms is not None]
    if lat:
        m["decision_latency_ms"] = float(np.median(lat))

    com = [q.commit_latency_ms for q in answered if q.commit_latency_ms is not None]
    if com:
        m["commit_latency_ms"] = float(np.median(com))

    if n >= MIN_ANSWERS:
        m["switches_per_q"] = float(sum(q.switches for q in answered) / n)
        kept = [q.kept_first for q in answered if q.kept_first is not None]
        if kept:
            m["first_choice_kept"] = float(sum(kept) / len(kept))
        m["timeout_fraction"] = float(sum(q.timed_out for q in answered) / n)

    if len(ev) >= 2:
        m["idle_max_ms"] = float(max(b.t_ms - a.t_ms for a, b in zip(ev, ev[1:])))

    m["focus_losses"] = float(sum(1 for e in ev if e.event is Event.FOCUS_LOST))

    if fidget is not None:
        m["fidget"] = float(fidget)

    # Camera metrics ride the same report and the same baseline. Only the ones
    # this module declares are taken -- a new camera metric must be added to
    # METRICS deliberately, so it cannot arrive unannounced and unphrased.
    if camera:
        for k in ("head_motion_px_s", "face_absent_fraction",
                  "turn_fraction", "head_tilt_range_deg"):
            v = camera.get(k)
            if v is not None:
                m[k] = float(v)

    return BehaviourSummary(metrics=m, n_keystrokes=sum(1 for e in ev
                                                        if e.event is Event.KEYDOWN),
                            n_answers=n)


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

    def spread_is_floored(self, metric: str) -> bool | None:
        """True when the practice round was too uniform to measure spread.

        The floor keeps a steady practice round from turning every later
        observation into a 40-sigma event, but it also means the sigma is no
        longer a calibrated z-score -- it is a lower bound on how unusual
        something was. A report that prints "+24 sd" off a floored spread is
        stating a precision it does not have, so it has to say which it is.
        """
        vals = self._obs.get(metric)
        if not self.calibrated or vals is None or len(vals) < 2:
            return None
        a = np.fromiter(vals, dtype=float)
        centre = float(np.median(a))
        mad = float(np.median(np.abs(a - centre)))
        return mad * MAD_TO_SIGMA < abs(centre) * 0.10

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
