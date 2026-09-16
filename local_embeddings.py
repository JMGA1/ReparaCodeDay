"""Embeddings opcionales: solo modelos previamente descargados al disco local."""

import logging
import os
import threading
from pathlib import Path

_model = None
_failed = False
_lock = threading.Lock()


def similarities(description, candidates):
    global _model, _failed
    directory = os.environ.get("LOCAL_EMBED_MODEL", "")
    if not directory or not candidates or _failed:
        return None
    with _lock:
        try:
            if _model is None:
                if not Path(directory).is_dir():
                    raise ValueError("LOCAL_EMBED_MODEL debe ser una carpeta local")
                from sentence_transformers import SentenceTransformer

                _model = SentenceTransformer(
                    directory,
                    device="cpu",
                    local_files_only=True,
                    trust_remote_code=False,
                )
            texts = [description] + [
                str(c.get("description") or c.get("title") or "") for c in candidates
            ]
            vectors = _model.encode(
                texts, normalize_embeddings=True, show_progress_bar=False
            )
            return [max(0.0, min(1.0, float(vectors[0] @ v))) for v in vectors[1:]]
        except Exception:
            _failed = True
            logging.getLogger("repara").exception(
                "Embeddings locales no disponibles; se conserva comparación léxica"
            )
            return None
