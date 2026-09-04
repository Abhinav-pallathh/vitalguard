"""The live monitor -- device to screen, in real time. This is the demo.

    PYTHONPATH=src ./venv/bin/python live.py --serial /dev/ttyUSB0 --save rec.csv
    PYTHONPATH=src ./venv/bin/python live.py --file data/rec.csv        # replay
    PYTHONPATH=src ./venv/bin/python live.py --synth unexplained        # no hardware

`demo.py` proves the pipeline over a finished recording. This runs the SAME
pipeline sample-by-sample as the rows arrive, which is the only mode that can
show the product's actual claim: pull the sensor off and the number does not
freeze, does not drift, and does not degrade gracefully into a plausible lie.
It disappears, and the device says why.

Nothing here re-implements any logic. It is a source, a ring buffer, and a
renderer around `gate` / `hr` / `baseline` / `scorer`. If a number appears on
this screen it came through the same code the test suite covers.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import deque
from pathlib import Path

from vitalguard import features, hr, synth
from vitalguard.baseline import MIN_COVERAGE_S, PersonalBaseline
from vitalguard.gate import Trust, assess
from vitalguard.model import ArousalModel
from vitalguard.replay import DEFAULT_HOP_S, DEFAULT_WINDOW_S, windows
from vitalguard.schema import FIELDS, SAMPLE_RATE_HZ, Sample, read_csv
from vitalguard.scorer import Severity, SustainedScorer, score

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
GREEN, YELLOW, RED, CYAN = "\033[32m", "\033[33m", "\033[31m", "\033[36m"

BADGE = {
    Trust.TRUSTED:  f"{GREEN}  TRUSTED {RESET}",
    Trust.DEGRADED: f"{YELLOW} DEGRADED {RESET}",
    Trust.UNSCORED: f"{RED}▌UNSCORED {RESET}",
}


# --- sources ---------------------------------------------------------------
# Each yields Sample objects. The pipeline cannot tell them apart, which is the
# point: what we rehearse on synthetic data is exactly what runs on hardware.

class SerialSource:
    """Rows off the ESP32, and verdicts back to it on the same line.

    Malformed rows are COUNTED and skipped -- never repaired, never
    interpolated. A partial row at 100 Hz is a row the device could not vouch
    for, and silently patching one is the same failure as holding a stale
    reading over: it produces a file that looks complete.

    `send_verdict` is the return path. The device displays a conclusion it did
    not compute, because reimplementing the gate in firmware would create a
    second source of truth for the one decision the product rests on -- and
    the firmware copy would have no test suite to keep it honest.
    """

    def __init__(self, port: str, baud: int = 230400) -> None:
        import serial                          # pyserial, imported lazily
        # pyserial asserts DTR+RTS high by default on a plain open(), which
        # the ESP32 auto-reset circuit reads as EN=LOW + GPIO0=LOW -- that is
        # the bootloader-entry pulse, not a normal boot. A "plain" open was
        # silently priming download mode instead of just connecting, so the
        # device never got past the ROM banner. Constructing without
        # auto-opening and forcing both lines low first lets it boot normally.
        self._sp = serial.Serial()
        self._sp.port = port
        self._sp.baudrate = baud
        self._sp.timeout = 2
        self._sp.dtr = False
        self._sp.rts = False
        self._sp.open()
        self.bad = 0

    def __iter__(self):
        header = None
        while True:
            raw = self._sp.readline().decode("ascii", "replace").strip()
            if not raw:
                continue
            parts = raw.split(",")
            if header is None:
                if parts == list(FIELDS):
                    header = parts
                    continue
                # Boot chatter before the header ("i2c: 0x57 0x68 ...") is
                # useful, so it is shown rather than swallowed.
                print(f"{DIM}device: {raw}{RESET}", file=sys.stderr)
                continue
            try:
                yield _parse(parts)
            except (ValueError, IndexError):
                self.bad += 1
                if self.bad in (1, 10, 100, 1000):
                    print(f"{DIM}[{self.bad} malformed rows skipped]{RESET}",
                          file=sys.stderr)

    def send_verdict(self, trust: str, bpm: str, ctx: str, why: str) -> None:
        # Commas are the field separator, so a reason containing one would
        # shift every field after it on the device -- the same silent
        # column-shift schema.read_csv refuses to allow.
        why = why.replace(",", ";")[:40]
        try:
            self._sp.write(f"V,{trust},{bpm},{ctx},{why}\n".encode("ascii", "replace"))
        except Exception:
            pass        # the display is never allowed to interrupt the capture

    def close(self) -> None:
        self._sp.close()


def _parse(parts: list[str]) -> Sample:
    return Sample(
        t_ms=int(parts[0]), ppg_ir=int(parts[1]), ppg_red=int(parts[2]),
        ax=float(parts[3]), ay=float(parts[4]), az=float(parts[5]),
        gx=float(parts[6]), gy=float(parts[7]), gz=float(parts[8]),
        gsr_raw=int(parts[9]), ecg_raw=int(parts[10]),
        lead_off=int(parts[11]), btn=int(parts[12]), label=parts[13],
    )


def from_file(path: str, realtime: bool):
    """Replay a recording. `--realtime` paces it at the true 100 Hz so a
    rehearsal has the same rhythm as the live run."""
    period = 1.0 / SAMPLE_RATE_HZ
    for s in read_csv(path):
        yield s
        if realtime:
            time.sleep(period)


def from_synth(scenario: str, seconds: float, realtime: bool):
    period = 1.0 / SAMPLE_RATE_HZ
    for s in synth.generate(scenario, duration_s=seconds, seed=11):
        yield s
        if realtime:
            time.sleep(period)


# --- the honesty ledger ----------------------------------------------------

class Coverage:
    """How often we were willing to show a number at all.

    This is the metric no consumer wearable reports, because every one of them
    answers 100% by construction -- they always show something. Reporting it
    turns the product's refusal from an excuse into a measurement:

        "78% coverage at 3.2 bpm MAE" is a claim with a denominator.
        "always-on" at unstated accuracy is not.

    It also makes the refusal falsifiable. A gate tuned until it refuses
    everything would score perfectly on accuracy and is exposed here instantly.
    """

    def __init__(self) -> None:
        self.n = 0
        self.by_trust = {t: 0 for t in Trust}
        self.reasons: dict[str, int] = {}

    def add(self, verdict) -> None:
        self.n += 1
        self.by_trust[verdict.ppg] += 1
        if verdict.ppg is Trust.UNSCORED and verdict.reasons:
            self.reasons[verdict.reasons[0]] = self.reasons.get(verdict.reasons[0], 0) + 1

    @property
    def pct(self) -> float:
        return 100.0 * (self.n - self.by_trust[Trust.UNSCORED]) / self.n if self.n else 0.0

    def summary(self) -> str:
        if not self.n:
            return "no windows"
        t, d, u = (self.by_trust[x] for x in (Trust.TRUSTED, Trust.DEGRADED, Trust.UNSCORED))
        out = [f"coverage {self.pct:.1f}%  ({t} trusted, {d} degraded, {u} refused, n={self.n})"]
        for why, k in sorted(self.reasons.items(), key=lambda kv: -kv[1]):
            out.append(f"    refused {k:>5}x  {why}")
        return "\n".join(out)


# --- calibration -----------------------------------------------------------
#
# Found by running this file: with no prior calibration the baseline learns
# from whatever is streaming, so an UNEXPLAINED episode teaches the model that
# 111 bpm is this person's resting rate and then reports it as NORMAL. The
# model is behaving exactly as specified -- the bug is that a live monitor
# started mid-episode has no resting evidence to learn from.
#
# That is not a demo artifact. A real wearer straps the device on at some
# arbitrary moment, and "calibrate from whatever you see first" would make the
# device most wrong precisely when the wearer is unwell. Calibration is a
# separate, deliberate, RESTING recording, and it persists across restarts.


def calibrate_from(pb: PersonalBaseline, samples, hop_s: float, window_s: float) -> None:
    for w in windows(samples, window_s=window_s, hop_s=hop_s):
        v = assess(w)
        pb.update(w, v, hr.estimate(w.cols["ppg_ir"]))


def save_baseline(pb: PersonalBaseline, path: str) -> None:
    b = pb.snapshot()
    if not b.calibrated:
        print(f"{YELLOW}not calibrated -- nothing saved{RESET}", file=sys.stderr)
        return
    Path(path).write_text(json.dumps({
        "resting_hr": b.resting_hr, "spread": b.spread,
        "resting_gsr": b.resting_gsr, "gsr_spread": b.gsr_spread,
        "coverage_s": b.coverage_s, "n": b.n_contributing,
    }, indent=2))
    print(f"{DIM}baseline saved -> {path}{RESET}")


class LoadedBaseline(PersonalBaseline):
    """A baseline restored from an earlier resting session.

    Deliberately a subclass rather than a dict of numbers: `deviation()` still
    demands a gate Verdict, so a restored baseline cannot become a back door
    around the quality gate the way a plain saved threshold would.
    """

    def __init__(self, path: str) -> None:
        super().__init__()
        d = json.loads(Path(path).read_text())
        self._loaded = d
        print(f"{DIM}baseline loaded: {d['resting_hr']:.1f} bpm "
              f"+/- {d['spread']:.1f} ({d['coverage_s']:.0f}s){RESET}")

    def snapshot(self):
        from vitalguard.baseline import Baseline
        live = super().snapshot()
        if live.calibrated:          # enough fresh resting evidence: prefer it
            return live
        d = self._loaded
        return Baseline(d["resting_hr"], d["spread"], d["coverage_s"], d["n"],
                        resting_gsr=d["resting_gsr"], gsr_spread=d["gsr_spread"])


# --- the run ---------------------------------------------------------------

def run(source, save, hop_s, window_s, pb=None, bridge=None, model=None) -> None:
    ring: deque[Sample] = deque(maxlen=int(window_s * SAMPLE_RATE_HZ))
    hop_n = int(hop_s * SAMPLE_RATE_HZ)
    # The learned channel is loaded here and read NOWHERE in the scoring path.
    # If it is missing, every line below prints the same verdict it always did
    # -- the model adds a column to the report, never a decision.
    model = model if model is not None else ArousalModel.load_or_none()

    pb = pb or PersonalBaseline()
    ss, cov = SustainedScorer(hop_s=hop_s), Coverage()
    fh = None
    if save:
        fh = open(save, "w", buffering=1)
        fh.write(",".join(FIELDS) + "\n")

    print(f"{BOLD}VitalGuard live{RESET}  "
          f"{DIM}window {window_s:.0f}s / hop {hop_s:.0f}s{RESET}")
    print(f"  {DIM}model: "
          f"{model.card.headline if model else 'none loaded -- rules only'}{RESET}\n")
    print(f"  {'time':>7}  {'trust':^11} {'reading':<24} {'context':<12} "
          f"{'sev':<8} why")
    print(f"  {'-'*7}  {'-'*11} {'-'*24} {'-'*12} {'-'*8} {'-'*40}")

    since_hop = 0
    try:
        for s in source:
            ring.append(s)
            if bridge is not None:
                bridge.state.tick(s.t_ms)   # 10 ms resolution, not 1 s
            if fh:
                fh.write(",".join(str(getattr(s, f)) for f in FIELDS) + "\n")
            since_hop += 1
            if len(ring) < ring.maxlen or since_hop < hop_n:
                continue
            since_hop = 0

            w = next(windows(list(ring), window_s=window_s, hop_s=window_s))
            v = assess(w)
            cov.add(v)
            est = hr.estimate(w.cols["ppg_ir"])
            pb.update(w, v, est)
            d = pb.deviation(est, v)

            p_model = agree = None
            verdict_out = None
            if d is None:
                base = pb.snapshot()
                if not v.scored:
                    # THE moment. No number. Not the last one, not an estimate.
                    reading = f"{RED}  --  no reading  --{RESET}"
                    why = v.reasons[0] if v.reasons else "quality gate refused"
                    verdict_out = ("unscored", "--", "", why)
                else:
                    reading = f"{DIM}  -- calibrating --{RESET}"
                    why = f"{base.coverage_s:.0f}/{MIN_COVERAGE_S:.0f}s of clean rest"
                    verdict_out = (v.ppg.value, "--", "CALIBRATING", why)
                ctx = sev = ""
            else:
                reading = f"{BOLD}{d.hr_bpm:5.0f}{RESET} bpm  {d.delta_bpm:+5.0f}  {d.personal_sigma:+5.1f}sd"
                sc = ss.push(score(d, v.metrics["motion"], pb.gsr_deviation(w)))
                ctx, why = sc.context.value.upper(), sc.explanation
                sev = sc.severity.name
                verdict_out = (v.ppg.value, f"{d.hr_bpm:.0f}",
                               sc.context.value.upper(), sc.explanation)
                if model is not None:
                    # The SAME vector build_features.py trained on. Building it
                    # a second way here would be a second definition of the
                    # model's input, which is how a model silently starts
                    # scoring something other than what it learned.
                    p_model = model.p_arousal(
                        features.extract(w, d, est, v.metrics["motion"], pb))
                    agree = model.agreement(p_model, sc.context)
                if sc.severity.value >= Severity.CONCERN.value:
                    ctx = f"{RED}{ctx}{RESET}"

            if bridge is not None:
                b = pb.snapshot()
                bridge.state.publish(
                    device_t_ms=int(w.t_end_ms),
                    trust=v.ppg.value,
                    calibrated=b.calibrated,
                    personal_sigma=(d.personal_sigma if d is not None else None),
                    gsr_sigma=(pb.gsr_deviation(w) if d is not None else None),
                    hr_bpm=(d.hr_bpm if d is not None else None),
                    context=(sc.context.value if d is not None else None),
                    model_p=p_model, agreement=agree,
                )

            if verdict_out and hasattr(source, "send_verdict"):
                source.send_verdict(*verdict_out)

            mcol = "" if p_model is None else (
                f"{YELLOW}p={p_model:.2f} model disagrees{RESET}"
                if agree == "disagree" else f"{DIM}p={p_model:.2f}{RESET}")
            print(f"  {w.t_end_ms/1000:6.1f}s  {BADGE[v.ppg]} {reading:<24} "
                  f"{ctx:<12} {sev:<8} {DIM}{why}{RESET} {mcol}")
    except KeyboardInterrupt:
        print(f"\n{DIM}stopped{RESET}")
    finally:
        if fh:
            fh.close()
        if hasattr(source, "close"):
            source.close()
        print(f"\n{BOLD}{CYAN}{cov.summary()}{RESET}")
        if save:
            print(f"{DIM}saved -> {save}{RESET}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--serial", metavar="PORT", help="live device, e.g. /dev/ttyUSB0")
    src.add_argument("--file", metavar="CSV", help="replay a recording")
    src.add_argument("--synth", metavar="SCENARIO", choices=sorted(synth.SCENARIOS),
                     help=f"no hardware: {', '.join(sorted(synth.SCENARIOS))}")
    p.add_argument("--save", metavar="CSV", help="write every row received")
    p.add_argument("--seconds", type=float, default=180.0, help="--synth duration")
    p.add_argument("--fast", action="store_true", help="do not pace replay at 100 Hz")
    p.add_argument("--calibrate", metavar="CSV",
                   help="resting recording to learn the baseline from first")
    p.add_argument("--calibrate-synth", metavar="SCENARIO",
                   help="calibrate on synthetic data, e.g. rest")
    p.add_argument("--load-baseline", metavar="JSON",
                   help="restore a baseline saved by --save-baseline")
    p.add_argument("--save-baseline", metavar="JSON",
                   help="persist the learned baseline for later runs")
    p.add_argument("--bridge", nargs="?", const=8765, type=int, metavar="PORT",
                   help="serve game/ and carry its events onto the device clock")
    p.add_argument("--camera", metavar="URL",
                   help="phone stream, e.g. http://192.168.29.164:8080/video")
    p.add_argument("--session", metavar="JSON", default="session.json",
                   help="where --bridge writes events + the clock audit")
    p.add_argument("--hop", type=float, default=DEFAULT_HOP_S)
    p.add_argument("--window", type=float, default=DEFAULT_WINDOW_S)
    a = p.parse_args()

    pb = LoadedBaseline(a.load_baseline) if a.load_baseline else PersonalBaseline()
    if a.calibrate:
        calibrate_from(pb, read_csv(a.calibrate), a.hop, a.window)
    elif a.calibrate_synth:
        calibrate_from(pb, synth.generate(a.calibrate_synth, duration_s=150.0, seed=7),
                       a.hop, a.window)
    if a.calibrate or a.calibrate_synth:
        b = pb.snapshot()
        print(f"{DIM}calibrated: {b}{RESET}")
        if a.save_baseline:
            save_baseline(pb, a.save_baseline)

    if a.serial:
        source = SerialSource(a.serial)
    elif a.file:
        source = from_file(a.file, realtime=not a.fast)
    else:
        source = from_synth(a.synth, a.seconds, realtime=not a.fast)

    br = None
    if a.bridge:
        from vitalguard.bridge import Bridge
        br = Bridge(Path(__file__).parent / "game", port=a.bridge).start()
        print(f"{BOLD}bridge{RESET} {CYAN}{br.url}{RESET}  "
              f"{DIM}open the game there, NOT from file://{RESET}\n")
    cam = None
    if a.camera:
        if br is None:
            print(f"{YELLOW}--camera needs --bridge (the camera is stamped with the "
                  f"device clock the bridge carries){RESET}")
            return
        from vitalguard.camera import CameraRunner
        cam = CameraRunner(a.camera, str(Path(__file__).parent / "models" /
                                         "face_detection_yunet_2023mar.onnx"),
                           stamp=lambda: br.state.device_t_ms,
                           sink=br.state.record_face).start()
        br.state.camera = cam
        print(f"{BOLD}camera{RESET} {CYAN}{a.camera}{RESET}\n")

    try:
        run(source, a.save, a.hop, a.window, pb=pb, bridge=br)
    finally:
        if cam is not None:
            cam.stop()
            ff = cam.face_fraction
            state = (f"{RED}{cam.error}{RESET}" if cam.error
                     else f"{GREEN}ok{RESET}" if (ff or 0) > 0.8
                     else f"{YELLOW}face in {(ff or 0)*100:.0f}% of frames{RESET}")
            print(f"\n{BOLD}camera{RESET} {state}  {DIM}{cam.frames} frames, "
                  f"rotation {cam.rotation}, {cam.reconnects} reconnects{RESET}")
            for k, v in br.state.face_summary().items():
                print(f"  {DIM}{k:<22}{RESET} {'--' if v is None else f'{v:8.3f}'}")
        if br is not None:
            audit = br.save(a.session)
            # The alignment is reported, never assumed. If this says no, the
            # report cannot put behaviour and physiology on one timeline and
            # must say so rather than quietly interleaving them.
            verdict = f"{GREEN}alignable{RESET}" if audit.alignable else f"{RED}NOT alignable{RESET}"
            print(f"\n{BOLD}clock{RESET} {verdict}  "
                  f"{DIM}n={audit.n} offset={audit.offset_median_ms} "
                  f"spread={audit.offset_spread_ms}ms unstamped={audit.unstamped}{RESET}")
            print(f"{DIM}session -> {a.session}{RESET}")
            br.stop()


if __name__ == "__main__":
    main()
