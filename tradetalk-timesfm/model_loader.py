"""Placeholder for Hugging Face ``TimesFM_2p5_200M_torch`` load + compile."""

# Production image installs torch + timesfm and implements load_model().

MODEL_ID = "google/timesfm-2.5-200m-pytorch"


def load_model_stub() -> str:
    return MODEL_ID
