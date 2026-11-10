import asyncio
import os
import shutil
import subprocess
import tempfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from .. import db
from ..security import current_user, member
from ..services import speech, storage
from ..services.catalog import VOICES

router = APIRouter(prefix="/api/voices", tags=["voices"])
SAMPLE = {"en": "Hi there! Thanks for calling. I'd be happy to help you with that today.",
          "hi": "नमस्ते! कॉल करने के लिए धन्यवाद। मैं आपकी कैसे मदद कर सकती हूँ?",
          "ta": "வணக்கம்! அழைத்ததற்கு நன்றி. நான் உங்களுக்கு எப்படி உதவலாம்?",
          "te": "నమస్కారం! కాల్ చేసినందుకు ధన్యవాదాలు. నేను మీకు ఎలా సహాయం చేయగలను?",
          "kn": "ನಮಸ್ಕಾರ! ಕರೆ ಮಾಡಿದ್ದಕ್ಕೆ ಧನ್ಯವಾದಗಳು. ನಾನು ಹೇಗೆ ಸಹಾಯ ಮಾಡಲಿ?",
          "ml": "നമസ്കാരം! വിളിച്ചതിന് നന്ദി. ഞാൻ എങ്ങനെ സഹായിക്കാം?",
          "ar": "مرحباً! شكراً لاتصالك. كيف يمكنني مساعدتك اليوم؟"}


@router.get("")
def library(u=Depends(current_user)):
    cloned = db.query("SELECT id, name, locale, status, speaker_profile_id, created_at, error FROM voices "
                      "WHERE tenant_id = ? ORDER BY created_at DESC", u["tenant_id"])
    return {"premade": VOICES, "cloned": cloned}


@router.get("/preview")
async def preview(voice: str = "", locale: str = "en-US", text: str = "", voice_id: str = "",
                  u=Depends(current_user)):
    text = text or SAMPLE.get(locale[:2], SAMPLE["en"])
    spid = None
    if voice_id:
        v = await asyncio.to_thread(db.one, "SELECT * FROM voices WHERE id = ? AND tenant_id = ?", voice_id,
                                    u["tenant_id"])
        if not v or not v.get("speaker_profile_id"):
            raise HTTPException(400, "Voice not ready yet")
        spid, locale = v["speaker_profile_id"], v["locale"]
    try:
        audio = await speech.tts(text[:400], voice, locale, spid)
    except Exception as e:
        raise HTTPException(400, f"Preview failed: {e}")
    return Response(audio, media_type="audio/mpeg")


def _to_wav(data: bytes) -> bytes:
    """Browser recordings (webm/ogg) -> 24 kHz mono WAV."""
    if data[:4] == b"RIFF" or not shutil.which("ffmpeg"):
        return data
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, "in"), os.path.join(d, "out.wav")
        with open(src, "wb") as f:
            f.write(data)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-ar", "24000", "-ac", "1", dst],
                       capture_output=True)
        with open(dst, "rb") as f:
            return f.read()


@router.post("/clone")
async def clone(name: str = Form(...), locale: str = Form("en-IN"), talent_name: str = Form(...),
                company_name: str = Form(...), consent_audio: UploadFile = File(...),
                sample_audio: UploadFile = File(...), u=Depends(member)):
    vid = db.new_id()
    paths = {}
    for f, kind in ((consent_audio, "consent"), (sample_audio, "sample")):
        data = await f.read()
        if len(data) > 25 * 1024 * 1024:
            raise HTTPException(400, "Audio too large (max 25 MB)")
        wav = await asyncio.to_thread(_to_wav, data)
        paths[kind] = f"voices/{u['tenant_id']}/{vid}/{kind}.wav"
        await asyncio.to_thread(storage.put_bytes, paths[kind], wav, "audio/wav")
    await asyncio.to_thread(db.insert, "voices", {
        "id": vid, "tenant_id": u["tenant_id"], "name": name, "locale": locale, "talent_name": talent_name,
        "company_name": company_name, "consent_path": paths["consent"], "sample_path": paths["sample"],
        "status": "pending_review", "created_by": u["id"], "created_at": db.now()})
    await asyncio.to_thread(db.audit, u["tenant_id"], u["id"], "voice.clone_request", name)
    return {"id": vid, "status": "pending_review"}


@router.delete("/{voice_id}")
def delete_voice(voice_id: str, u=Depends(member)):
    v = db.one("SELECT consent_path, sample_path FROM voices WHERE id = ? AND tenant_id = ?", voice_id, u["tenant_id"])
    if v:
        for p in (v.get("consent_path"), v.get("sample_path")):
            if p:
                storage.delete(p)
    db.execute("DELETE FROM voices WHERE id = ? AND tenant_id = ?", voice_id, u["tenant_id"])
    return {"ok": True}
