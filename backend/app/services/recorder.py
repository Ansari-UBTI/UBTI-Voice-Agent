"""Two-channel call recording made inside the bridge (left = caller, right = agent).

Works the same for Vobiz, Twilio, ACS and browser calls, needs no provider recording API, and writes to temp
files as the call goes (no large buffers in memory). At the end it is encoded to MP3 (ffmpeg) or WAV."""
import logging
import os
import shutil
import subprocess
import time
import wave

from ..config import settings
from .audio import audioop

log = logging.getLogger("recorder")


class Recorder:
    """All audio is stored as 16-bit mono PCM at `rate` (16 kHz, or 8 kHz for Twilio's 8 kHz line)."""

    def __init__(self, call_id: str, rate: int):
        self.rate = rate
        self.bps = rate * 2
        self.dir = os.path.join(settings.TMP_DIR, "rec", call_id)
        os.makedirs(self.dir, exist_ok=True)
        self.left = open(os.path.join(self.dir, "caller.raw"), "w+b")
        self.right = open(os.path.join(self.dir, "agent.raw"), "w+b")
        self.t0 = time.time()
        self.caller_pos = 0          # bytes written to the caller track (continuous stream)
        self.agent_end = 0           # furthest byte written to the agent track
        self._rs_in = None
        self._rs_out = None
        self.closed = False

    def _to_rate(self, pcm: bytes, src_rate: int, state_attr: str) -> bytes:
        if src_rate == self.rate:
            return pcm
        out, st = audioop.ratecv(pcm, 2, 1, src_rate, self.rate, getattr(self, state_attr))
        setattr(self, state_attr, st)
        return out

    def caller(self, pcm16: bytes, src_rate: int):
        if self.closed or not pcm16:
            return
        data = self._to_rate(pcm16, src_rate, "_rs_in")
        self.left.seek(self.caller_pos)
        self.left.write(data)
        self.caller_pos += len(data)

    def agent(self, pcm16: bytes, src_rate: int, at_time: float):
        """Agent audio that will start playing at wall-clock `at_time`."""
        if self.closed or not pcm16:
            return
        data = self._to_rate(pcm16, src_rate, "_rs_out")
        pos = max(0, int((at_time - self.t0) * self.bps))
        pos -= pos % 2
        self.right.seek(pos)
        self.right.write(data)
        self.agent_end = max(self.agent_end, pos + len(data))

    def cut_agent(self, at_time: float):
        """Caller interrupted: audio queued after this moment was never played - remove it."""
        if self.closed:
            return
        pos = max(0, int((at_time - self.t0) * self.bps))
        pos -= pos % 2
        if pos < self.agent_end:
            self.right.truncate(pos)
            self.agent_end = pos

    def finish(self) -> tuple[str, str] | None:
        """Returns (file path, content type) or None if nothing was recorded."""
        if self.closed:
            return None
        self.closed = True
        self.left.flush()
        self.right.flush()
        n = max(self.caller_pos, self.agent_end)
        if n < self.bps:               # under one second - nothing worth keeping
            self._close()
            return None
        for f in (self.left, self.right):          # pad both tracks to the same length
            f.seek(0, 2)
            size = f.tell()
            if size < n:
                f.write(b"\x00" * (n - size))
            f.flush()
        self._close()
        lp, rp = os.path.join(self.dir, "caller.raw"), os.path.join(self.dir, "agent.raw")
        if shutil.which("ffmpeg"):
            mp3 = os.path.join(self.dir, "recording.mp3")
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", str(self.rate), "-ac", "1", "-i", lp,
                   "-f", "s16le", "-ar", str(self.rate), "-ac", "1", "-i", rp,
                   "-filter_complex", "[0:a][1:a]join=inputs=2:channel_layout=stereo[a]", "-map", "[a]",
                   "-c:a", "libmp3lame", "-b:a", "48k", mp3]
            if subprocess.run(cmd, capture_output=True).returncode == 0:
                return mp3, "audio/mpeg"
            log.warning("ffmpeg failed, writing WAV instead")
        wav = os.path.join(self.dir, "recording.wav")
        with open(lp, "rb") as L, open(rp, "rb") as R, wave.open(wav, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(self.rate)
            while True:
                a, b = L.read(65536), R.read(65536)
                if not a and not b:
                    break
                a, b = a.ljust(len(b), b"\x00"), b.ljust(len(a), b"\x00")
                w.writeframes(audioop.add(audioop.tostereo(a, 2, 1, 0), audioop.tostereo(b, 2, 0, 1), 2))
        return wav, "audio/wav"

    def _close(self):
        for f in (self.left, self.right):
            try:
                f.close()
            except Exception:
                pass

    def cleanup(self):
        self._close()
        shutil.rmtree(self.dir, ignore_errors=True)
