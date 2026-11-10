"""Knowledge base: chunking, embeddings and retrieval.

Small knowledge bases (<= KB_INLINE_CHARS) still go into the prompt whole - fastest, no extra round trip.
Larger ones are split into chunks; the agent gets a `search_knowledge` function and the top matching chunks are
returned during the call. Retrieval uses Azure OpenAI embeddings when AZURE_OPENAI_EMBED_DEPLOYMENT is set,
otherwise BM25 keyword ranking, so nothing is ever silently cut off."""
import asyncio
import logging
import math
import re
from collections import Counter

import httpx
import numpy as np

from .. import db
from ..config import settings
from . import hot

log = logging.getLogger("kb")
CHUNK, OVERLAP = 900, 150
_TOKEN = re.compile(r"[\w؀-ۿऀ-෿]+", re.U)


def chunk_text(text: str, size: int = CHUNK, overlap: int = OVERLAP) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        while len(p) > size:                       # very long paragraph -> hard split on sentence/space
            cut = max(p.rfind(". ", 0, size), p.rfind(" ", 0, size))
            cut = cut if cut > size // 2 else size
            piece, p = p[:cut + 1].strip(), p[max(0, cut + 1 - overlap):].strip()
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(piece)
        if len(cur) + len(p) + 2 <= size:
            cur = f"{cur}\n\n{p}" if cur else p
        else:
            if cur:
                chunks.append(cur)
            cur = (cur[-overlap:] + "\n\n" + p) if cur and overlap else p
    if cur:
        chunks.append(cur)
    return chunks


def embeddings_enabled() -> bool:
    return bool(settings.AOAI_ENDPOINT and settings.AOAI_KEY and settings.AOAI_EMBED_DEPLOYMENT)


async def embed(texts: list[str]) -> list[np.ndarray] | None:
    if not embeddings_enabled() or not texts:
        return None
    url = (f"{settings.AOAI_ENDPOINT}/openai/deployments/{settings.AOAI_EMBED_DEPLOYMENT}/embeddings"
           f"?api-version={settings.AOAI_API_VERSION}")
    out = []
    async with httpx.AsyncClient(timeout=60) as c:
        for i in range(0, len(texts), 64):
            r = await c.post(url, json={"input": texts[i:i + 64]}, headers={"api-key": settings.AOAI_KEY})
            r.raise_for_status()
            for d in sorted(r.json()["data"], key=lambda x: x["index"]):
                v = np.asarray(d["embedding"], dtype=np.float32)
                out.append(v / (np.linalg.norm(v) or 1.0))
    return out


async def index_doc(doc: dict):
    """(Re)build the chunks of one document."""
    chunks = chunk_text(doc["content"] or "")
    vecs = None
    try:
        vecs = await embed(chunks)
    except Exception as e:
        log.warning("embedding failed for %s, using keyword search: %s", doc["id"][:8], e)
    await asyncio.to_thread(db.execute, "DELETE FROM knowledge_chunks WHERE doc_id = ?", doc["id"])
    for i, ch in enumerate(chunks):
        await asyncio.to_thread(db.insert, "knowledge_chunks", {
            "doc_id": doc["id"], "tenant_id": doc["tenant_id"], "agent_id": doc.get("agent_id"), "seq": i,
            "text": ch, "embedding": vecs[i].tobytes() if vecs else None, "created_at": db.now()})
    await asyncio.to_thread(db.update, "knowledge_docs", doc["id"], {"chunks": len(chunks), "embedded": bool(vecs)})
    await invalidate()


async def invalidate():
    hot.drop_prefix("kb")
    from . import bus
    await bus.publish("cache", {"drop": "kb"})


# ---------------- per-agent index ----------------

class Index:
    def __init__(self, docs: list[dict], chunks: list[dict]):
        self.docs = docs
        self.total_chars = sum(d.get("chars") or len(d.get("content") or "") for d in docs)
        self.inline = self.total_chars <= settings.KB_INLINE_CHARS
        self.chunks = chunks
        vecs = [c.get("embedding") for c in chunks]
        self.matrix = None
        if chunks and all(vecs):
            self.matrix = np.vstack([np.frombuffer(v, dtype=np.float32) for v in vecs])
        self._bm25()

    def _bm25(self):
        self.toks = [[t.lower() for t in _TOKEN.findall(c["text"])] for c in self.chunks]
        n = len(self.toks) or 1
        df = Counter(t for toks in self.toks for t in set(toks))
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.avg = sum(len(t) for t in self.toks) / n if self.toks else 1
        self.tf = [Counter(t) for t in self.toks]

    def keyword(self, query: str, k: int) -> list[int]:
        q = [t.lower() for t in _TOKEN.findall(query)]
        scores = []
        for i, tf in enumerate(self.tf):
            ln = len(self.toks[i]) or 1
            s = sum(self.idf.get(t, 0) * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * ln / self.avg)) for t in q if t in tf)
            scores.append((s, i))
        return [i for s, i in sorted(scores, reverse=True)[:k] if s > 0]

    async def search(self, query: str, k: int = 4) -> list[dict]:
        if not self.chunks:
            return []
        idx = []
        if self.matrix is not None:
            try:
                qv = (await embed([query]))[0]
                sims = self.matrix @ qv
                idx = [int(i) for i in np.argsort(-sims)[:k] if sims[i] > 0.2]
            except Exception as e:
                log.warning("query embedding failed, using keyword search: %s", e)
        if not idx:
            idx = self.keyword(query, k)
        return [self.chunks[i] for i in idx]

    def inline_text(self) -> str:
        return "\n\n".join(f"## {d['name']}\n{d['content']}" for d in self.docs)

    def titles(self) -> str:
        return ", ".join(d["name"] for d in self.docs[:40])


def _load(tenant_id: str, agent_id: str) -> Index:
    docs = db.query("SELECT id, name, content, chars, chunks FROM knowledge_docs WHERE tenant_id = ? AND "
                    "(agent_id = ? OR agent_id IS NULL) ORDER BY created_at", tenant_id, agent_id)
    chunks = []
    if sum(d.get("chars") or 0 for d in docs) > settings.KB_INLINE_CHARS:
        chunks = db.query("SELECT k.doc_id, k.seq, k.text, k.embedding, d.name FROM knowledge_chunks k "
                          "JOIN knowledge_docs d ON d.id = k.doc_id WHERE k.tenant_id = ? AND "
                          "(k.agent_id = ? OR k.agent_id IS NULL) ORDER BY k.doc_id, k.seq", tenant_id, agent_id)
        have = {c["doc_id"] for c in chunks}
        for d in docs:                                   # documents added before chunking existed
            if d["id"] not in have:
                chunks += [{"doc_id": d["id"], "seq": i, "text": t, "embedding": None, "name": d["name"]}
                           for i, t in enumerate(chunk_text(d["content"] or ""))]
        if any(c.get("embedding") is None for c in chunks):
            for c in chunks:
                c["embedding"] = None                     # mixed index -> keyword search for all
    return Index(docs, chunks)


async def index_for(agent: dict) -> Index:
    return await hot.cached(("kb", agent["id"]), 60, _load, agent["tenant_id"], agent["id"], timeout=6.0)


def format_results(chunks: list[dict]) -> str:
    if not chunks:
        return "No matching information in the knowledge base."
    return "\n\n".join(f"[{c.get('name', 'doc')}]\n{c['text']}" for c in chunks)
