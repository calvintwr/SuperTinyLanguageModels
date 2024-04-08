"""
A simplistic model builder for building models 
from scratch or from checkpoints
"""

from models.baseline import BaseGPT


MODEL_CLASSES = {
    "baseline": BaseGPT,
}


def build_model(cfg=None, model_checkpoint=None):
    """
    Build model from scratch or load model from checkpoint
    Args:
        cfg: model configuration
        model_checkpoint: model weight dict
    Returns:
        model: model instance
    """
    # check if model is loaded
    if model_checkpoint is not None:
        # load model with correct architecture
        # assume access to original config
        model = MODEL_CLASSES[cfg["model"]](
            cfg=cfg
        )

        # load model weights
        model.load_state_dict(model_checkpoint["model"])
        model.eval()

        return model

    else:
        # build model from scratch
        model = MODEL_CLASSES[cfg["model"]](cfg=cfg)

        return model
