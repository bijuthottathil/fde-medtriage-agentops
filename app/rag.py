"""Clinic protocol retrieval (RAG) backed by Chroma.

The protocol file is indexed at startup. Chroma runs embedded (no separate server):
in-memory by default, on disk when CHROMA_PATH is set. Embeddings use Chroma's default
local ONNX model (all-MiniLM-L6-v2), baked into the image, so no network or LLM is needed.
"""
import os
import threading
from functools import cached_property

import chromadb
import yaml
from chromadb.config import Settings
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

from . import config

COLLECTION = "triage_protocols"
_lock = threading.Lock()
_collection = None


class _PinnedThreadsEmbedder(ONNXMiniLM_L6_V2):
    """Default embedder with ONNX threads pinned. Unpinned, onnxruntime sizes its thread pool
    to the NODE's cores (14 here), and under a 1-CPU pod limit the CFS throttling took
    retrieval from ~40ms to ~1s (p95 7s with 4 concurrent requests)."""

    @cached_property
    def model(self):
        so = self.ort.SessionOptions()
        so.log_severity_level = 3
        so.graph_optimization_level = self.ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        so.intra_op_num_threads = config.RAG_THREADS
        so.inter_op_num_threads = 1
        return self.ort.InferenceSession(
            os.path.join(self.DOWNLOAD_PATH, self.EXTRACTED_FOLDER_NAME, "model.onnx"),
            providers=["CPUExecutionProvider"], sess_options=so)


def load_protocols() -> dict:
    return yaml.safe_load(config.PROTOCOLS_PATH.read_text())


def _get_collection():
    global _collection
    with _lock:
        if _collection is None:
            settings = Settings(anonymized_telemetry=False)
            client = (chromadb.PersistentClient(path=config.CHROMA_PATH, settings=settings)
                      if config.CHROMA_PATH else chromadb.EphemeralClient(settings=settings))
            coll = client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"},
                                                   embedding_function=_PinnedThreadsEmbedder())
            kb = load_protocols()
            # upsert keeps a persistent index in sync with the versioned protocol file
            coll.upsert(
                ids=[p["id"] for p in kb["protocols"]],
                documents=[f'{p["title"]}. {p["text"]}' for p in kb["protocols"]],
                metadatas=[{"title": p["title"], "label": p["label"], "kb_version": str(kb["version"])}
                           for p in kb["protocols"]],
            )
            _collection = coll
        return _collection


def warm() -> None:
    """Build the index and load the model before the pod reports ready (no cold first request)."""
    retrieve_protocols("warmup", k=1)


def retrieve_protocols(query: str, k: int | None = None) -> list[dict]:
    res = _get_collection().query(query_texts=[query], n_results=k or config.RAG_TOP_K)
    return [{"id": i, "title": m["title"], "label": m["label"], "text": d, "distance": round(dist, 3)}
            for i, m, d, dist in zip(res["ids"][0], res["metadatas"][0], res["documents"][0],
                                     res["distances"][0])]
