"""
ReID feature extractor using OSNet.
Crops detected persons and extracts 512-dim L2-normalized embeddings.
"""

import numpy as np
import cv2
import torch
import torch.nn.functional as F
from torchvision import transforms

# Lazy-loaded model
_model = None
_device = None

_preprocess = transforms.Compose([
    transforms.ToPILImage(),
    transforms.Resize((256, 128)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])


def _load_model():
    global _model, _device
    if _model is not None:
        return _model, _device

    _device = torch.device(
        "mps" if torch.backends.mps.is_available()
        else "cuda" if torch.cuda.is_available()
        else "cpu"
    )

    # Use torchreid's OSNet (small, fast, good accuracy)
    from torchreid.reid.utils import FeatureExtractor
    _model = FeatureExtractor(
        model_name="osnet_x0_25",
        model_path="",  # auto-downloads pretrained weights
        device=str(_device),
    )
    print(f"ReID model loaded on {_device}")
    return _model, _device


def extract_embedding(frame: np.ndarray, bbox_xywhn: list) -> list:
    """
    Crop person from frame using normalized bbox, extract 512-dim embedding.

    Args:
        frame: BGR image (H, W, 3)
        bbox_xywhn: [cx, cy, w, h] normalized to [0, 1]

    Returns:
        512-dim L2-normalized embedding as list of floats
    """
    h, w = frame.shape[:2]
    cx, cy, bw, bh = bbox_xywhn

    # Convert normalized center coords to pixel coords
    x1 = int((cx - bw / 2) * w)
    y1 = int((cy - bh / 2) * h)
    x2 = int((cx + bw / 2) * w)
    y2 = int((cy + bh / 2) * h)

    # Clamp to image bounds
    x1 = max(0, x1)
    y1 = max(0, y1)
    x2 = min(w, x2)
    y2 = min(h, y2)

    if x2 - x1 < 10 or y2 - y1 < 10:
        return []

    crop = frame[y1:y2, x1:x2]
    crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)

    extractor, device = _load_model()
    features = extractor(crop_rgb)  # returns tensor (1, 512)

    # L2 normalize
    embedding = F.normalize(features, p=2, dim=1)
    return embedding[0].cpu().tolist()
