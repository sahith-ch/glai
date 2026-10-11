"""Semantic skill search for the hiring site: embeddings shortlist, a cross-encoder confirms.

Documents are a candidate's acquired skills AND the tasks that earned them (title + description),
so very particular queries ("fill missing values with the column mean") match the exact work done.

    1. shortlist   bge-small embeddings (local, CPU): the TOP_K closest docs above SHORTLIST_SIM
    2. confirm     jina-reranker-v1-tiny reads query + doc together; accept above ACCEPT
                   (plain embedding similarity can't tell "pytorch neural networks" from
                   "scikit-learn linear regression" - both score ~0.7; the reranker can)

Doc embeddings are cached on disk by text hash, so only new or changed tasks are embedded.
If the models can't load, `get()` returns None and the site falls back to keyword search.
"""

import hashlib
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .schemas import SkillNode, TaskEvidence, UserProfile

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
RERANK_MODEL = "jinaai/jina-reranker-v1-tiny-en"
CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"
VECTORS_FILE = CACHE_DIR / "skill_vectors.npz"

# Acceptance needs all three (calibrated on hand-labelled query/skill pairs - tune here):
MIN_COS = 0.65  # embedding similarity: drops vaguely related docs (0.62 let "css" reach a Plotly dashboard task)
ACCEPT_FLOOR = -0.5  # reranker score: drops weak matches (-0.72 let "deep learning" reach scikit-learn regression)
RELATIVE_MARGIN = 0.9  # within this of the query's best match across all candidates: when someone has the
#                        exact skill, loosely related ones (generic "pandas basics" for "pandas groupby") drop out
TOP_K = 5  # docs per candidate per term sent to the reranker

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


@dataclass
class Doc:
    node: SkillNode
    task: TaskEvidence | None
    text: str


@dataclass
class Hit:
    doc: Doc
    score: float  # reranker score
    cos: float  # embedding similarity


def docs_of(profile: UserProfile) -> list[Doc]:
    """Acquired skills plus the tasks behind them. A task is listed under its most specific skill only."""
    nodes = [n for n in profile.skill_graph.nodes if n.status in ("completed", "verified")]
    in_subskill = {(t.roadmap, t.id) for n in nodes if n.kind == "subskill" for t in n.tasks}
    docs = []
    for n in nodes:
        docs.append(Doc(n, None, n.name if not n.parent else f"{n.name} ({n.parent})"))
        for t in n.tasks:
            if n.kind == "skill" and (t.roadmap, t.id) in in_subskill:
                continue
            docs.append(Doc(n, t, f"{t.title}. {t.description}"))
    return docs


class Engine:
    def __init__(self):
        from fastembed import TextEmbedding
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        cache = str(CACHE_DIR / "fastembed")
        self.embedder = TextEmbedding(EMBED_MODEL, cache_dir=cache)
        self.reranker = TextCrossEncoder(RERANK_MODEL, cache_dir=cache)
        self.lock = threading.Lock()
        self.vectors: dict[str, np.ndarray] = {}
        if VECTORS_FILE.exists():
            with np.load(VECTORS_FILE) as f:
                self.vectors = {k: f[k] for k in f.files}

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha1(f"{EMBED_MODEL}|{text}".encode("utf-8")).hexdigest()

    def embed_docs(self, texts: list[str]) -> np.ndarray:
        with self.lock:
            missing = list(dict.fromkeys(t for t in texts if self._key(t) not in self.vectors))
            if missing:
                for t, v in zip(missing, self.embedder.passage_embed(missing)):
                    self.vectors[self._key(t)] = (v / np.linalg.norm(v)).astype(np.float32)
                CACHE_DIR.mkdir(exist_ok=True)
                np.savez(VECTORS_FILE, **self.vectors)
            return np.stack([self.vectors[self._key(t)] for t in texts]) if texts else np.zeros((0, 384), np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        v = next(iter(self.embedder.query_embed([text])))
        return (v / np.linalg.norm(v)).astype(np.float32)

    def candidates(self, term: str, qvec: np.ndarray, docs: list[Doc], vecs: np.ndarray) -> list[Hit]:
        """Scored shortlist for one learner (not yet accepted - see `accept`)."""
        if not docs:
            return []
        sims = vecs @ qvec
        short = [i for i in np.argsort(-sims)[:TOP_K] if sims[i] >= MIN_COS]
        if not short:
            return []
        scores = self.reranker.rerank(term, [docs[i].text for i in short])
        return [Hit(docs[i], float(s), float(sims[i])) for i, s in zip(short, scores)]


def accept(per_learner: dict[str, list[Hit]]) -> dict[str, list[Hit]]:
    """Keep hits above the floor AND close to the best match for this term across all learners."""
    scores = [h.score for hits in per_learner.values() for h in hits]
    if not scores:
        return {}
    bar = max(ACCEPT_FLOOR, max(scores) - RELATIVE_MARGIN)
    kept = {k: sorted((h for h in hits if h.score >= bar), key=lambda h: -h.score) for k, hits in per_learner.items()}
    return {k: v for k, v in kept.items() if v}


_engine: Engine | None = None
_failed = False
_init_lock = threading.Lock()


def get() -> Engine | None:
    """The shared engine, loaded on first use; None if the models aren't available (keyword search then).
    GLAI_SEMANTIC_SEARCH=off skips them entirely: they take ~370 MB of memory, more than a 512 MB host can spare."""
    global _engine, _failed
    if os.environ.get("GLAI_SEMANTIC_SEARCH", "on").strip().lower() == "off":
        return None
    with _init_lock:
        if _engine is None and not _failed:
            try:
                _engine = Engine()
            except Exception as e:  # no network for the first download, missing package, ...
                print(f"  (semantic search unavailable, using keyword search: {e})")
                _failed = True
        return _engine
