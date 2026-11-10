import asyncio
import io
import re
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from .. import db
from ..config import settings
from ..security import current_user, member
from ..services import kb, netguard

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class TextIn(BaseModel):
    name: str
    content: str
    agent_id: Optional[str] = None


class UrlIn(BaseModel):
    url: str
    agent_id: Optional[str] = None


def _check_agent(u, agent_id):
    if agent_id and not db.one("SELECT id FROM agents WHERE id = ? AND tenant_id = ?", agent_id, u["tenant_id"]):
        raise HTTPException(404, "Agent not found")


async def _save(u, name, content, source, agent_id, source_url=None):
    content = re.sub(r"\n{3,}", "\n\n", content or "").strip()
    if not content:
        raise HTTPException(400, "No readable text found")
    await asyncio.to_thread(_check_agent, u, agent_id)
    doc = await asyncio.to_thread(db.insert, "knowledge_docs", {
        "tenant_id": u["tenant_id"], "agent_id": agent_id or None, "name": name[:200], "source_type": source,
        "source_url": source_url, "content": content[:500000], "chars": len(content[:500000]), "created_at": db.now()})
    await kb.index_doc(doc)
    return {k: v for k, v in doc.items() if k != "content"}


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h\d|tr)>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    return re.sub(r"[ \t]+", " ", text)


@router.get("")
def list_docs(u=Depends(current_user)):
    return db.query("""SELECT k.id, k.name, k.source_type, k.source_url, k.chars, k.chunks, k.embedded, k.agent_id,
                       k.created_at, a.name AS agent_name
                       FROM knowledge_docs k LEFT JOIN agents a ON a.id = k.agent_id
                       WHERE k.tenant_id = ? ORDER BY k.created_at DESC""", u["tenant_id"])


@router.get("/status")
def status(u=Depends(current_user)):
    total = db.scalar("SELECT ISNULL(SUM(chars), 0) FROM knowledge_docs WHERE tenant_id = ?", u["tenant_id"]) or 0
    return {"inline_limit": settings.KB_INLINE_CHARS, "embeddings": kb.embeddings_enabled(), "total_chars": total}


@router.post("/text")
async def add_text(b: TextIn, u=Depends(member)):
    return await _save(u, b.name, b.content, "text", b.agent_id)


@router.post("/url")
async def add_url(b: UrlIn, u=Depends(member)):
    try:
        r = await netguard.request("GET", b.url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (UBTI knowledge)"})
        r.raise_for_status()
    except netguard.BlockedURL as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Could not fetch URL: {e}")
    return await _save(u, b.url, _html_to_text(r.text), "url", b.agent_id, b.url)


@router.post("/file")
async def add_file(file: UploadFile = File(...), agent_id: str = Form(""), u=Depends(member)):
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 20 MB)")
    name = file.filename or "document"
    ext = name.lower().rsplit(".", 1)[-1]
    if ext == "pdf":
        from pypdf import PdfReader
        text = "\n\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)
    elif ext == "docx":
        from docx import Document
        text = "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs)
    elif ext in ("txt", "md", "csv", "json"):
        text = data.decode("utf-8", errors="ignore")
    else:
        raise HTTPException(400, "Supported: PDF, DOCX, TXT, MD, CSV, JSON")
    return await _save(u, name, text, ext, agent_id or None)


@router.post("/{doc_id}/refresh")
async def refresh(doc_id: str, u=Depends(member)):
    """Re-fetch a URL document (or just rebuild its search index)."""
    d = await asyncio.to_thread(db.one, "SELECT * FROM knowledge_docs WHERE id = ? AND tenant_id = ?", doc_id,
                                u["tenant_id"])
    if not d:
        raise HTTPException(404)
    if d.get("source_type") == "url" and (d.get("source_url") or d["name"]).startswith("http"):
        try:
            r = await netguard.request("GET", d.get("source_url") or d["name"], timeout=20)
            r.raise_for_status()
            text = re.sub(r"\n{3,}", "\n\n", _html_to_text(r.text)).strip()
            if text:
                await asyncio.to_thread(db.update, "knowledge_docs", doc_id,
                                        {"content": text[:500000], "chars": len(text[:500000])})
                d["content"] = text
        except Exception as e:
            raise HTTPException(400, f"Could not fetch URL: {e}")
    await kb.index_doc(d)
    return {"ok": True}


@router.post("/reindex")
async def reindex(u=Depends(member)):
    """Rebuild chunks / embeddings for every document (e.g. after enabling embeddings)."""
    docs = await asyncio.to_thread(db.query, "SELECT * FROM knowledge_docs WHERE tenant_id = ?", u["tenant_id"])
    for d in docs:
        await kb.index_doc(d)
    return {"documents": len(docs)}


@router.get("/{doc_id}")
def get_doc(doc_id: str, u=Depends(current_user)):
    d = db.one("SELECT * FROM knowledge_docs WHERE id = ? AND tenant_id = ?", doc_id, u["tenant_id"])
    if not d:
        raise HTTPException(404)
    return d


@router.delete("/{doc_id}")
async def delete_doc(doc_id: str, u=Depends(member)):
    await asyncio.to_thread(db.execute, "DELETE FROM knowledge_chunks WHERE doc_id = ? AND tenant_id = ?", doc_id,
                            u["tenant_id"])
    await asyncio.to_thread(db.execute, "DELETE FROM knowledge_docs WHERE id = ? AND tenant_id = ?", doc_id,
                            u["tenant_id"])
    await kb.invalidate()
    return {"ok": True}
