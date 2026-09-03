"""The bridge -- the one place behaviour and physiology meet on one clock.

The test runs in a browser. The pipeline runs in Python. Until now those were
two islands with two clocks, and `behaviour.py` says plainly why that is fatal:

    "if the test uses wall-clock and the device uses millis(), the two channels
     are unalignable and the whole report is a guess."

This is the fix, and it closes three open problems with one component:

  1. THE CLOCK. The browser POSTs each event the instant it happens. The server
     stamps the device's own `t_ms` on arrival. Over loopback that transfer is
     sub-millisecond, so arrival time is event time to well inside our 1 s
     analysis hop.
  2. THE GATE. The door polls `GET /state` for the wearer's live
     `personal_sigma`, replacing the Space-bar stand-in.
  3. THE REPORT. Both channels land in one process, already aligned.

⚠ THE ONE RULE THAT MAKES THIS HONEST: events must be POSTed ONE AT A TIME, as
they happen. If the browser ever batches them, arrival time stops meaning event
time and every alignment silently gains the batch interval as error. The offset
audit below exists to catch exactly that -- it does not assume the clocks agree,
it MEASURES their disagreement on every single event and reports the spread. A
widening spread is the alarm.

Nothing here computes physiology. It is a socket, a lock, and a list.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from statistics import median

from .behaviour import BehaviourBaseline, BehaviourEvent, Channel, Event
from .behaviour import summarise as summarise_behaviour
from .camera import FaceObservation
from .camera import summarise as summarise_faces
from .model import NO_OPINION
from .model import summarise as summarise_model

DEFAULT_PORT = 8765


@dataclass(frozen=True, slots=True)
class StampedEvent:
    """A browser event, carrying BOTH clocks so the offset stays auditable."""

    browser_t_ms: int
    device_t_ms: int | None      # None before the first device sample arrives
    event: str
    channel: str
    detail: str = ""

    @property
    def offset_ms(self) -> int | None:
        if self.device_t_ms is None:
            return None
        return self.device_t_ms - self.browser_t_ms


@dataclass
class ClockAudit:
    """What we actually know about the two clocks, rather than what we hope."""

    n: int = 0
    offset_median_ms: float | None = None
    offset_spread_ms: float | None = None    # max - min, the honest error bar
    unstamped: int = 0                       # events that beat the first sample

    @property
    def alignable(self) -> bool:
        """True only if we measured a stable offset over a real sample."""
        return (self.n >= 8 and self.offset_spread_ms is not None
                and self.offset_spread_ms <= 250.0)


class SharedState:
    """Everything the browser may ask about, behind one lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.device_t_ms: int | None = None
        self.personal_sigma: float | None = None
        self.gsr_sigma: float | None = None
        self.hr_bpm: float | None = None
        self.trust: str = "unscored"
        self.calibrated: bool = False
        self.events: list[StampedEvent] = []
        self.faces: list[FaceObservation] = []
        # One row per analysis hop: what the rules concluded, and what the
        # model thought, kept side by side so the report can count their
        # disagreements without either one having influenced the other.
        self.physiology: list[dict] = []
        self.camera: object | None = None      # a CameraRunner, when one is attached

    # -- written by the pipeline thread ------------------------------------
    def tick(self, device_t_ms: int) -> None:
        """Advance ONLY the device clock, once per sample.

        This is separate from publish() on purpose. Physiology is computed once
        per analysis hop (1 s), but if the clock only moved at that rate then
        every event arriving inside a hop would be stamped with the same time,
        and the audit would read a ~1 s spread that is our own quantisation
        rather than any real disagreement between the two clocks. Ticking at
        the 100 Hz sample rate makes the stamp resolution 10 ms, so the spread
        the audit reports is the transfer jitter it is supposed to measure.
        """
        with self._lock:
            self.device_t_ms = device_t_ms

    def publish(self, *, device_t_ms: int | None = None, trust: str, calibrated: bool,
                personal_sigma: float | None = None, gsr_sigma: float | None = None,
                hr_bpm: float | None = None, context: str | None = None,
                model_p: float | None = None, agreement: str | None = None) -> None:
        with self._lock:
            if device_t_ms is not None:
                self.device_t_ms = device_t_ms
            self.trust = trust
            self.calibrated = calibrated
            self.personal_sigma = personal_sigma
            self.gsr_sigma = gsr_sigma
            self.hr_bpm = hr_bpm
            if device_t_ms is not None:
                self.physiology.append({
                    "t_ms": device_t_ms, "trust": trust, "context": context,
                    "personal_sigma": personal_sigma, "gsr_sigma": gsr_sigma,
                    "model_p": model_p, "agreement": agreement,
                })

    # -- read by HTTP threads ----------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            return {
                "device_t_ms": self.device_t_ms,
                "personal_sigma": self.personal_sigma,
                "gsr_sigma": self.gsr_sigma,
                "hr_bpm": self.hr_bpm,
                "trust": self.trust,
                "calibrated": self.calibrated,
                "n_events": len(self.events),
                "camera": self._camera_state(),
            }

    def _camera_state(self) -> dict | None:
        """What the camera is actually doing, in the same breath as the vitals.

        Reported on /state so the operator sees a dead camera immediately rather
        than discovering an empty channel in the report afterwards.
        """
        c = self.camera
        if c is None:
            return None
        seen = self.faces[-1] if self.faces else None
        return {
            "frames": c.frames, "faces": c.faces,
            "face_fraction": c.face_fraction,
            "rotation": c.rotation, "reconnects": c.reconnects,
            "error": c.error,
            "face_now": bool(seen.present) if seen is not None else None,
        }

    def record(self, browser_t_ms: int, event: str, channel: str, detail: str = "") -> StampedEvent:
        with self._lock:
            e = StampedEvent(browser_t_ms, self.device_t_ms, event, channel, detail)
            self.events.append(e)
            return e

    def record_face(self, obs: FaceObservation) -> None:
        """Camera observations arrive ALREADY on the device clock -- the runner
        is handed the stamp function and refuses to emit without one. Unlike
        browser events there is no second clock to reconcile here."""
        with self._lock:
            self.faces.append(obs)

    def face_summary(self) -> dict:
        with self._lock:
            snapshot = list(self.faces)
        return summarise_faces(snapshot)

    def blocks(self) -> dict[int, list[BehaviourEvent]]:
        """Split the session into acts, using the act number in each question id.

        The act boundary is the only segmentation the report needs, and the game
        already encodes it in `a<act>q<n>`. Deriving it from timing instead would
        invent a boundary the test did not actually have.
        """
        out: dict[int, list[BehaviourEvent]] = {}
        act = None
        for e in self.behaviour_events():
            if e.event is Event.QUESTION_SHOWN:
                m = re.match(r"a(\d+)q", e.detail or "")
                act = int(m.group(1)) if m else act
            if act is not None:
                out.setdefault(act, []).append(e)
        return out

    def faces_between(self, t0: int, t1: int) -> list[FaceObservation]:
        with self._lock:
            return [f for f in self.faces if t0 <= f.t_ms <= t1]

    def phys_between(self, t0: int, t1: int) -> list[dict]:
        with self._lock:
            return [w for w in self.physiology if t0 <= w["t_ms"] <= t1]

    def audit(self) -> ClockAudit:
        with self._lock:
            offs = [e.offset_ms for e in self.events if e.offset_ms is not None]
            unstamped = sum(1 for e in self.events if e.offset_ms is None)
        if not offs:
            return ClockAudit(n=0, unstamped=unstamped)
        return ClockAudit(n=len(offs), offset_median_ms=median(offs),
                          offset_spread_ms=float(max(offs) - min(offs)),
                          unstamped=unstamped)

    def behaviour_events(self) -> list[BehaviourEvent]:
        """Convert to the type `behaviour.summarise()` consumes, ON THE DEVICE CLOCK.

        Events the vocabulary does not know are dropped, not guessed at, and
        events that arrived before the device did are dropped too -- an event
        with no device time cannot be placed on the timeline, and inventing one
        is exactly the "plausible lie" this project refuses elsewhere.
        """
        out = []
        with self._lock:
            snapshot = list(self.events)
        for e in snapshot:
            if e.device_t_ms is None:
                continue
            try:
                ev, ch = Event(e.event), Channel(e.channel)
            except ValueError:
                continue
            out.append(BehaviourEvent(t_ms=e.device_t_ms, event=ev,
                                      channel=ch, detail=e.detail))
        return out


def _per_question(evs: list[BehaviourEvent]) -> list[list[BehaviourEvent]]:
    """One list per question_shown, so a single act yields several observations."""
    out: list[list[BehaviourEvent]] = []
    for e in evs:
        if e.event is Event.QUESTION_SHOWN or not out:
            out.append([])
        out[-1].append(e)
    return [b for b in out if b]


class _Handler(SimpleHTTPRequestHandler):
    state: SharedState = None      # type: ignore[assignment]
    bridge: "Bridge" = None        # type: ignore[assignment]

    def log_message(self, *a) -> None:      # the pipeline owns the terminal
        pass

    def _json(self, obj, code=200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.startswith("/state"):
            return self._json(self.state.snapshot())
        if self.path.startswith("/report"):
            return self._json(self.bridge.report())
        if self.path.startswith("/audit"):
            return self._json(asdict(self.state.audit()) | {"alignable": self.state.audit().alignable})
        return super().do_GET()

    def do_POST(self) -> None:
        if not self.path.startswith("/event"):
            return self._json({"error": "unknown endpoint"}, 404)
        try:
            n = int(self.headers.get("Content-Length", 0))
            d = json.loads(self.rfile.read(n) or b"{}")
            e = self.state.record(int(d["t_ms"]), str(d["event"]),
                                  str(d.get("channel", "input")), str(d.get("detail", "")))
        except (ValueError, KeyError, TypeError) as exc:
            return self._json({"error": f"{type(exc).__name__}: {exc}"}, 400)
        return self._json({"device_t_ms": e.device_t_ms})


class Bridge:
    """Serves the game and carries its events onto the device clock."""

    def __init__(self, game_dir: str | Path, port: int = DEFAULT_PORT) -> None:
        self.state = SharedState()
        self.port = port
        self._dir = str(Path(game_dir).resolve())
        # `directory` must be bound per-instance: SimpleHTTPRequestHandler's
        # __init__ overwrites any class attribute of that name with cwd.
        handler = partial(type("Handler", (_Handler,),
                               {"state": self.state, "bridge": self}),
                          directory=self._dir)
        self._srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._srv.server_address[1]}/"

    def start(self) -> "Bridge":
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()
        if self._thread:
            self._thread.join(timeout=2)

    def __enter__(self) -> "Bridge":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    def report(self) -> dict:
        """The close: every act compared to the person's OWN practice round.

        Act 1 is untimed and unscored, and it exists for exactly this -- without
        it every metric here would need a population threshold, which is the
        cross-person scoring this project refuses. If act 1 is missing or too
        short the report says so and returns no deviations, rather than falling
        back to fixed numbers that would look identical to real ones.
        """
        blocks = self.state.blocks()
        audit = self.state.audit()
        out: dict = {
            "alignable": audit.alignable,
            "clock_spread_ms": audit.offset_spread_ms,
            "acts": {}, "baselined": False, "why": None,
        }
        if not blocks:
            out["why"] = "no questions were answered"
            return out

        def block_summary(evs):
            t0, t1 = evs[0].t_ms, evs[-1].t_ms
            faces = self.state.faces_between(t0, t1)
            cam = summarise_faces(faces) if faces else None
            return summarise_behaviour(evs, camera=cam)

        def block_model(evs) -> dict | None:
            """The learned channel over the same span, as a COUNT of opinions.

            It is reported next to the rules, never merged with them: a mean
            probability and how often the two disagreed. Nothing here changes a
            verdict -- a reader who ignores this block loses no information the
            deterministic path was using.
            """
            rows = self.state.phys_between(evs[0].t_ms, evs[-1].t_ms)
            if not rows:
                return None
            return summarise_model([r["model_p"] for r in rows],
                                   [r["agreement"] or NO_OPINION for r in rows])

        summaries = {a: block_summary(evs) for a, evs in sorted(blocks.items()) if evs}
        for a, s in summaries.items():
            out["acts"][a] = {"metrics": s.metrics, "n_answers": s.n_answers}
            m = block_model(blocks[a])
            if m is not None:
                out["acts"][a]["model"] = m

        base = BehaviourBaseline()
        practice = summaries.get(1)
        if practice is None:
            out["why"] = "no practice act -- nothing to compare against"
            return out
        # The practice act is one block; the baseline needs several observations
        # of it, so each question inside it becomes its own observation.
        for evs in _per_question(blocks[1]):
            base.update(block_summary(evs))
        if not base.calibrated:
            out["why"] = "practice round too short to be a reference"
            return out

        out["baselined"] = True
        for a, s in summaries.items():
            if a == 1:
                continue
            out["acts"][a]["deviations"] = [
                {"metric": d.metric, "value": d.value, "baseline": d.baseline,
                 "personal_sigma": d.personal_sigma, "channel": d.channel.value,
                 "says": str(d),
                 "spread_floored": base.spread_is_floored(d.metric)}
                for d in base.report(s)
            ]
        return out

    def save(self, path: str | Path) -> ClockAudit:
        """Write the session. The audit goes in the file, not just the console.

        A later reader must be able to see how well the clocks agreed without
        rerunning anything -- otherwise the report's alignment is an assertion.
        """
        a = self.state.audit()
        Path(path).write_text(json.dumps({
            "clock_audit": asdict(a) | {"alignable": a.alignable},
            "camera": self.state._camera_state(),
            "camera_summary": self.state.face_summary(),
            "report": self.report(),
            "events": [asdict(e) | {"offset_ms": e.offset_ms} for e in self.state.events],
            "faces": [asdict(f) for f in self.state.faces],
        }, indent=1))
        return a
