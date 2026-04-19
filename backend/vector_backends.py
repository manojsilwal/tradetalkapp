import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Chroma's built-in HuggingFaceEmbeddingFunction still points at deprecated
# https://api-inference.huggingface.co — use InferenceClient (router) instead.
_DEFAULT_HF_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class HfInferenceRouterEmbeddingFunction:
    """Embedding function for Chroma using huggingface_hub InferenceClient (router.huggingface.co)."""

    def __init__(self, api_key: str, model_name: str = _DEFAULT_HF_EMBED_MODEL):
        from huggingface_hub import InferenceClient

        self._client = InferenceClient(model=model_name, token=api_key)
        self._model_name = model_name

    def __call__(self, input: List[str]) -> List[List[float]]:
        out: List[List[float]] = []
        for text in input:
            arr = self._client.feature_extraction(text)
            if hasattr(arr, "tolist"):
                vec = arr.tolist()
            else:
                vec = list(arr)
            # Normalize nested [[...]] from some servers to [...]
            while vec and isinstance(vec[0], list):
                vec = vec[0]
            out.append([float(x) for x in vec])
        return out


class VectorBackendBase:
    def ensure_collection(self, name: str) -> None:
        raise NotImplementedError

    def add(self, collection: str, documents: List[str], metadatas: List[dict], ids: List[str], embeddings: Optional[List[List[float]]] = None) -> None:
        raise NotImplementedError

    def query(
        self,
        collection: str,
        query_text: str,
        n_results: int = 3,
        where: Optional[dict] = None,
    ) -> Dict[str, List[Any]]:
        raise NotImplementedError

    def get(self, collection: str) -> Dict[str, List[Any]]:
        raise NotImplementedError

    def count(self, collection: str) -> int:
        raise NotImplementedError

    def update_metadata(self, collection: str, doc_id: str, metadata_updates: dict) -> None:
        raise NotImplementedError


class ChromaVectorBackend(VectorBackendBase):
    def __init__(self, chroma_path: Optional[str] = None):
        # Disable Chroma product telemetry (avoids noisy logs / posthog "capture()" bugs on serverless).
        import os

        os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
        import chromadb
        from chromadb.config import Settings

        if chroma_path:
            self._client = chromadb.PersistentClient(
                path=chroma_path,
                settings=Settings(anonymized_telemetry=False),
            )
        else:
            self._client = chromadb.EphemeralClient(
                settings=Settings(anonymized_telemetry=False),
            )
            
        self._cols: Dict[str, Any] = {}

        # Use remote HF Embedding Function on Render instead of heavy local ONNX model
        self._embedding_function = None
        render = os.environ.get("RENDER", "").strip().lower()
        if render in ("true", "1", "yes"):
            hf_token = os.environ.get("HF_TOKEN")
            if hf_token:
                try:
                    model_name = os.environ.get("HF_EMBEDDING_MODEL", _DEFAULT_HF_EMBED_MODEL).strip() or _DEFAULT_HF_EMBED_MODEL
                    self._embedding_function = HfInferenceRouterEmbeddingFunction(
                        api_key=hf_token,
                        model_name=model_name,
                    )
                    logger.info(
                        "[ChromaVectorBackend] Configured HfInferenceRouterEmbeddingFunction (%s) via InferenceClient.",
                        model_name,
                    )
                except Exception as e:
                    logger.warning(f"[ChromaVectorBackend] Failed to init remote embedding function: {e}")

    def ensure_collection(self, name: str) -> None:
        if self._embedding_function:
            self._cols[name] = self._client.get_or_create_collection(name=name, embedding_function=self._embedding_function)
        else:
            self._cols[name] = self._client.get_or_create_collection(name=name)

    def _col(self, name: str):
        return self._cols.get(name)

    def add(self, collection: str, documents: List[str], metadatas: List[dict], ids: List[str], embeddings: Optional[List[List[float]]] = None) -> None:
        col = self._col(collection)
        if not col:
            return
        if embeddings:
            col.add(documents=documents, metadatas=metadatas, ids=ids, embeddings=embeddings)
        else:
            col.add(documents=documents, metadatas=metadatas, ids=ids)

    def query(
        self,
        collection: str,
        query_text: str,
        n_results: int = 3,
        where: Optional[dict] = None,
    ) -> Dict[str, List[Any]]:
        col = self._col(collection)
        if not col:
            return {"documents": [], "metadatas": [], "ids": [], "distances": []}
        normalized_where = where
        if isinstance(where, dict) and len(where) > 1:
            # Chroma requires one top-level operator for multi-condition filters.
            normalized_where = {"$and": [{k: v} for k, v in where.items()]}
        params = {"query_texts": [query_text], "n_results": n_results}
        if normalized_where:
            params["where"] = normalized_where
        result = col.query(**params)
        return {
            "documents": result.get("documents", [[]])[0],
            "metadatas": result.get("metadatas", [[]])[0],
            "ids": result.get("ids", [[]])[0],
            "distances": result.get("distances", [[]])[0],
        }

    def get(self, collection: str) -> Dict[str, List[Any]]:
        col = self._col(collection)
        if not col:
            return {"documents": [], "metadatas": [], "ids": []}
        result = col.get(include=["documents", "metadatas"])
        return {
            "documents": result.get("documents", []),
            "metadatas": result.get("metadatas", []),
            "ids": result.get("ids", []),
        }

    def count(self, collection: str) -> int:
        col = self._col(collection)
        return col.count() if col else 0

    def update_metadata(self, collection: str, doc_id: str, metadata_updates: dict) -> None:
        col = self._col(collection)
        if not col:
            return
        existing = col.get(ids=[doc_id], include=["metadatas"])
        metas = existing.get("metadatas", [])
        if not metas:
            return
        merged = {**(metas[0] or {}), **metadata_updates}
        col.update(ids=[doc_id], metadatas=[merged])


class SupabaseVectorBackend(VectorBackendBase):
    def __init__(self, url: str, key: str):
        from supabase import create_client

        self._client = create_client(url, key)
        self._embedding_pool = None
        self._embedding_model = os.environ.get("OPENROUTER_EMBEDDING_MODEL", "").strip()

        base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        headers: Dict[str, str] = {}
        ref = os.environ.get("OPENROUTER_HTTP_REFERER", "").strip()
        if ref:
            headers["HTTP-Referer"] = ref
        # Match llm_client default so the shared OpenRouter pool gets identical headers regardless of init order.
        xt = os.environ.get("OPENROUTER_X_TITLE", "TradeTalk App").strip()
        if xt:
            headers["X-Title"] = xt

        if self._embedding_model:
            from .openrouter_pool import collect_openrouter_api_keys, get_or_create_openrouter_pool

            if collect_openrouter_api_keys():
                try:
                    self._embedding_pool = get_or_create_openrouter_pool(base_url, headers)
                except Exception as e:
                    logger.warning(f"[SupabaseVectorBackend] Embedding pool init failed: {e}")

        # Fail fast if SQL bootstrap has not been applied yet.
        try:
            self._client.table("vector_memory").select("id").limit(1).execute()
        except Exception as e:
            hint = (
                "Supabase vector schema missing (public.vector_memory not found — often HTTP 404 / "
                "PGRST205 from PostgREST). One-time fix: open Supabase Dashboard → SQL Editor, "
                "run the full contents of backend/supabase_pgvector_bootstrap.sql from this repo, "
                "then retry. Required before VECTOR_BACKEND=supabase or batch ETL upserts."
            )
            raise RuntimeError(hint) from e

    def ensure_collection(self, name: str) -> None:
        # No-op: collection is represented by a table field.
        return

    def _embed(self, text: str) -> Optional[List[float]]:
        if not self._embedding_pool or not self._embedding_model:
            return None
        try:
            from .openrouter_pool import should_try_other_openrouter_keys_on_429, sync_failover_execute

            clients = self._embedding_pool.sync_clients_for_request(
                should_try_other_openrouter_keys_on_429()
            )

            def _embed_one(client):
                return client.embeddings.create(
                    model=self._embedding_model,
                    input=text,
                )

            emb, err = sync_failover_execute(clients, _embed_one)
            if emb is not None:
                return emb.data[0].embedding
            if err is not None:
                logger.warning(f"[SupabaseVectorBackend] Embedding generation failed: {err}")
            return None
        except Exception as e:
            logger.warning(f"[SupabaseVectorBackend] Embedding generation failed: {e}")
            return None

    def add(self, collection: str, documents: List[str], metadatas: List[dict], ids: List[str], embeddings: Optional[List[List[float]]] = None) -> None:
        rows = []
        for i, (doc, meta, row_id) in enumerate(zip(documents, metadatas, ids)):
            row = {
                "id": row_id,
                "collection": collection,
                "document": doc,
                "metadata": meta,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            if embeddings and i < len(embeddings) and embeddings[i] is not None:
                row["embedding"] = embeddings[i]
            else:
                embedding = self._embed(doc)
                if embedding is not None:
                    row["embedding"] = embedding
            rows.append(row)
        self._client.table("vector_memory").upsert(rows).execute()

    def query(
        self,
        collection: str,
        query_text: str,
        n_results: int = 3,
        where: Optional[dict] = None,
    ) -> Dict[str, List[Any]]:
        metadata_filter = where or {}

        # Prefer pgvector RPC if embeddings are available and SQL bootstrap is installed.
        query_embedding = self._embed(query_text)
        if query_embedding is not None:
            try:
                response = self._client.rpc(
                    "match_vector_memory",
                    {
                        "query_embedding": query_embedding,
                        "match_count": n_results,
                        "in_collection": collection,
                        "metadata_filter": metadata_filter,
                    },
                ).execute()
                rows = response.data or []
                return {
                    "documents": [r.get("document", "") for r in rows],
                    "metadatas": [r.get("metadata", {}) for r in rows],
                    "ids": [r.get("id", "") for r in rows],
                    "distances": [r.get("distance", 0.0) for r in rows],
                }
            except Exception:
                # Fall back to lexical path if RPC/function is not set up.
                pass

        query = self._client.table("vector_memory").select("id,document,metadata").eq("collection", collection)
        for k, v in metadata_filter.items():
            query = query.contains("metadata", {k: v})
        response = query.order("created_at", desc=True).limit(max(n_results * 3, 10)).execute()
        rows = response.data or []

        tokens = [t for t in query_text.lower().split() if t]
        scored = []
        for row in rows:
            doc = (row.get("document") or "").lower()
            score = sum(1 for t in tokens if t in doc)
            scored.append((score, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = [row for _, row in scored[:n_results]]
        return {
            "documents": [r.get("document", "") for r in top],
            "metadatas": [r.get("metadata", {}) for r in top],
            "ids": [r.get("id", "") for r in top],
            "distances": [0.0 for _ in top],
        }

    def get(self, collection: str) -> Dict[str, List[Any]]:
        response = (
            self._client.table("vector_memory")
            .select("id,document,metadata")
            .eq("collection", collection)
            .limit(5000)
            .execute()
        )
        rows = response.data or []
        return {
            "documents": [r.get("document", "") for r in rows],
            "metadatas": [r.get("metadata", {}) for r in rows],
            "ids": [r.get("id", "") for r in rows],
        }

    def count(self, collection: str) -> int:
        response = (
            self._client.table("vector_memory")
            .select("id", count="exact")
            .eq("collection", collection)
            .limit(1)
            .execute()
        )
        return int(response.count or 0)

    def update_metadata(self, collection: str, doc_id: str, metadata_updates: dict) -> None:
        response = (
            self._client.table("vector_memory")
            .select("metadata")
            .eq("id", doc_id)
            .eq("collection", collection)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return
        merged = {**(rows[0].get("metadata") or {}), **metadata_updates}
        self._client.table("vector_memory").update({"metadata": merged}).eq("id", doc_id).execute()
