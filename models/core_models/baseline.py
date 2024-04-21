"""
GPT-2 like transformer.
"""

import torch 
import torch.nn as nn 

from models.components.positional_encoding import (
    LearnedPosEncoding
)

from models.components.layers import (
    BaseTransformerBlock
)

class StandardTransformer(nn.Module):
    def __init__(self, model_cfg):
        """
        Initialize the standard transformer model 
        similar to gpt-2
        """
        super().__init__()
        
        self.core_model_cfg = model_cfg["core_model"]

        # build positional encoding
        self.pos_encoder = LearnedPosEncoding(
            hidden_dim=model_cfg["core_model"]["hidden_dim"], 
            context_window=model_cfg["model_shell"]["context_window"]
        )

        # build the transformer
        self.transformer = nn.ModuleDict(
            dict(
                drop=nn.Dropout(self.core_model_cfg["dropout"]),
                h=nn.ModuleList(
                    [BaseTransformerBlock(
                        hidden_dim=self.core_model_cfg["hidden_dim"], 
                        ffn_dim=self.core_model_cfg["ffn_dim"], 
                        ffn_activation=self.core_model_cfg["ffn_activation"],
                        bias=self.core_model_cfg["bias"], 
                        num_heads=self.core_model_cfg["num_heads"], 
                        dropout=self.core_model_cfg["dropout"],
                    ) for _ in range(self.core_model_cfg["depth"])]
                )
            )
        )

    def forward(self, x):
        """
        Pass an input through the model
        """
        # positional encoding
        x = x + self.pos_encoder(x)

        x = self.transformer.drop(x)
        for block in self.transformer.h:
            x = block(x)

        return x