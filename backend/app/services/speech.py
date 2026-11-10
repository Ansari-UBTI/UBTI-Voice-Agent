"""Azure Speech: voice preview (TTS) and Personal Voice (voice cloning)."""
import asyncio
import html
import logging

import httpx

from .. import db
from ..config import settings
from . import storage

log = logging.getLogger("speech")
CV_VER = "2024-02-01-preview"


def _key():
    if not settings.SPEECH_KEY:
        raise RuntimeError("AZURE_SPEECH_KEY not configured")
    return {"Ocp-Apim-Subscription-Key": settings.SPEECH_KEY}


async def tts(text: str, voice: str, locale: str = "en-US", speaker_profile_id: str | None = None) -> bytes:
    text = html.escape(text)
    if speaker_profile_id:
        inner = (f"<voice name='DragonLatestNeural'><mstts:ttsembedding speakerProfileId='{speaker_profile_id}'>"
                 f"<lang xml:lang='{locale}'>{text}</lang></mstts:ttsembedding></voice>")
    else:
        inner = f"<voice name='{voice}'>{text}</voice>"
    ssml = (f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' "
            f"xmlns:mstts='http://www.w3.org/2001/mstts' xml:lang='{locale}'>{inner}</speak>")
    url = f"https://{settings.SPEECH_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(url, content=ssml.encode(), headers={
            **_key(), "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3", "User-Agent": "ubti-voice"})
        r.raise_for_status()
        return r.content


async def _wait(c: httpx.AsyncClient, r: httpx.Response):
    op = r.headers.get("Operation-Location")
    if not op:
        return
    for _ in range(60):
        await asyncio.sleep(3)
        s = (await c.get(op, headers=_key())).json()
        if s.get("status") == "Succeeded":
            return
        if s.get("status") in ("Failed", "Canceled"):
            raise RuntimeError(f"Azure operation failed: {s}")
    raise RuntimeError("Azure operation timed out")


async def create_personal_voice(voice_id: str):
    """Runs after admin approval: project -> consent -> personal voice -> speakerProfileId."""
    v = await asyncio.to_thread(db.one, "SELECT * FROM voices WHERE id = ?", voice_id)
    base = f"https://{settings.SPEECH_REGION}.api.cognitive.microsoft.com/customvoice"
    pid, cid, vid = f"ubti-{v['tenant_id'][:8]}", f"consent-{voice_id[:18]}", f"pv-{voice_id[:18]}"
    try:
        await asyncio.to_thread(db.update, "voices", voice_id, {"status": "processing"})
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.put(f"{base}/projects/{pid}?api-version={CV_VER}", headers=_key(),
                            json={"description": "UBTI tenant voices", "kind": "PersonalVoice"})
            if r.status_code not in (200, 201, 409):
                r.raise_for_status()
            consent = await asyncio.to_thread(storage.get_bytes, v["consent_path"])
            r = await c.post(f"{base}/consents/{cid}?api-version={CV_VER}", headers=_key(),
                             data={"projectId": pid, "voiceTalentName": v["talent_name"],
                                   "companyName": v["company_name"], "locale": v["locale"]},
                             files={"audiodata": ("consent.wav", consent, "audio/wav")})
            r.raise_for_status()
            await _wait(c, r)
            sample = await asyncio.to_thread(storage.get_bytes, v["sample_path"])
            r = await c.post(f"{base}/personalvoices/{vid}?api-version={CV_VER}", headers=_key(),
                             data={"projectId": pid, "consentId": cid},
                             files={"audiodata": ("sample.wav", sample, "audio/wav")})
            r.raise_for_status()
            await _wait(c, r)
            pv = (await c.get(f"{base}/personalvoices/{vid}?api-version={CV_VER}", headers=_key())).json()
        await asyncio.to_thread(db.update, "voices", voice_id, {
            "status": "ready", "speaker_profile_id": pv.get("speakerProfileId"), "error": None})
    except Exception as e:
        log.exception("personal voice failed")
        await asyncio.to_thread(db.update, "voices", voice_id, {"status": "failed", "error": str(e)[:900]})
