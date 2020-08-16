# Copyright (c) Open-MMLab. All rights reserved.
from itertools import chain

import torch
from torch.cuda._utils import _get_device_index
from torch.nn.parallel import DataParallel
from torch.nn.parallel.data_parallel import _check_balance

from .scatter_gather import scatter_kwargs


class MMDataParallel(DataParallel):
    """The DataParallel module that supports DataContainer.

    MMDataParallel has two main differences with PyTorch DataParallel:

    - It supports a custom type :class:`DataContainer` which allows more
      flexible control of input data during both GPU and CPU inference.
    - It implement two more APIs ``train_step()`` and ``val_step()``.

    Args:
        module (:class:`nn.Module`): Module to be encapsulated.
        device_ids (list[int]): Device IDS of modules to be scattered to.
        output_device (str | int): Device ID for output
        dim (int): Dimension used to scatter the data.
    """

    def __init__(self, module, device_ids=None, output_device=None, dim=0):
        super(DataParallel, self).__init__()

        if not torch.cuda.is_available():
            self.module = module
            self.device_ids = []
            # we add this line from the original DP to enable CPU inference.
            self.dim = dim
            return

        if device_ids is None:
            device_ids = list(range(torch.cuda.device_count()))
        if output_device is None:
            output_device = device_ids[0]

        self.dim = dim
        self.module = module
        self.device_ids = list(
            map(lambda x: _get_device_index(x, True), device_ids))
        self.output_device = _get_device_index(output_device, True)
        self.src_device_obj = torch.device('cuda:{}'.format(
            self.device_ids[0]))

        _check_balance(self.device_ids)

        if len(self.device_ids) == 1:
            self.module.cuda(device_ids[0])

    def forward(self, *inputs, **kwargs):
        """Override the original forward function.

        The main difference lies in the CPU inference where the datas in
        :class:`DataContainers` will still be gathered.
        """
        if not self.device_ids:
            # We add the following line thus the module could gather and
            # convert data containers as those in GPU inference
            inputs, kwargs = self.scatter(inputs, kwargs, [-1])
            return self.module(*inputs[0], **kwargs[0])

        for t in chain(self.module.parameters(), self.module.buffers()):
            if t.device != self.src_device_obj:
                raise RuntimeError(
                    'module must have its parameters and buffers '
                    f'on device {self.src_device_obj} (device_ids[0]) but '
                    f'found one of them on device: {t.device}')

        inputs, kwargs = self.scatter(inputs, kwargs, self.device_ids)
        if len(self.device_ids) == 1:
            return self.module(*inputs[0], **kwargs[0])
        replicas = self.replicate(self.module, self.device_ids[:len(inputs)])
        outputs = self.parallel_apply(replicas, inputs, kwargs)
        return self.gather(outputs, self.output_device)

    def scatter(self, inputs, kwargs, device_ids):
        return scatter_kwargs(inputs, kwargs, device_ids, dim=self.dim)

    def train_step(self, *inputs, **kwargs):
        if not self.device_ids:
            inputs, kwargs = self.scatter(inputs, kwargs, -1)
            return self.module.train_step(*inputs, **kwargs)

        assert len(self.device_ids) == 1, \
            ('MMDataParallel only supports single GPU training, if you need to'
             ' train with multiple GPUs, please use MMDistributedDataParallel'
             'instead.')

        for t in chain(self.module.parameters(), self.module.buffers()):
            if t.device != self.src_device_obj:
                raise RuntimeError(
                    'module must have its parameters and buffers '
                    f'on device {self.src_device_obj} (device_ids[0]) but '
                    f'found one of them on device: {t.device}')

        inputs, kwargs = self.scatter(inputs, kwargs, self.device_ids)
        return self.module.train_step(*inputs[0], **kwargs[0])

    def val_step(self, *inputs, **kwargs):
        if not self.device_ids:
            inputs, kwargs = self.scatter(inputs, kwargs, -1)
            return self.module.val_step(*inputs, **kwargs)

        assert len(self.device_ids) == 1, \
            ('MMDataParallel only supports single GPU training, if you need to'
             ' train with multiple GPUs, please use MMDistributedDataParallel'
             'instead.')

        for t in chain(self.module.parameters(), self.module.buffers()):
            if t.device != self.src_device_obj:
                raise RuntimeError(
                    'module must have its parameters and buffers '
                    f'on device {self.src_device_obj} (device_ids[0]) but '
                    f'found one of them on device: {t.device}')

        inputs, kwargs = self.scatter(inputs, kwargs, self.device_ids)
        return self.module.val_step(*inputs[0], **kwargs[0])
