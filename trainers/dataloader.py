"""
A collection of dataloaders
"""

import os

import numpy as np
import torch
from tqdm import tqdm

from models.embedding_models import GenericEmbedder
from trainers.utils import load_data


class BaseDataloader:
    """Abstract class for dataloaders"""

    def __init__(
        self,
        cfg,
        embedder,
    ):
        """Arguments:
        cfg: the train script cfg,
        tokenizer: the tokenizer object
            This is required to pre-tokenize the data
        """
        self.cfg = cfg
        self.model_cfg = cfg.model
        self.trainer_cfg = cfg.trainer
        self.tokenized_data_dir = cfg["general"]["paths"]["data_dir"]
        self.embedder: GenericEmbedder = embedder
        self.context_window = self.model_cfg["context_window"]
        self.vocab_size = self.model_cfg["vocab_size"]
        self.batch_size = self.trainer_cfg["training"]["batch_size"]
        self.dataset_name = self.trainer_cfg["dataset"]
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.tokenized_data_path = os.path.join(
            self.tokenized_data_dir,
            self.dataset_name,
            f'{self.model_cfg["embedder"]["tokenizer_type"]}-{self.model_cfg["vocab_size"]}',
        )
        self.masking_pct = cfg.get("trainer", {}).get("dataloader", {}).get("masking_pct", 0.15) # masking percentage for NextTokenMLMDataloader

    def set_split(self, split):
        """
        Get a split of the data
        """
        self.split = split
        self.data = self.get_data(split=split)
        self.general_device = self.cfg.general.device

    def __len__(self):
        return len(self.data) - self.context_window
        
    def __getitem__(self, idx): ## works for BytePooling and StandardDataloader, but not for ConversationalDataloader and NextTokenMLMDataloader
        '''
        Similar to the get_batch method in the original dataloader class.
        '''
        X = torch.from_numpy((self.data[idx : idx + self.context_window]).astype(np.int64))
        y = torch.from_numpy((self.data[idx + 1 : idx + 1 + self.context_window]).astype(np.int64))

        X, y = X.pin_memory().to(self.general_device, non_blocking=True), y.pin_memory().to(
            self.general_device, non_blocking=True
        )

        return X, y


    def get_batch(self, split="train"):
        """
        Get a train/val batch
        """
        data = np.memmap(
            os.path.join(self.tokenized_data_path, f"{split}.bin"),
            dtype=np.uint16,
            mode="r",
        )

        idxs = torch.randint(len(data) - self.context_window, (self.batch_size,))
        X = torch.stack(
            [
                torch.from_numpy((data[i : i + self.context_window]).astype(np.int64))
                for i in idxs
            ]
        )
        y = torch.stack(
            [
                torch.from_numpy(
                    (data[i + 1 : i + 1 + self.context_window]).astype(np.int64)
                )
                for i in idxs
            ]
        )

        X, y = X.pin_memory().to(self.device, non_blocking=True), y.pin_memory().to(
            self.device, non_blocking=True
        )

        return X, y

    def check_processed(self):
        """
        Check if the data has been preprocessed
        """
        return os.path.exists(self.tokenized_data_path)

    def _write_tokenized_data(self, tokenized):
        for split, dset in tokenized.items():
            arr_len = np.sum(dset["len"], dtype=np.uint64)
            filename = os.path.join(self.tokenized_data_path, f"{split}.bin")
            dtype = np.uint16  # (can do since enc.max_token_value == 50256 is < 2**16)
            arr = np.memmap(filename, dtype=dtype, mode="w+", shape=(arr_len,))
            total_batches = 1024

            idx = 0
            for batch_idx in tqdm(range(total_batches), desc=f"writing {filename}"):
                # Batch together samples for faster write
                batch = dset.shard(
                    num_shards=total_batches, index=batch_idx, contiguous=True
                ).with_format("numpy")
                arr_batch = np.concatenate(batch["ids"])
                # Write into mmap
                arr[idx : idx + len(arr_batch)] = arr_batch
                idx += len(arr_batch)
            arr.flush()

    def prepare_data(self):
        """
        Tokenize and store the data
        """
        # create folder
        if not os.path.exists(self.tokenized_data_path):
            os.makedirs(self.tokenized_data_path)
        else:
            return  # already processed

        # load the dataset
        split_dataset = load_data(
            dataset_name=self.dataset_name,
        )
        # can be used for debugging
        #split_dataset["train"] = split_dataset["train"].select(range(2048))
        #split_dataset["val"] = split_dataset["val"].select(range(2048))

        def process(example):
            ids = self.embedder.tokenize_input(example["text"])
            return {"ids": ids, "len": len(ids)}

        try:
            # tokenize the dataset
            tokenized = split_dataset.map(
                process,
                remove_columns=["text"],
                desc="tokenizing the splits",
                num_proc=1,
            )

            # concatenate all the ids in each dataset into one large file we can use for training
            self._write_tokenized_data(tokenized)
        except RuntimeError as exc:
            # if we fail, destroy the file
            os.removedirs(self.tokenized_data_path)
            raise SystemExit from exc
        
    def get_data(self, split="train"):
        """
        Get the data
        """
        ## load the data
        data = np.memmap(
            os.path.join(self.tokenized_data_path, f"{split}.bin"),
            dtype=np.uint16,
            mode="r",
        )

        ## additional steps for loading BytePooling and Conversational dataloaders
        if hasattr(self, 'loading_shapes') and self.loading_shapes[split] is None:
            if isinstance(self, BytePoolingDataloader):
                self.loading_shapes[split] = (len(data)// self.model_cfg.embedder.byte_context_window, self.model_cfg.embedder.byte_context_window)

                ## reset the data
                data = None

                ## re-load the data with loading shapes
                data = np.memmap(
                    os.path.join(self.tokenized_data_path, f"{split}.bin"),
                    dtype=np.uint16,
                    mode="r",
                    shape=self.loading_shapes[split],
                )
            
            elif isinstance(self, ConversationalDataloader):
                self.loading_shapes[split] = (int(len(data)/2/self.context_window), 2, self.context_window)

                ## reset the data
                data = None

                ## re-load the data with loading shapes
                data = np.memmap(
                    os.path.join(self.tokenized_data_path, f"{split}.bin"), 
                    dtype=np.uint16, 
                    mode="r+",
                    shape=self.loading_shapes[split]
                )
            
        return data


class StandardDataloader(BaseDataloader):
    """
    A basic dataloader that preprocesses a dataset via
    tokenization, and then randomly loads batches as
    necessary.
    """


class BytePoolingDataloader(BaseDataloader):
    """
    A basic dataloader that preprocesses a dataset via
    tokenization, and then randomly loads batches as
    necessary.
    Args:
        cfg: the data config
        preprocess: whether to preprocess the data
    """

    def __init__(self, cfg, embedder):
        super().__init__(cfg, embedder=embedder)
        self.tokenized_data_path += f"-BytePooling"
        self.loading_shapes = {"train": None, "val": None}

    def _write_tokenized_data(self, tokenized):
        for split, dset in tokenized.items():
            arr_len = np.sum(dset["len"], dtype=np.uint64)
            filename = os.path.join(self.tokenized_data_path, f"{split}.bin")
            dtype = np.uint16  # (can do since enc.max_token_value == 50256 is < 2**16)
            arr = np.memmap(
                filename,
                dtype=dtype,
                mode="w+",
                shape=(arr_len, self.model_cfg.embedder.byte_context_window),
            )
            total_batches = 1024

            idx = 0
            for batch_idx in tqdm(range(total_batches), desc=f"writing {filename}"):
                # Batch together samples for faster write
                batch = dset.shard(
                    num_shards=total_batches, index=batch_idx, contiguous=True
                ).with_format("numpy")
                arr_batch = np.concatenate(batch["ids"])
                # Write into mmap
                arr[idx : idx + len(arr_batch)] = arr_batch
                idx += len(arr_batch)
            arr.flush()

    def get_batch(self, split="train"):
        """
        Get a train/val batch
        """
        if self.loading_shapes[split] is None:
            data = np.memmap(
                os.path.join(self.tokenized_data_path, f"{split}.bin"),
                dtype=np.uint16,
                mode="r",
            )
            self.loading_shapes[split] = (len(data)// self.model_cfg.embedder.byte_context_window, self.model_cfg.embedder.byte_context_window)
            
        data = np.memmap(
            os.path.join(self.tokenized_data_path, f"{split}.bin"),
            dtype=np.uint16,
            mode="r",
            shape=self.loading_shapes[split],
        )

        idxs = torch.randint(len(data) - self.context_window, (self.batch_size,))
        X = torch.stack(
            [
                torch.from_numpy((data[i : i + self.context_window]).astype(np.int64))
                for i in idxs
            ]
        )
        y = torch.stack(
            [
                torch.from_numpy(
                    (data[i + 1 : i + 1 + self.context_window]).astype(np.int64)
                )
                for i in idxs
            ]
        )

        X, y = X.pin_memory().to(self.device, non_blocking=True), y.pin_memory().to(
            self.device, non_blocking=True
        )

        return X, y



class ConversationalDataloader(BaseDataloader):
    """
    A basic dataloader for conversational or turn-based
    datasets.
    """
    def __init__(self, cfg, embedder):
        super().__init__(cfg, embedder=embedder)
        self.loading_shapes = {"train": None, "val": None}
        # check if dataset is conversational
        assert cfg["trainer"]["dataset"] in ["openhermes-2.5"], "Dataset must be conversational"

    def get_batch(self, split="train"):
        """
        Get a train/val batch
        """
        if self.loading_shapes[split] is None:
            data = np.memmap(
                os.path.join(self.tokenized_data_path, f"{split}.bin"),
                dtype=np.uint16,
                mode="r",
            )
            self.loading_shapes[split] = (int(len(data)/2/self.context_window), 2, self.context_window)
            data = None 

        # actually load the data
        data = np.memmap(
            os.path.join(self.tokenized_data_path, f"{split}.bin"), 
            shape=self.loading_shapes[split],
            dtype=np.uint16, 
            mode="r+"
        )

        ix = torch.randint(len(data), (self.batch_size,))
        # test load one 
        a = data[ix[0]]
        Xy = torch.stack(
            [
                torch.from_numpy((data[i]).astype(np.int64))
                for i in ix
            ]
        )

        # transpose and split into X y
        X = Xy[:, 0]
        y = Xy[:, 1]


        X, y = X.pin_memory().to(self.device, non_blocking=True), y.pin_memory().to(
            self.device, non_blocking=True
        )

        return X, y


    def _write_tokenized_data(self, tokenized):
        # concatenate all the ids in each dataset into one large file we can use for training
        for split, dset in tokenized.items():
            arr_shape = (len(dset), 2, self.context_window)
            filename = os.path.join(self.tokenized_data_path, f"{split}.bin")
            dtype = np.uint16  # (can do since enc.max_token_value == 50256 is < 2**16)
            arr = np.memmap(filename, dtype=dtype, mode="w+", shape=arr_shape)
            total_batches = 1024

            idx = 0
            for batch_idx in tqdm(range(total_batches), desc=f"writing {filename}"):
                # Batch together samples for faster write
                batch = dset.shard(
                    num_shards=total_batches, index=batch_idx, contiguous=True
                ).with_format("numpy")
                arr_batch = np.stack(batch["ids"])
                # Write into mmap
                arr[idx : idx + len(arr_batch)] = arr_batch
                idx += len(arr_batch)
            arr.flush()


    def prepare_data(self):
        """
        Tokenize and store the data
        """
        # create folder
        if not os.path.exists(self.tokenized_data_path):
            os.makedirs(self.tokenized_data_path)
        else:
            return  # already processed

        # load the dataset
        split_dataset = load_data(
            dataset_name=self.dataset_name,
        )
        # can be used for debugging
        #split_dataset["train"] = split_dataset["train"].select(range(2048))
        #split_dataset["val"] = split_dataset["val"].select(range(2048))

        def process(example):
            question_ids = self.embedder.tokenize_input(example["conversations"][0]["value"])
            response_ids = self.embedder.tokenize_input(example["conversations"][1]["value"])
            qa_pair = np.stack([question_ids, response_ids])
            return {"ids" : qa_pair}

        try:
            # tokenize the dataset
            tokenized = split_dataset.map(
                process,
                remove_columns=["conversations"],
                desc="tokenizing the splits",
                num_proc=1,
            )

            # concatenate all the ids in each dataset into one large file we can use for training
            self._write_tokenized_data(tokenized)
        except RuntimeError as exc:
            # if we fail, destroy the file
            os.removedirs(self.tokenized_data_path)
            raise SystemExit from exc


    def __len__(self):
        '''
        (different from the other dataloaders) 
        Length of dataset includes the context window.
        '''
        return len(self.data)
    
    def __getitem__(self, idx):
        '''
        (different from the other dataloaders)
        Method of getting X and y is different.
        '''
        
        Xy = torch.stack(
            torch.from_numpy((self.data[idx]).astype(np.int64))
        )

        # transpose and split into X y
        X = Xy[:, 0]
        y = Xy[:, 1]


        X, y = X.pin_memory().to(self.device, non_blocking=True), y.pin_memory().to(
            self.device, non_blocking=True
        )

        return X, y


class NextTokenMLMDataloader(BaseDataloader):
    """
    Similarly to the generic dataloader, but mask out some tokens and
    return the mask used.
    """
    def get_batch(self, split="train", masking_pct=0.15):
        """
        Get a train/val batch
        """
        data = np.memmap(
            os.path.join(self.tokenized_data_path, f"{split}.bin"),
            dtype=np.uint16,
            mode="r",
        )

        idxs = torch.randint(len(data) - self.context_window, (self.batch_size,))
        X = torch.stack(
            [
                torch.from_numpy((data[i : i + self.context_window]).astype(np.int64))
                for i in idxs
            ]
        )
        y = torch.stack(
            [
                torch.from_numpy(
                    (data[i + 1 : i + 1 + self.context_window]).astype(np.int64)
                )
                for i in idxs
            ]
        )

        X, y = X.pin_memory().to(self.device, non_blocking=True), y.pin_memory().to(
            self.device, non_blocking=True
        )

        # mask out some tokens
        mask = torch.rand(X.size()) < masking_pct
        X[mask] = 0

        return X, (y, mask)
    
    def __getitem__(self, idx):
        '''
        (different from the other dataloaders)
        This returns y as a tuple of (y and mask)
        '''
        assert self.masking_pct is not None, "Masking percentage (self.masking_pct) must be set."

        X = torch.from_numpy((self.data[idx : idx + self.context_window]).astype(np.int64))
        y = torch.from_numpy((self.data[idx + 1 : idx + 1 + self.context_window]).astype(np.int64))

        X, y = X.pin_memory().to(self.general_device, non_blocking=True), y.pin_memory().to(
            self.general_device, non_blocking=True
        )

        # mask out some tokens
        mask = torch.rand(X.size()) < self.masking_pct
        X[mask] = 0
        
        return X, (y, mask)
