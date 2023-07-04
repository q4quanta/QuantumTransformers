import os
from collections import Counter
import tarfile

import gdown
import numpy as np
import torch
import torch.utils.data
import torchvision
import torchtext


def datasets_to_dataloaders(train_dataset, valid_dataset, **dataloader_kwargs):
    """Returns dataloaders for the given datasets"""
    train_dataloader = torch.utils.data.DataLoader(train_dataset, shuffle=True, **dataloader_kwargs)
    valid_dataloader = torch.utils.data.DataLoader(valid_dataset, **dataloader_kwargs)
    return train_dataloader, valid_dataloader


def get_mnist_dataloaders(root='data', download=True, **dataloader_kwargs):
    """Returns dataloaders for the MNIST digits dataset (computer vision, 10-class classification)"""
    transform = torchvision.transforms.Compose([
        torchvision.transforms.ToTensor(),
        torchvision.transforms.Normalize((0.1307,), (0.3081,))
    ])
    train_dataset = torchvision.datasets.MNIST(root, train=True, download=download, transform=transform)
    valid_dataset = torchvision.datasets.MNIST(root, train=False, download=download, transform=transform)
    return datasets_to_dataloaders(train_dataset, valid_dataset, **dataloader_kwargs)


def download_dataset(root, name, gdrive_id, remove_archive=True, verbose=True):
    """Downloads a dataset from Google Drive and extracts it if it does not already exist"""
    if os.path.exists(f'{root}/{name}'):
        if verbose:
            print(f'{root}/{name} already exists, skipping download')
        return
    os.makedirs(f'{root}/{name}', exist_ok=True)
    gdown.download(id=gdrive_id, output=f'{root}/{name}.tar.xz', quiet=not verbose)
    with tarfile.open(f'{root}/{name}.tar.xz') as f:
        print(f'Extracting {name}.tar.xz to {root}...')
        f.extractall(f'{root}')
    if remove_archive:
        os.remove(f'{root}/{name}.tar.xz')


def npy_loader(path):
    """Loads a .npy file as a PyTorch tensor"""
    sample = torch.from_numpy(np.load(path))
    return sample


def get_electron_photon_dataloaders(root='data', download=True, **dataloader_kwargs):
    """Returns dataloaders for the electron-photon dataset (computer vision - particle physics, binary classification)"""
    if download:
        download_dataset(root, 'electron-photon', '1VAqGQaMS5jSWV8gTXw39Opz-fNMsDZ8e')
    train_dataset = torchvision.datasets.DatasetFolder(root=f'{root}/electron-photon/train', loader=npy_loader, extensions=('.npy',))
    valid_dataset = torchvision.datasets.DatasetFolder(root=f'{root}/electron-photon/test', loader=npy_loader, extensions=('.npy',))
    return datasets_to_dataloaders(train_dataset, valid_dataset, **dataloader_kwargs)


def get_quark_gluon_dataloaders(root='data', download=True, **dataloader_kwargs):
    """Returns dataloaders for the quark-gluon dataset (computer vision - particle physics, binary classification)"""
    if download:
        download_dataset(root, 'quark-gluon', '1G6HJKf3VtRSf7JLms2t1ofkYAldOKMls')
    train_dataset = torchvision.datasets.DatasetFolder(root=f'{root}/quark-gluon/train', loader=npy_loader, extensions=('.npy',))
    valid_dataset = torchvision.datasets.DatasetFolder(root=f'{root}/quark-gluon/test', loader=npy_loader, extensions=('.npy',))
    return datasets_to_dataloaders(train_dataset, valid_dataset, **dataloader_kwargs)


def get_imdb_dataloaders(root='data', **dataloader_kwargs):
    """Returns dataloaders for the IMDB sentiment analysis dataset (natural language processing, binary classification)"""
    train_dataset = torchtext.datasets.IMDB(root, split='train')
    valid_dataset = torchtext.datasets.IMDB(root, split='test')

    tokenizer = torchtext.data.utils.get_tokenizer('basic_english')
    counter = Counter()
    for _, line in train_dataset:
        counter.update(tokenizer(line))
    unk_token, bos_token, eos_token, pad_token = '<UNK>', '<BOS>', '<EOS>', '<PAD>'
    vocab = torchtext.vocab.vocab(counter, min_freq=10, specials=(unk_token, bos_token, eos_token, pad_token))
    vocab.set_default_index(vocab[unk_token])

    def text_transform(x): return [vocab['<BOS>']] + [vocab[token] for token in tokenizer(x)] + [vocab['<EOS>']]
    def label_transform(x): return x - 1  # 1/2 -> 0/1

    def collate_batch(batch):
        label_list, text_list = [], []
        for label, text in batch:
            label_list.append(label_transform(label))
            text_list.append(torch.tensor(text_transform(text)))
        return torch.nn.utils.rnn.pad_sequence(text_list, padding_value=vocab['<PAD>'], batch_first=True), torch.tensor(label_list)

    return (datasets_to_dataloaders(list(train_dataset), list(valid_dataset), collate_fn=collate_batch, **dataloader_kwargs), vocab)
