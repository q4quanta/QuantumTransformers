from typing import Literal
import flax.linen as nn
import jax.numpy as jnp

from quantum_transformers.quantum_layer import QuantumLayer, get_circuit

# See:
# - https://nlp.seas.harvard.edu/annotated-transformer/
# - https://github.com/rdisipio/qtransformer/blob/main/qtransformer.py
# - https://github.com/google-research/vision_transformer/blob/main/vit_jax/models_vit.py


class MultiHeadSelfAttention(nn.Module):
    embed_dim: int
    num_heads: int
    dropout: float = 0.0

    @nn.compact
    def __call__(self, x, deterministic, quantum_attn_circuit=None):
        batch_size, seq_len, embed_dim = x.shape
        # x.shape = (batch_size, seq_len, embed_dim)
        assert embed_dim == self.embed_dim, f"Input embedding dimension ({embed_dim}) should match layer embedding dimension ({self.embed_dim})"
        assert embed_dim % self.num_heads == 0, f"Input embedding dimension ({embed_dim}) should be divisible by number of heads ({self.num_heads})"
        head_dim = embed_dim // self.num_heads

        if quantum_attn_circuit is None:
            q, k, v = [
                proj(x).reshape(batch_size, seq_len, self.num_heads, head_dim).swapaxes(1, 2)
                for proj, x in zip([nn.Dense(features=embed_dim),
                                    nn.Dense(features=embed_dim),
                                    nn.Dense(features=embed_dim)], [x, x, x])
            ]
        else:
            q, k, v = [
                proj(x).reshape(batch_size, seq_len, self.num_heads, head_dim).swapaxes(1, 2)
                for proj, x in zip([QuantumLayer(num_qubits=embed_dim, circuit=quantum_attn_circuit),
                                    QuantumLayer(num_qubits=embed_dim, circuit=quantum_attn_circuit),
                                    QuantumLayer(num_qubits=embed_dim, circuit=quantum_attn_circuit)], [x, x, x])
            ]

        # Compute scaled dot-product attention
        attn_logits = (q @ k.swapaxes(-2, -1)) / jnp.sqrt(head_dim)
        # attn_logits.shape = (batch_size, num_heads, seq_len, seq_len)
        attn = nn.softmax(attn_logits, axis=-1)
        # attn.shape = (batch_size, num_heads, seq_len, seq_len)
        attn = nn.Dropout(rate=self.dropout)(attn, deterministic=deterministic)

        # Compute output
        values = attn @ v
        # values.shape = (batch_size, num_heads, seq_len, head_dim)
        values = values.swapaxes(1, 2).reshape(batch_size, seq_len, embed_dim)
        # values.shape = (batch_size, seq_len, embed_dim)
        if quantum_attn_circuit is None:
            x = nn.Dense(features=embed_dim)(values)
        else:
            x = QuantumLayer(num_qubits=embed_dim, circuit=quantum_attn_circuit)(values)
        # x.shape = (batch_size, seq_len, embed_dim)

        return x


class FeedForward(nn.Module):
    hidden_size: int
    mlp_hidden_size: int
    dropout: float = 0.0

    @nn.compact
    def __call__(self, x, deterministic, quantum_mlp_circuit=None):
        x = nn.Dense(features=self.mlp_hidden_size)(x)
        if quantum_mlp_circuit is not None:
            x = QuantumLayer(num_qubits=self.mlp_hidden_size, circuit=quantum_mlp_circuit)(x)
        x = nn.Dropout(rate=self.dropout)(x, deterministic=deterministic)
        x = nn.gelu(x)
        x = nn.Dense(features=self.hidden_size)(x)
        return x


class TransformerBlock(nn.Module):
    hidden_size: int
    num_heads: int
    mlp_hidden_size: int
    dropout: float = 0.0

    @nn.compact
    def __call__(self, x, deterministic, quantum_attn_circuit=None, quantum_mlp_circuit=None):
        attn_output = nn.LayerNorm()(x)
        attn_output = MultiHeadSelfAttention(embed_dim=self.hidden_size, num_heads=self.num_heads, dropout=self.dropout)(
            attn_output, deterministic=deterministic, quantum_attn_circuit=quantum_attn_circuit)
        attn_output = nn.Dropout(rate=self.dropout)(attn_output, deterministic=deterministic)
        x = x + attn_output

        y = nn.LayerNorm()(x)
        y = FeedForward(hidden_size=self.hidden_size, mlp_hidden_size=self.mlp_hidden_size)(
            y, deterministic=deterministic, quantum_mlp_circuit=quantum_mlp_circuit)
        y = nn.Dropout(rate=self.dropout)(y, deterministic=deterministic)

        return x + y


class Transformer(nn.Module):
    num_tokens: int
    max_seq_len: int
    num_classes: int
    hidden_size: int
    num_heads: int
    num_transformer_blocks: int
    mlp_hidden_size: int
    dropout: float = 0.0

    @nn.compact
    def __call__(self, x, train, quantum_attn_circuit=None, quantum_mlp_circuit=None):
        # Token embedding
        x = nn.Embed(num_embeddings=self.num_tokens, features=self.hidden_size)(x)
        # x.shape = (batch_size, seq_len, hidden_size)

        # Positional embedding
        x += nn.Embed(num_embeddings=self.max_seq_len, features=self.hidden_size)(jnp.arange(x.shape[1]))

        # Dropout
        x = nn.Dropout(rate=self.dropout)(x, deterministic=not train)

        # Transformer blocks
        for _ in range(self.num_transformer_blocks):
            x = TransformerBlock(
                hidden_size=self.hidden_size,
                num_heads=self.num_heads,
                mlp_hidden_size=self.mlp_hidden_size,
                dropout=self.dropout,
            )(x, deterministic=not train, quantum_attn_circuit=quantum_attn_circuit, quantum_mlp_circuit=quantum_mlp_circuit)

        # Layer normalization
        x = nn.LayerNorm()(x)

        # Global average pooling
        x = jnp.mean(x, axis=1)
        # x.shape = (batch_size, hidden_size)

        # Classification logits
        x = nn.Dense(self.num_classes)(x)
        # x.shape = (batch_size, num_classes)

        return x


class VisionTransformer(nn.Module):
    num_classes: int
    patch_size: int
    hidden_size: int
    num_heads: int
    num_transformer_blocks: int
    mlp_hidden_size: int
    dropout: float = 0.1
    channels_last: bool = True
    classifier: Literal['token', 'gap'] = 'gap'

    @nn.compact
    def __call__(self, x, train, quantum_attn_circuit=None, quantum_mlp_circuit=None):
        assert x.ndim == 4, "Input must be a 4D tensor"

        if not self.channels_last:
            x = x.transpose((0, 3, 1, 2))
        # x.shape = (batch_size, height, width, num_channels)
        # Note that JAX's Conv expects the input to be in the format (batch_size, height, width, num_channels)

        batch_size, height, width, num_channels = x.shape
        assert height == width, "Input must be square"
        img_size = height
        num_steps = (img_size // self.patch_size) ** 2

        # Splitting an image into patches and linearly projecting these flattened patches can be
        # simplified as a single convolution operation, where both the kernel size and the stride size
        # are set to the patch size.
        x = nn.Conv(
            features=self.hidden_size,
            kernel_size=(self.patch_size, self.patch_size),
            strides=self.patch_size,
            padding="VALID"
        )(x)
        # x.shape = (batch_size, sqrt(num_steps), sqrt(num_steps), hidden_size)
        x = jnp.reshape(x, (batch_size, num_steps, self.hidden_size))
        # x.shape = (batch_size, num_steps, hidden_size)

        if self.classifier == 'token':
            # CLS token
            cls_token = self.param('cls', nn.initializers.zeros, (1, 1, self.hidden_size))
            cls_token = jnp.tile(cls_token, (batch_size, 1, 1))
            x = jnp.concatenate([cls_token, x], axis=1)
            num_steps += 1
            # x.shape = (batch_size, num_steps, hidden_size)

        # Positional embedding
        x += self.param('pos_embedding', nn.initializers.normal(stddev=0.02), (1, num_steps, self.hidden_size))

        # Dropout
        x = nn.Dropout(rate=self.dropout)(x, deterministic=not train)
        # x.shape = (batch_size, num_steps, hidden_size)

        # Transformer blocks
        for _ in range(self.num_transformer_blocks):
            x = TransformerBlock(
                hidden_size=self.hidden_size,
                num_heads=self.num_heads,
                mlp_hidden_size=self.mlp_hidden_size,
                dropout=self.dropout,
            )(x, deterministic=not train, quantum_attn_circuit=quantum_attn_circuit, quantum_mlp_circuit=quantum_mlp_circuit)

        # Layer normalization
        x = nn.LayerNorm()(x)
        # x.shape = (batch_size, num_steps, hidden_size)

        if self.classifier == 'token':
            # Get the classifcation token
            x = x[:, 0]
        elif self.classifier == 'gap':
            # Global average pooling
            x = jnp.mean(x, axis=1)
        # x.shape = (batch_size, hidden_size)

        # Classification logits
        x = nn.Dense(features=self.num_classes)(x)
        # x.shape = (batch_size, num_classes)

        return x
