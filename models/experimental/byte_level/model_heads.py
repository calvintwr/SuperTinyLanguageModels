"""
A collection of different model heads.
"""

import torch

from models.components.positional_encoding import LearnedPosEncoding
from models.experimental.byte_level.layers import ByteLevelTransformerBlock


class ByteLevelDecoder(torch.nn.Module):
    """
    Use multiple learned heads to decode into by hidden size,
    pre-append to the byte embeddings of the answers and
    autoregressively decode the next token, applying the
    LM (byte level) head only to the actual tokens, not
    the latent ecoded ones.
    """

    def __init__(self, model_cfg):
        super().__init__()
        self.hidden_dim = model_cfg["hidden_dim"]
        self.embedding_dim = 128
        self.byte_vocab_size = model_cfg["vocab_size"]
        self.byte_context_window = 16

        self.projection = torch.nn.Linear(
            in_features=self.hidden_dim, # 512
            out_features=self.byte_context_window * self.embedding_dim,
            bias=False,
        )



        # build transformer block
        self.transformer = torch.nn.ModuleList(
            [
                ByteLevelTransformerBlock(
                    input_dim=self.embedding_dim,
                    output_dim=self.embedding_dim,
                    ffn_dim=self.embedding_dim * 4,
                    context_window=self.byte_context_window,
                    use_rope=True,
                ),
                ByteLevelTransformerBlock(
                    input_dim=self.embedding_dim,
                    output_dim=self.embedding_dim,
                    ffn_dim=self.embedding_dim * 4,
                    context_window=self.byte_context_window,
                    use_rope=True,
                ),
                ByteLevelTransformerBlock(
                    input_dim=self.embedding_dim,
                    output_dim=self.embedding_dim,
                    ffn_dim=self.embedding_dim * 4,
                    context_window=self.byte_context_window,
                    use_rope=True,
                ),
                ByteLevelTransformerBlock(
                    input_dim=self.embedding_dim,
                    output_dim=self.embedding_dim,
                    ffn_dim=self.embedding_dim * 4,
                    context_window=self.byte_context_window,
                    use_rope=True,
                ),
                # """ByteLevelTransformerBlock(
                #     input_dim=self.embedding_dim,
                #     output_dim=self.embedding_dim,
                #     ffn_dim=self.embedding_dim * 4,
                #     context_window=self.byte_context_window,
                #     use_rope=True,
                # ),
                # ByteLevelTransformerBlock(
                #     input_dim=self.embedding_dim,
                #     output_dim=self.embedding_dim,
                #     ffn_dim=self.embedding_dim * 4,
                #     context_window=self.byte_context_window,
                #     use_rope=True,
                # ),
                # ByteLevelTransformerBlock(
                #     input_dim=self.embedding_dim,
                #     output_dim=self.embedding_dim,
                #     ffn_dim=self.embedding_dim * 4,
                #     context_window=self.byte_context_window,
                #     use_rope=True,
                # ),"""
            ]
        )

        self.lm_head = torch.nn.Linear(
            in_features=self.embedding_dim, # 128
            out_features=self.byte_vocab_size, # 259 (256 bytes + 3 special)
            bias=False,
        )

    def forward(self, x):
        """
        Bidirectionally decode all tokens at once
        """

        # project the latent embeddings
        x = self.projection(x)
        x = x.view(x.size(0), x.size(1), self.byte_context_window, self.embedding_dim)

        # pass through model and deocde
        B, S, _, _ = x.size()
        x = x.view(B * S, self.byte_context_window, self.embedding_dim)

        # positional encoding
        #x = x + self.pos_encoder(x)

        # pass through transformer
        for block in self.transformer:
            x = block(x)

        # pass final self.byte_context_window byte tokens through lm head
        x = self.lm_head(x)

        # reshape and return
        x = x.view(B, S, self.byte_context_window, self.byte_vocab_size)

        return x

    def inference(self, x):
        """
        inference
        """
        return self.forward(x)[0][:, -1, :, :]
