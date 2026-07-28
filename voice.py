#!/usr/bin/env python3
"""
voice.py — PJ terminal voice mode over WebRTC (no browser required).

Run with:
    pj voice        (or: pj --voice)
    pj voice --no-gate    # disable mic echo suppression (headphone users)
    pj voice --meter      # measure mic levels to calibrate gate thresholds

Establishes a WebRTC peer connection (via aiortc) straight to the OpenAI
Realtime API — the same transport webrtc_client.html uses, minus the
browser: SDP offer is POSTed to https://api.openai.com/v1/realtime/calls,
mic audio streams out over an Opus RTP track, PJ's spoken replies stream
back on the remote audio track, and realtime events (transcripts, function
calls) flow over the "oai-events" data channel.

Uses the same realtime session configuration as realtime_server.py
(realtime_config.py): PJ's instructions, voice, server-side VAD, live input
transcription, and the function-calling skills from skills.py. Skill calls
are executed locally and their results fed back into the conversation.

Ctrl+C exits cleanly.
"""
import asyncio
import fractions
import json
import math
import os
import queue
import sys
import time

import requests
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import AudioStreamTrack, MediaStreamError

import skills
from realtime_config import realtime_session_config
from responses_runtime import dispatch_realtime_function, terminal_approval_handler

REALTIME_CALLS_URL = "https://api.openai.com/v1/realtime/calls"
SAMPLE_RATE = 48_000  # WebRTC/Opus native rate
CHANNELS = 1
FRAME_MS = 20
SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_MS // 1000

VOICE_EXTRA_INSTRUCTIONS = (
    "You are speaking with the user live over voice from their terminal. "
    "Keep replies conversational and concise. If they speak in another "
    "language, respond in that same language (live translation mode) unless "
    "asked to translate into a specific target language."
)

# Echo suppression: without it the mic hears PJ's own speaker output, which
# gets transcribed as user speech and trips server VAD (barge-in), cutting
# PJ off mid-sentence in a feedback loop.
# All thresholds are overridable via environment variables — run
# `pj voice --meter` to measure your room and pick good values.
GATE_TAIL_S = 0.25       # keep gating this long after playback stops (speaker decay)
BARGE_IN_RMS = int(os.environ.get("PJ_BARGE_IN_RMS", 3000))
# Single loud frames (a click, a cough, a bleed spike) must not barge in —
# gpt-4o-transcribe hallucinates sub-second blips into foreign fragments
# ("אני", "Nein") that cut PJ off. Loudness must be *sustained* to count as
# the user deliberately talking over PJ.
BARGE_IN_HOLD_S = float(os.environ.get("PJ_BARGE_IN_HOLD_MS", 280)) / 1000
# Noise gate: faint ambient noise/room bleed still trips server VAD and gets
# hallucinated into words by the transcriber ("Izvini", "Lütfen", ...), which
# PJ then answers. Suppress mic frames below the floor at all times; the
# hangover keeps quiet word-endings from being clipped mid-utterance.
NOISE_FLOOR_RMS = int(os.environ.get("PJ_NOISE_FLOOR_RMS", 700))
NOISE_HANGOVER_S = 0.6   # keep passing audio this long after the last loud frame
_SILENCE = b"\x00" * (SAMPLES_PER_FRAME * 2)  # one pcm16 mono frame of silence

# Terminal-specific input tuning, mirroring the playground's defaults: the
# browser flow gets echo cancellation / noise suppression from getUserMedia,
# but our raw sounddevice capture has no DSP — so lean on the server side:
# far-field noise reduction plus semantic VAD (detects turns from speech
# content, not raw energy, so hiss/bleed doesn't start phantom turns).
NOISE_REDUCTION = {"type": "far_field"}
TURN_DETECTION = {
    "type": "semantic_vad",
    # "low" waits for clearer end-of-utterance evidence before taking a
    # turn, so sub-second blips don't count as user speech. Override with
    # PJ_VAD_EAGERNESS=auto|low|medium|high.
    "eagerness": os.environ.get("PJ_VAD_EAGERNESS", "low"),
}
# Pin transcription to English so residual noise can't be hallucinated into
# random-language fragments. Override (e.g. PJ_VOICE_LANG=es) or set empty
# (PJ_VOICE_LANG=) for auto-detect if you actually speak other languages.
TRANSCRIPTION_LANGUAGE = os.environ.get("PJ_VOICE_LANG", "en")


def frame_rms(data: bytes) -> float:
    """RMS level of a pcm16-le frame (pure Python, no numpy)."""
    n = len(data) // 2
    if not n:
        return 0.0
    samples = memoryview(data).cast("h")
    return math.sqrt(sum(s * s for s in samples) / n)


class EchoGate:
    """Mic gate combining a noise floor with half-duplex echo suppression.

    Always: frames below the ambient noise floor are silenced (with a
    hangover after the last loud frame so word tails survive) — server VAD
    never hears faint room noise, so no phantom turns or hallucinated
    transcripts.

    While assistant audio is playing (plus a short tail), the bar rises to
    the barge-in threshold — and loudness must be *sustained* for hold_s
    (~a spoken word, not a click or bleed spike) before frames pass, so the
    user can still interrupt PJ deliberately but transients cannot.
    """

    def __init__(self, enabled=True, tail_s=GATE_TAIL_S,
                 barge_in_rms=BARGE_IN_RMS, noise_floor_rms=NOISE_FLOOR_RMS,
                 hangover_s=NOISE_HANGOVER_S, hold_s=BARGE_IN_HOLD_S,
                 clock=time.monotonic):
        self.enabled = enabled
        self._tail_s = tail_s
        self._barge_in_rms = barge_in_rms
        self._noise_floor_rms = noise_floor_rms
        self._hangover_s = hangover_s
        self._hold_s = hold_s
        self._clock = clock
        self._last_playback = float("-inf")
        self._last_loud = float("-inf")
        self._streak_start = None  # start of current sustained-loud streak

    def mark_playback(self):
        """Called whenever assistant audio is actually being played."""
        self._last_playback = self._clock()

    def gating(self) -> bool:
        """True while echo (half-duplex) gating is active."""
        return (self.enabled
                and self._clock() - self._last_playback < self._tail_s)

    def filter(self, data: bytes) -> bytes:
        """Returns the mic frame, or silence if it should be suppressed."""
        if not self.enabled:
            return data
        rms = frame_rms(data)
        now = self._clock()
        if self.gating():
            # Assistant is speaking: only *sustained* loudness passes.
            if rms < self._barge_in_rms:
                self._streak_start = None
                return _SILENCE
            if self._streak_start is None:
                self._streak_start = now
            if now - self._streak_start < self._hold_s:
                return _SILENCE  # not sustained long enough yet
            self._last_loud = now
            return data
        self._streak_start = None
        # Idle: noise gate with hangover.
        if rms >= self._noise_floor_rms:
            self._last_loud = now
            return data
        if now - self._last_loud < self._hangover_s:
            return data  # quiet tail of an utterance
        return _SILENCE


def _import_audio():
    """Imports sounddevice, failing with a clear, actionable error."""
    try:
        import sounddevice as sd
        return sd
    except OSError as e:  # PortAudio library missing
        raise SystemExit(
            f"Voice mode needs PortAudio (sounddevice failed to load: {e}).\n"
            "On macOS: brew install portaudio"
        )
    except ImportError:
        raise SystemExit(
            "Voice mode needs the 'sounddevice' package — install it with:\n"
            "    ./venv/bin/pip install sounddevice"
        )


class MicrophoneTrack(AudioStreamTrack):
    """Outgoing WebRTC audio track fed from the local microphone."""

    def __init__(self, sd, gate: EchoGate):
        super().__init__()
        import av
        self._av = av
        self._gate = gate
        self._q: queue.Queue = queue.Queue()
        self._timestamp = 0
        self._stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16",
            blocksize=SAMPLES_PER_FRAME,
            callback=lambda indata, *_: self._q.put(bytes(indata)))
        self._stream.start()

    async def recv(self):
        if self.readyState != "live":
            raise MediaStreamError
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, self._q.get)
        data = self._gate.filter(data)  # echo suppression
        frame = self._av.AudioFrame(format="s16", layout="mono",
                                    samples=SAMPLES_PER_FRAME)
        frame.planes[0].update(data)
        frame.sample_rate = SAMPLE_RATE
        frame.pts = self._timestamp
        frame.time_base = fractions.Fraction(1, SAMPLE_RATE)
        self._timestamp += SAMPLES_PER_FRAME
        return frame

    def close(self):
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass


class SpeakerSink:
    """Plays a remote WebRTC audio track through the local speakers."""

    def __init__(self, sd, gate: EchoGate):
        self._gate = gate
        self._q: queue.Queue = queue.Queue()
        self._task = None
        self._stream = sd.RawOutputStream(
            samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16",
            blocksize=SAMPLES_PER_FRAME, callback=self._callback)
        self._stream.start()

    def _callback(self, outdata, frames, time_info, status):
        buf = b""
        while len(buf) < len(outdata):
            try:
                buf += self._q.get_nowait()
            except queue.Empty:
                break
        if len(buf) > len(outdata):  # keep overflow for the next block
            self._q.queue.appendleft(buf[len(outdata):])
            buf = buf[:len(outdata)]
        if buf:
            self._gate.mark_playback()
        outdata[:len(buf)] = buf
        outdata[len(buf):] = b"\x00" * (len(outdata) - len(buf))

    def play(self, track):
        self._task = asyncio.ensure_future(self._consume(track))

    async def _consume(self, track):
        import av
        resampler = av.AudioResampler(format="s16", layout="mono",
                                      rate=SAMPLE_RATE)
        while True:
            try:
                frame = await track.recv()
            except MediaStreamError:
                return
            for out in resampler.resample(frame):
                self._q.put(bytes(out.planes[0])[:out.samples * 2])

    def clear(self):
        """Barge-in: drop any queued assistant audio."""
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    def close(self):
        if self._task:
            self._task.cancel()
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass


def _open_audio(gate: EchoGate):
    sd = _import_audio()
    try:
        mic = MicrophoneTrack(sd, gate)
        spk = SpeakerSink(sd, gate)
    except Exception as e:
        raise SystemExit(
            f"Could not open microphone/speakers: {e}\n"
            "Terminal voice mode needs local audio hardware. Check System "
            "Settings → Privacy & Security → Microphone, or use the browser "
            "flow (realtime_server.py + webrtc_client.html) instead."
        )
    return mic, spk


def _voice_approval_handler(event):
    return terminal_approval_handler(event)


def _run_tool_call(
        name: str,
        arguments: str,
        *,
        approval_handler=_voice_approval_handler):
    """Executes a local skill; returns a JSON string result."""
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        args = {}
    print(f"\n🔧 {name}({json.dumps(args)})", flush=True)
    try:
        approval_granted = False
        if skills.tool_policy_mode(name) == "approval":
            try:
                approval_granted = bool(approval_handler({
                    "type": "approval.required",
                    "approval_kind": "local_function",
                    "name": name,
                    "arguments": args,
                }))
            except (EOFError, KeyboardInterrupt):
                approval_granted = False
            if not approval_granted:
                result = {"error": f"Tool '{name}' was rejected by the owner."}
            else:
                result = dispatch_realtime_function(
                    name,
                    args,
                    approval_granted=True,
                )
        else:
            result = dispatch_realtime_function(name, args)
    except Exception as e:
        result = {"error": str(e)}
    print(f"   ✅ {json.dumps(result)}", flush=True)
    return json.dumps(result)


class EventHandler:
    """Handles realtime events from the oai-events data channel: streaming
    transcripts, streaming function calls, and barge-in."""

    def __init__(self, channel, speaker: SpeakerSink):
        self._channel = channel
        self._speaker = speaker
        self._assistant_speaking = False
        # Function-call arguments stream in as deltas; show progress once
        # per call, then execute on .done (mirrors pj.py's streaming flow).
        self._calls_announced = set()

    def handle(self, raw: str):
        event = json.loads(raw)
        etype = event.get("type")

        if etype == "response.output_audio_transcript.delta":
            if not self._assistant_speaking:
                print("PJ: ", end="", flush=True)
                self._assistant_speaking = True
            print(event.get("delta", ""), end="", flush=True)

        elif etype == "response.output_audio_transcript.done":
            if self._assistant_speaking:
                print(flush=True)
                self._assistant_speaking = False

        elif etype == "conversation.item.input_audio_transcription.completed":
            transcript = (event.get("transcript") or "").strip()
            if transcript:
                if self._assistant_speaking:
                    # Break the assistant's in-progress line so speakers
                    # don't garble together; next delta reprints "PJ: ".
                    print(flush=True)
                    self._assistant_speaking = False
                print(f"You: {transcript}", flush=True)

        elif etype == "input_audio_buffer.speech_started":
            # Barge-in: stop playback immediately when the user speaks.
            self._speaker.clear()
            if self._assistant_speaking:
                print(" ⏹", flush=True)
                self._assistant_speaking = False

        elif etype == "response.function_call_arguments.delta":
            call_id = event.get("call_id") or event.get("item_id")
            if call_id not in self._calls_announced:
                self._calls_announced.add(call_id)
                print("\n🔧 PJ is calling a function...", flush=True)

        elif etype == "response.function_call_arguments.done":
            asyncio.ensure_future(self._finish_tool_call(event))

        elif etype == "error":
            err = event.get("error") or {}
            print(f"\n⚠️  Realtime error: {err.get('message', event)}",
                  file=sys.stderr, flush=True)

    async def _finish_tool_call(self, event):
        loop = asyncio.get_running_loop()
        output = await loop.run_in_executor(
            None, _run_tool_call, event["name"], event.get("arguments"))
        self._channel.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": event["call_id"],
                "output": output,
            },
        }))
        self._channel.send(json.dumps({"type": "response.create"}))


async def _connect(api_key: str, mic: MicrophoneTrack, spk: SpeakerSink):
    """Sets up the peer connection and exchanges SDP with OpenAI."""
    pc = RTCPeerConnection()
    pc.addTrack(mic)
    channel = pc.createDataChannel("oai-events")
    handler = EventHandler(channel, spk)
    channel.on("message", handler.handle)
    pc.on("track", lambda track: spk.play(track)
          if track.kind == "audio" else None)

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    session_cfg = realtime_session_config(VOICE_EXTRA_INSTRUCTIONS)
    # Terminal-specific input processing (see NOISE_REDUCTION/TURN_DETECTION).
    session_cfg["audio"]["input"]["noise_reduction"] = NOISE_REDUCTION
    session_cfg["audio"]["input"]["turn_detection"] = TURN_DETECTION
    if TRANSCRIPTION_LANGUAGE:
        session_cfg["audio"]["input"]["transcription"]["language"] = \
            TRANSCRIPTION_LANGUAGE
    loop = asyncio.get_running_loop()
    resp = await loop.run_in_executor(None, lambda: requests.post(
        REALTIME_CALLS_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        files={
            "sdp": (None, pc.localDescription.sdp, "application/sdp"),
            "session": (None, json.dumps(session_cfg), "application/json"),
        },
        timeout=30,
    ))
    if resp.status_code >= 400:
        await pc.close()
        raise SystemExit(f"Realtime call setup failed "
                         f"({resp.status_code}): {resp.text}")
    await pc.setRemoteDescription(
        RTCSessionDescription(sdp=resp.text, type="answer"))
    return pc


async def _main(gate_enabled=True):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY not set — run via ./pj so ~/.env is loaded")

    gate = EchoGate(enabled=gate_enabled)
    mic, spk = _open_audio(gate)
    pc = await _connect(api_key, mic, spk)
    from realtime_config import REALTIME_MODEL
    mode = "echo gate on — interrupt by speaking up" if gate_enabled \
        else "echo gate off (headphones)"
    print(f"🎙  PJ voice mode ({REALTIME_MODEL}, WebRTC, {mode}) — speak "
          "whenever you're ready. Ctrl+C to exit.\n", flush=True)

    closed = asyncio.Event()

    @pc.on("connectionstatechange")
    async def _on_state():
        if pc.connectionState in ("failed", "closed", "disconnected"):
            closed.set()

    try:
        await closed.wait()
        print("\nConnection closed.", flush=True)
    finally:
        mic.close()
        spk.close()
        await pc.close()


def run(gate_enabled=True):
    """Entry point for terminal voice mode (called from pj.py).

    gate_enabled=False (`pj voice --no-gate`) disables mic echo suppression
    for headphone users who want unrestricted barge-in.
    """
def meter(seconds=12):
    """Mic calibration: shows live RMS so gate thresholds can be tuned.

    Run `pj voice --meter`, stay quiet for a few seconds, then speak
    normally, then (optionally) play PJ's voice from a previous session at
    your usual volume to measure speaker bleed. Suggested settings follow.
    """
    sd = _import_audio()
    print(f"🎚  Mic meter — {seconds}s. Stay QUIET first, then SPEAK "
          "normally. Ctrl+C to stop early.\n", flush=True)
    levels = []
    try:
        with sd.RawInputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                               dtype="int16", blocksize=SAMPLES_PER_FRAME) as stream:
            frames = int(seconds * 1000 / FRAME_MS)
            for i in range(frames):
                data, _ = stream.read(SAMPLES_PER_FRAME)
                rms = frame_rms(bytes(data))
                levels.append(rms)
                if i % 10 == 0:  # ~5 updates/sec
                    bar = "█" * min(60, int(rms / 100))
                    print(f"\r  RMS {rms:7.0f} |{bar:<60}|", end="", flush=True)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        raise SystemExit(f"\nCould not open microphone: {e}")

    if not levels:
        return
    levels.sort()
    quiet = levels[len(levels) // 4]        # 25th percentile ≈ ambient floor
    peak = levels[-1]
    floor = max(300, int(quiet * 3))
    barge = max(floor * 3, int(peak * 0.5))
    print(f"\n\n  ambient (p25): {quiet:.0f}   peak: {peak:.0f}")
    print(f"  current:   PJ_NOISE_FLOOR_RMS={NOISE_FLOOR_RMS}  "
          f"PJ_BARGE_IN_RMS={BARGE_IN_RMS}")
    print(f"  suggested: PJ_NOISE_FLOOR_RMS={floor}  PJ_BARGE_IN_RMS={barge}")
    print("\n  Set them in ~/.env (or export before `pj voice`) to apply.")


def run(gate_enabled=True, args=()):
    """Entry point for terminal voice mode (called from pj.py).

    gate_enabled=False (`pj voice --no-gate`) disables mic echo suppression
    for headphone users who want unrestricted barge-in.
    `pj voice --meter` runs mic calibration instead of a session.
    """
    if "--meter" in args:
        meter()
        return
    try:
        asyncio.run(_main(gate_enabled))
    except KeyboardInterrupt:
        print("\nBye!")


if __name__ == "__main__":
    run("--no-gate" not in sys.argv[1:], sys.argv[1:])
