"""
Llama-3 like transformer.
"""

import torch 
import torch.nn as nn 

from models.components.positional_encoding import (
    LearnedPosEncoding
)

from models.components.layers import (
    JetFFNMoEBlock
)

class ModernTransformerFFNMoE(nn.Module):
    def __init__(self, cfg):
        """
        Initialize a Llama-3 style transformer model with
        JetMoE style MoE FFN (standard Llama-3 attention).
        """
        super().__init__()
        
        self.core_model_cfg = cfg["core_model"]
        self.context_window = cfg["model_shell"]["context_window"]

        # build positional encoding
        self.pos_encoder = LearnedPosEncoding(
            hidden_dim=cfg["core_model"]["hidden_dim"], 
            context_window=self.context_window
        )

        # build the transformer
        self.transformer = nn.ModuleDict(
            dict(
                drop=nn.Dropout(self.core_model_cfg["dropout"]),
                h=nn.ModuleList(
                    [JetFFNMoEBlock(
                        hidden_dim=self.core_model_cfg["hidden_dim"], 
                        ffn_dim=self.core_model_cfg["ffn_dim"], 
                        num_heads=self.core_model_cfg["num_heads"], 
                        dropout=self.core_model_cfg["dropout"],
                        context_window=self.context_window, 
                        num_experts=self.core_model_cfg["num_experts"],
                        top_k=self.core_model_cfg["top_k"]
                    ) for _ in range(self.core_model_cfg["depth"])]
                )
            )
        )

    def forward(self, x):
        """
        Pass an input through the model
        """
        full_aux_loss = 0
        # positional encoding
        x = x + self.pos_encoder(x)

        x = self.transformer.drop(x)
        for block in self.transformer.h:
            x, aux_loss = block(x)
            full_aux_loss += aux_loss

        return x, full_aux_loss