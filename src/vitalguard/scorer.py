"""The Arousal-Context Severity Scorer -- differentiator #3.

Every consumer wearable can tell you your heart rate went up. The claim here is
narrower and much more useful: it says WHY, and refuses to alarm when it knows
the answer.

    elevated + motion        -> EXERTION      you are exercising. Not an alarm.
    elevated + arousal       -> AROUSAL       stress or emotion. Notice, not alarm.
    elevated + neither       -> UNEXPLAINED   this is the one that matters.
    not elevated             -> NORMAL

The whole product lives in that third row. An unexplained elevation is what a
fixed-threshold device buries under false alarms from exercise, and what a
device with no context cannot distinguish from a workout.

D8: rules, not a model. A rule-based scorer is explainable on stage, debuggable
at 2am, and needs no training data. If a classifier cannot beat explicit rules
on our own recordings, the classifier is decoration.

Every threshold below was READ OFF measured WESAD data (S2-S4, 3 subjects,
1135 gate-passing windows), never invented:

    feature                 rest      meditation  amusement   stress
    HR deviation (sigma)    +0.07     -0.40       +0.03       +5.77
      p25/p75               -0.59/.79             -1.02/.96   +2.05/9.79
    GSR level (sigma)       +0.05     +0.27       +0.45       +2.60
    motion (g)               0.007     0.004       0.005       0.022

⚠ TWO HONEST LIMITS ON THAT TABLE:

  1. WESAD has NO exercise condition -- motion is 0.004-0.022 g everywhere,
     because it is a seated lab study. The EXERTION branch is therefore
     UNVALIDATED on real data. It works on synthetic data that we wrote. Only
     our own recordings can test it.

  2. WESAD's amusement condition barely raises heart rate (+0.03 sigma, the
     same as rest). So this data does NOT meaningfully test the hard
     discrimination -- arousal-without-stress at an ELEVATED heart rate. The
     scorer will score well on amusement for the wrong reason. Do not quote
     amusement performance as evidence that AROUSAL vs UNEXPLAINED works.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum

from .baseline import Deviation


class Context(Enum):
    NORMAL = "normal"
    EXERTION = "exertion"
    AROUSAL = "arousal"
    UNEXPLAINED = "unexplained"


class Severity(Enum):
    NONE = 0
    NOTICE = 1
    CONCERN = 2
    ALERT = 3


@dataclass(frozen=True, slots=True)
class ScoreProfile:
    """Thresholds, in units of the wearer's OWN variability -- not bpm.

    That is the point. A threshold in bpm is a fixed threshold wearing a
    disguise; +15 bpm is noise for one person and a real event for another.
    """

    elevated_sigma: float = 2.0     # stress p25 = +2.05; every other state p75 < +1.0
    high_sigma: float = 4.0         # sustained above this is not ambiguous
    motion_explains: float = 0.15   # matches the gate's DEGRADED motion threshold
    arousal_sigma: float = 1.5      # stress GSR p25 = +1.83; rest median = +0.05
    sustain_windows: int = 3        # a single window is noise, not an event


DEFAULT = ScoreProfile()


@dataclass(slots=True)
class Score:
    context: Context
    severity: Severity
    hr_sigma: float
    motion: float
    gsr_sigma: float | None
    explanation: str

    def __str__(self) -> str:
        return f"{self.context.value.upper():<12} {self.severity.name:<8} {self.explanation}"


def score(
    deviation: Deviation,
    motion: float,
    gsr_sigma: float | None,
    profile: ScoreProfile = DEFAULT,
) -> Score:
    """Classify one window. Pure, deterministic, no state.

    `deviation` is required and can only be produced by `PersonalBaseline`,
    which itself requires a gate Verdict -- so a score cannot be computed for a
    reading that failed quality. The chain is enforced by the type signatures
    rather than by anyone remembering the order.
    """
    s = deviation.personal_sigma

    if s < profile.elevated_sigma:
        return Score(Context.NORMAL, Severity.NONE, s, motion, gsr_sigma,
                     f"within your normal range ({s:+.1f}sd)")

    # Physical explanation is checked FIRST and wins outright. Exercise raises
    # skin conductance too (sweat), so testing arousal first would label every
    # workout as stress -- the exact false alarm that trains people to ignore
    # the device.
    if motion > profile.motion_explains:
        return Score(Context.EXERTION, Severity.NONE, s, motion, gsr_sigma,
                     f"elevated ({s:+.1f}sd) and you are moving - expected")

    if gsr_sigma is not None and gsr_sigma > profile.arousal_sigma:
        sev = Severity.CONCERN if s >= profile.high_sigma else Severity.NOTICE
        return Score(Context.AROUSAL, sev, s, motion, gsr_sigma,
                     f"elevated ({s:+.1f}sd) with skin conductance up "
                     f"({gsr_sigma:+.1f}sd) - stress or emotion")

    # Nothing explains it. This is the alarm the product exists for.
    sev = Severity.ALERT if s >= profile.high_sigma else Severity.CONCERN
    unknown = " (skin conductance unavailable)" if gsr_sigma is None else ""
    return Score(Context.UNEXPLAINED, sev, s, motion, gsr_sigma,
                 f"elevated ({s:+.1f}sd) at rest, no physical or emotional "
                 f"explanation{unknown}")


class SustainedScorer:
    """Requires an event to persist before it is allowed to escalate.

    One window at +4 sigma is a twitch; twelve seconds of it is an event. Every
    consumer wearable that cries wolf does so because it alarmed on a single
    sample, and a device that has cried wolf once is a device on a bedside
    table with a flat battery.

    Severity is capped at NOTICE until `sustain_windows` consecutive windows
    agree on the context. The context itself is never suppressed -- the user
    always sees what we think is happening, just not the alarm.
    """

    def __init__(self, profile: ScoreProfile = DEFAULT) -> None:
        self._profile = profile
        self._recent: deque[Context] = deque(maxlen=profile.sustain_windows)

    def push(self, s: Score) -> Score:
        self._recent.append(s.context)
        sustained = (len(self._recent) == self._recent.maxlen
                     and len(set(self._recent)) == 1)
        if sustained or s.severity is Severity.NONE:
            return s
        capped = min(s.severity.value, Severity.NOTICE.value)
        return Score(s.context, Severity(capped), s.hr_sigma, s.motion,
                     s.gsr_sigma, s.explanation + " [confirming]")

    def reset(self) -> None:
        self._recent.clear()
