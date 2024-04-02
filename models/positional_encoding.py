"""
A collection of positional encoding modules.
"""

import torch
import torch.nn as nn


class LearnedPosEncoding(nn.Module):
    """
    Basic learned positional encoding
    """
    def __init__(self, hidden_dim, context_window):
        super().__init__()
        self.pe = nn.Embedding(
            num_embeddings=context_window, 
            embedding_dim=hidden_dim
        )

    def forward(self, x):
        """
        Forward pass
        """
        print(x.device)
        print(self.pe.weight.device)
        print(self.pe(torch.arange(x.size(1)-5, device=x.device)))
        x = x.to('cpu')
        self.pe.weight = self.pe.weight.to('cpu')
        if len(x.shape) >= 2:
            return self.pe(torch.arange(x.size(1), device=x.device)).unsqueeze(0)#.to(x.device)
        else:
            return self.pe(torch.arange(x.size(1), device=x.device))