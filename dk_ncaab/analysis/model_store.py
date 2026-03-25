"""
Model persistence — save and load trained models + metadata.

Stores models as joblib files alongside a JSON manifest containing:
  - Training date, dataset size, feature list
  - CV metrics (R², RMSE, AUC, etc.)
  - Model class name

Directory: artifacts/models/<model_name>_<timestamp>.joblib
Manifest:  artifacts/models/<model_name>_<timestamp>.json
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib

log = logging.getLogger(__name__)

_MODEL_DIR = Path("artifacts/models")


def save_model(
    model: Any,
    name: str,
    features: list[str],
    metrics: dict,
    scaler: Any | None = None,
    tag: str | None = None,
) -> Path:
    """
    Save a trained model + optional scaler + metadata to disk.

    Args:
        model:    The fitted model object (sklearn, lightgbm, etc.).
        name:     Human-readable name, e.g. "ridge_close", "lgbm_close".
        features: Ordered list of feature column names used during training.
        metrics:  Dict of evaluation metrics (R², RMSE, AUC, etc.).
        scaler:   Optional fitted StandardScaler (for Ridge, LogReg).
        tag:      Optional timestamp tag; defaults to now.

    Returns: Path to the saved .joblib file.
    """
    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    ts = tag or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    stem = f"{name}_{ts}"

    # Save model (+ scaler bundled together)
    bundle = {"model": model, "scaler": scaler, "features": features}
    model_path = _MODEL_DIR / f"{stem}.joblib"
    joblib.dump(bundle, model_path)
    log.info("Model saved: %s", model_path)

    # Save manifest
    manifest = {
        "name": name,
        "saved_at": ts,
        "features": features,
        "metrics": metrics,
        "has_scaler": scaler is not None,
        "model_class": type(model).__name__,
    }
    manifest_path = _MODEL_DIR / f"{stem}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))

    return model_path


def load_model(path: str | Path) -> dict:
    """
    Load a saved model bundle.

    Returns dict with keys: 'model', 'scaler', 'features'.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Model not found: {path}")
    bundle = joblib.load(path)
    log.info("Model loaded: %s", path)
    return bundle


def get_latest_model(name: str) -> Path | None:
    """
    Find the most recently saved model matching the given name prefix.

    E.g. name="lgbm_close" matches "lgbm_close_20260215T120000.joblib".
    """
    if not _MODEL_DIR.exists():
        return None

    matches = sorted(
        _MODEL_DIR.glob(f"{name}_*.joblib"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def list_models() -> list[dict]:
    """List all saved models with their manifest info."""
    if not _MODEL_DIR.exists():
        return []

    models = []
    for jpath in sorted(_MODEL_DIR.glob("*.json")):
        try:
            info = json.loads(jpath.read_text())
            joblib_path = jpath.with_suffix(".joblib")
            info["path"] = str(joblib_path)
            info["exists"] = joblib_path.exists()
            models.append(info)
        except Exception:
            pass
    return models
