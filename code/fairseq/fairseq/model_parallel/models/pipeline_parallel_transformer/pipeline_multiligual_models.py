# Copyright (c) Facebook, Inc. and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

from collections import OrderedDict

from fairseq import utils
from fairseq.model_parallel.models.pipeline_parallel_transformer import PipelineParallelTransformerModel
from fairseq.models import (
    FairseqMultiModel,
    register_model,
    register_model_architecture,
)
from fairseq.models.transformer import (
    Embedding,
    TransformerDecoder,
    TransformerEncoder,
    TransformerModel,
    base_architecture,
)
from fairseq.utils import safe_hasattr

from fairseq.models.multilingual_transformer import base_multilingual_architecture

from fairseq.models.transformer_from_m2m import TransformerDecoderFromPretrainedM2M, \
    TransformerEncoderFromPretrainedM2M

from fairseq.models.transformer_from_m2m import set_pretrain_emb

from fairseq.models.fairseq_model import get_lg_family_info


@register_model("pipeline_multilingual_transformer_from_m2m")
class MultilingualTransformerModel(PipelineParallelTransformerModel):
    """Train Transformer models for multiple language pairs simultaneously.

    Requires `--task multilingual_translation`.

    We inherit all arguments from TransformerModel and assume that all language
    pairs use a single Transformer architecture. In addition, we provide several
    options that are specific to the multilingual setting.

    Args:
        --share-encoder-embeddings: share encoder embeddings across all source languages
        --share-decoder-embeddings: share decoder embeddings across all target languages
        --share-encoders: share all encoder params (incl. embeddings) across all source languages
        --share-decoders: share all decoder params (incl. embeddings) across all target languages
    """

    def __init__(self,  encoder, decoder, balance, devices, chunks, checkpoint, lang_pairs=None):
        super().__init__(encoders, decoders, lang_pairs)

    @staticmethod
    def add_args(parser):
        """Add model-specific arguments to the parser."""
        TransformerModel.add_args(parser)
        parser.add_argument(
            "--share-encoder-embeddings",
            action="store_true",
            help="share encoder embeddings across languages",
        )
        parser.add_argument(
            "--share-decoder-embeddings",
            action="store_true",
            help="share decoder embeddings across languages",
        )
        parser.add_argument(
            "--share-encoders",
            action="store_true",
            help="share encoders across languages",
        )
        parser.add_argument(
            "--share-decoders",
            action="store_true",
            help="share decoders across languages",
        )
        parser.add_argument(
            "--pretrained-m2m-checkpoint",
            type=str,
            metavar="STR",
            help="M2M model to use for initializing transformer encoder and/or decoder",
        )
        parser.add_argument(
            "--init-encoder-only",
            action="store_true",
            help="if set, don't load the M2M weights and embeddings into decoder",
        )
        parser.add_argument(
            "--init-decoder-only",
            action="store_true",
            help="if set, don't load the M2M weights and embeddings into encoder",
        )
        parser.add_argument(
            "--pretrained-m2m-dictionary",
            type=str,
            metavar="STR",
            help="M2M model to use for initializing transformer embedding",
        )


    @classmethod
    def build_model(cls, args, task):
        """Build a new model instance."""
        from fairseq.tasks.multilingual_translation import MultilingualTranslationTask

        assert isinstance(task, MultilingualTranslationTask)

        # make sure all arguments are present in older models
        base_multilingual_architecture(args)

        if not safe_hasattr(args, "max_source_positions"):
            args.max_source_positions = 1024
        if not safe_hasattr(args, "max_target_positions"):
            args.max_target_positions = 1024

        src_langs = [lang_pair.split("-")[0] for lang_pair in task.model_lang_pairs]
        tgt_langs = [lang_pair.split("-")[1] for lang_pair in task.model_lang_pairs]

        if args.share_encoders:
            args.share_encoder_embeddings = True
        if args.share_decoders:
            args.share_decoder_embeddings = True

        def build_embedding(dictionary, embed_dim, path=None):
            num_embeddings = len(dictionary)
            padding_idx = dictionary.pad()
            emb = Embedding(num_embeddings, embed_dim, padding_idx)
            # if provided, load from preloaded dictionaries
            if path:
                embed_dict = utils.parse_embedding(path)
                utils.load_embedding(embed_dict, dictionary, emb)
            return emb

        # build shared embeddings (if applicable)
        shared_encoder_embed_tokens, shared_decoder_embed_tokens = None, None
        if args.share_all_embeddings:
            if args.encoder_embed_dim != args.decoder_embed_dim:
                raise ValueError(
                    "--share-all-embeddings requires --encoder-embed-dim to match --decoder-embed-dim"
                )
            if args.decoder_embed_path and (
                args.decoder_embed_path != args.encoder_embed_path
            ):
                raise ValueError(
                    "--share-all-embeddings not compatible with --decoder-embed-path"
                )
            shared_encoder_embed_tokens = FairseqMultiModel.build_shared_embeddings(
                dicts=task.dicts,
                langs=task.langs,
                embed_dim=args.encoder_embed_dim,
                build_embedding=build_embedding,
                pretrained_embed_path=args.encoder_embed_path,
            )
            shared_decoder_embed_tokens = shared_encoder_embed_tokens
            args.share_decoder_input_output_embed = True
        else:
            if args.share_encoder_embeddings:
                shared_encoder_embed_tokens = FairseqMultiModel.build_shared_embeddings(
                    dicts=task.dicts,
                    langs=src_langs,
                    embed_dim=args.encoder_embed_dim,
                    build_embedding=build_embedding,
                    pretrained_embed_path=args.encoder_embed_path,
                )
            if args.share_decoder_embeddings:
                shared_decoder_embed_tokens = FairseqMultiModel.build_shared_embeddings(
                    dicts=task.dicts,
                    langs=tgt_langs,
                    embed_dim=args.decoder_embed_dim,
                    build_embedding=build_embedding,
                    pretrained_embed_path=args.decoder_embed_path,
                )

        # encoders/decoders for each language
        lang_encoders, lang_decoders = {}, {}

        def get_encoder(lang):
            if lang not in lang_encoders:
                if shared_encoder_embed_tokens is not None:
                    encoder_embed_tokens = shared_encoder_embed_tokens
                else:
                    encoder_embed_tokens = build_embedding(
                        task.dicts[lang],
                        args.encoder_embed_dim,
                        args.encoder_embed_path,
                    )
                if hasattr(args, "pretrained_m2m_checkpoint"):
                    set_pretrain_emb(args, task.dicts[lang], encoder_embed_tokens)
                    lang_encoders[get_lg_family_info(lang)] = TransformerEncoderFromPretrainedM2M(args, task.dicts[lang],
                                                                                              encoder_embed_tokens)
                else:
                    lang_encoders[get_lg_family_info(lang)] = cls._get_module_class(
                        True, args, task.dicts[lang], encoder_embed_tokens, src_langs
                    )
            return lang_encoders[get_lg_family_info(lang)]

        def get_decoder(lang):
            if lang not in lang_decoders:
                if shared_decoder_embed_tokens is not None:
                    decoder_embed_tokens = shared_decoder_embed_tokens
                else:
                    decoder_embed_tokens = build_embedding(
                        task.dicts[lang],
                        args.decoder_embed_dim,
                        args.decoder_embed_path,
                    )
                if hasattr(args, "pretrained_m2m_checkpoint"):
                    set_pretrain_emb(args, task.dicts[lang], decoder_embed_tokens)
                    lang_decoders[get_lg_family_info(lang)] = TransformerDecoderFromPretrainedM2M(args, task.dicts[lang],decoder_embed_tokens)
                else:
                    lang_decoders[get_lg_family_info(lang)] = cls._get_module_class(
                        False, args, task.dicts[lang], decoder_embed_tokens, tgt_langs
                    )
            return lang_decoders[get_lg_family_info(lang)]

        # shared encoders/decoders (if applicable)
        shared_encoder, shared_decoder = None, None
        if args.share_encoders:
            shared_encoder = get_encoder(src_langs[0])
        if args.share_decoders:
            shared_decoder = get_decoder(tgt_langs[0])

        encoders, decoders = OrderedDict(), OrderedDict()
        for lang_pair, src, tgt in zip(task.model_lang_pairs, src_langs, tgt_langs):
            encoders[get_lg_family_info(src)] = (
                shared_encoder if shared_encoder is not None else get_encoder(src)
            )
            decoders[get_lg_family_info(tgt)] = (
                shared_decoder if shared_decoder is not None else get_decoder(tgt)
            )

        return MultilingualTransformerModel(encoders, decoders, task.model_lang_pairs)

    @classmethod
    def _get_module_class(cls, is_encoder, args, lang_dict, embed_tokens, langs):
        module_class = TransformerEncoder if is_encoder else TransformerDecoder
        return module_class(args, lang_dict, embed_tokens)

    def load_state_dict(self, state_dict, strict=True, model_cfg=None):
        state_dict_subset = state_dict.copy()
        for k, _ in state_dict.items():
            assert k.startswith("models.")
            lang_pair = k.split(".")[1]
            if lang_pair not in self.models:
                del state_dict_subset[k]
        super().load_state_dict(state_dict_subset, strict=strict, model_cfg=model_cfg)

@register_model_architecture(
    "multilingual_transformer_from_m2m", "multilingual_transform_m2m_418"
)
def multilingual_transformer_iwslt_de_en(args):
    args.encoder_embed_dim = getattr(args, "encoder_embed_dim", 1024)
    args.encoder_ffn_embed_dim = getattr(args, "encoder_ffn_embed_dim", 4096)
    args.encoder_attention_heads = getattr(args, "encoder_attention_heads", 16)
    args.encoder_layers = getattr(args, "encoder_layers", 12)
    args.decoder_embed_dim = getattr(args, "decoder_embed_dim", 1024)
    args.decoder_ffn_embed_dim = getattr(args, "decoder_ffn_embed_dim", 4096)
    args.decoder_attention_heads = getattr(args, "decoder_attention_heads", 16)
    args.decoder_layers = getattr(args, "decoder_layers", 12)
    base_multilingual_architecture(args)
