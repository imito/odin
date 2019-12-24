from __future__ import absolute_import, division, print_function

import types

import numpy as np
import tensorflow as tf
from tensorflow.python import keras
from tensorflow.python.framework import tensor_shape
from tensorflow.python.keras.layers.convolutional import Conv as _Conv

from odin.backend.alias import (parse_activation, parse_constraint,
                                parse_initializer, parse_regularizer)
from odin.networks.util_layers import Conv1DTranspose
from odin.utils import as_tuple


# ===========================================================================
# Helpers
# ===========================================================================
def _as_arg_tuples(*args):
  ref = as_tuple(args[0], t=int)
  n = len(ref)
  return [ref] + [as_tuple(i, N=n) for i in args[1:]], n


def _store_arguments(d):
  self = d.pop('self')
  d.pop('__class__')
  self._init_arguments = dict(d)


def _rank_and_input_shape(rank, input_shape):
  if rank is None and input_shape is None:
    raise ValueError(
        "rank or input_shape must be given so the convolution type "
        "can be determined.")
  if rank is not None and input_shape is not None:
    if not isinstance(input_shape, tf.TensorShape):
      input_shape = tf.nest.flatten(input_shape)
    if rank != (len(input_shape) - 1):
      raise ValueError("rank=%d but given input_shape=%s (rank=%d)" %
                       (rank, str(input_shape), len(input_shape) - 1))
  if rank is None:
    rank = len(input_shape) - 1
  return rank, input_shape


_STORED_TRANSPOSE = {}


class SequentialNetwork(keras.Sequential):

  def __init__(self, start_layers=[], layers=None, extra_layers=[], name=None):
    layers = [[] if l is None else list(l)
              for l in (start_layers, layers, extra_layers)]
    layers = tf.nest.flatten(layers)
    super().__init__(layers=None if len(layers) == 0 else layers, name=name)

  @property
  def init_arguments(self):
    return dict(self._init_arguments)

  def transpose(self, input_shape=None, tied_weights=False):
    raise NotImplementedError

  def get_config(self):
    return super().get_config()

  @classmethod
  def from_config(cls, config, custom_objects=None):
    return cls.from_config(config, custom_objects)


# ===========================================================================
# Networks
# ===========================================================================
class DenseNetwork(SequentialNetwork):
  r""" Multi-layers neural network """

  def __init__(self,
               units=128,
               activation='relu',
               use_bias=True,
               kernel_initializer='glorot_uniform',
               bias_initializer='zeros',
               kernel_regularizer=None,
               bias_regularizer=None,
               activity_regularizer=None,
               kernel_constraint=None,
               bias_constraint=None,
               flatten=False,
               batchnorm=True,
               input_dropout=0.,
               output_dropout=0.,
               layer_dropout=0.,
               input_shape=None,
               start_layers=[],
               extra_layers=[],
               name=None):
    (units, activation, use_bias, kernel_initializer, bias_initializer,
     kernel_regularizer, bias_regularizer, activity_regularizer,
     kernel_constraint, bias_constraint,
     batchnorm, layer_dropout), nlayers = _as_arg_tuples(
         units, activation, use_bias, kernel_initializer, bias_initializer,
         kernel_regularizer, bias_regularizer, activity_regularizer,
         kernel_constraint, bias_constraint, batchnorm, layer_dropout)
    _store_arguments(locals())

    layers = []
    if input_shape is not None:
      layers.append(keras.Input(shape=input_shape))
    if flatten:
      layers.append(keras.layers.Flatten())
    if 0. < input_dropout < 1.:
      layers.append(keras.layers.Dropout(input_dropout))
    for i in range(nlayers):
      layers.append(
          keras.layers.Dense(\
            units[i],
            activation='linear',
            use_bias=(False if batchnorm else True) and use_bias[i],
            kernel_initializer=kernel_initializer[i],
            bias_initializer=bias_initializer[i],
            kernel_regularizer=kernel_regularizer[i],
            bias_regularizer=bias_regularizer[i],
            activity_regularizer=activity_regularizer[i],
            kernel_constraint=kernel_constraint[i],
            bias_constraint=bias_constraint[i],
            name="Layer%d" % i))
      if batchnorm[i]:
        layers.append(keras.layers.BatchNormalization())
      layers.append(keras.layers.Activation(activation[i]))
      if layer_dropout[i] > 0 and i != nlayers - 1:
        layers.append(keras.layers.Dropout(rate=layer_dropout[i]))
    if 0. < output_dropout < 1.:
      layers.append(keras.layers.Dropout(output_dropout))
    super().__init__(start_layers=start_layers,
                     layers=layers,
                     extra_layers=extra_layers,
                     name=name)

  def transpose(self, input_shape=None, tied_weights=False):
    if id(self) in _STORED_TRANSPOSE:
      return _STORED_TRANSPOSE[id(self)]
    args = self.init_arguments
    args['units'] = args['units'][::-1]
    args['input_shape'] = input_shape
    args['name'] = self.name + '_transpose'
    args['flatten'] = False
    if tied_weights:
      args['kernel_constraint'] = None
      args['kernel_regularizer'] = None
    transpose_net = DenseNetwork(**args)
    _STORED_TRANSPOSE[id(self)] = transpose_net
    if tied_weights:
      weights = [w for w in self.weights if '/kernel' in w.name][::-1][:-1]
      layers = [
          l for l in transpose_net.layers if isinstance(l, keras.layers.Dense)
      ][1:]
      for w, l in zip(weights, layers):

        def build(self, input_shape):
          input_shape = tensor_shape.TensorShape(input_shape)
          last_dim = tensor_shape.dimension_value(input_shape[-1])
          self.input_spec = keras.layers.InputSpec(min_ndim=2,
                                                   axes={-1: last_dim})
          self.kernel = tf.transpose(self.tied_kernel)
          if self.use_bias:
            self.bias = self.add_weight('bias',
                                        shape=(self.units,),
                                        initializer=self.bias_initializer,
                                        regularizer=self.bias_regularizer,
                                        constraint=self.bias_constraint,
                                        dtype=self.dtype,
                                        trainable=True)
          else:
            self.bias = None
          self.built = True

        l.tied_kernel = w
        l.build = types.MethodType(build, l)
    return transpose_net


class ConvNetwork(SequentialNetwork):
  r""" Multi-layers neural network """

  def __init__(self,
               filters,
               rank=2,
               kernel_size=3,
               strides=1,
               padding='same',
               dilation_rate=1,
               activation='relu',
               use_bias=True,
               kernel_initializer='glorot_uniform',
               bias_initializer='zeros',
               kernel_regularizer=None,
               bias_regularizer=None,
               activity_regularizer=None,
               kernel_constraint=None,
               bias_constraint=None,
               batchnorm=True,
               input_dropout=0.,
               output_dropout=0.,
               layer_dropout=0.,
               input_shape=None,
               start_layers=[],
               extra_layers=[],
               name=None):
    rank, input_shape = _rank_and_input_shape(rank, input_shape)
    (filters, kernel_size, strides, padding, dilation_rate, activation,
     use_bias, kernel_initializer, bias_initializer, kernel_regularizer,
     bias_regularizer, activity_regularizer, kernel_constraint, bias_constraint,
     batchnorm, layer_dropout), nlayers = _as_arg_tuples(
         filters, kernel_size, strides, padding, dilation_rate, activation,
         use_bias, kernel_initializer, bias_initializer, kernel_regularizer,
         bias_regularizer, activity_regularizer, kernel_constraint,
         bias_constraint, batchnorm, layer_dropout)
    _store_arguments(locals())

    layers = []
    if input_shape is not None:
      layers.append(keras.Input(shape=input_shape))
    if 0. < input_dropout < 1.:
      layers.append(keras.layers.Dropout(input_dropout))

    if rank == 3:
      layer_type = keras.layers.Conv3D
    elif rank == 2:
      layer_type = keras.layers.Conv2D
    elif rank == 1:
      layer_type = keras.layers.Conv1D

    for i in range(nlayers):
      layers.append(
          layer_type(\
            filters=filters[i],
            kernel_size=kernel_size[i],
            strides=strides[i],
            padding=padding[i],
            dilation_rate=dilation_rate[i],
            activation='linear',
            use_bias=(False if batchnorm else True) and use_bias[i],
            kernel_initializer=kernel_initializer[i],
            bias_initializer=bias_initializer[i],
            kernel_regularizer=kernel_regularizer[i],
            bias_regularizer=bias_regularizer[i],
            activity_regularizer=activity_regularizer[i],
            kernel_constraint=kernel_constraint[i],
            bias_constraint=bias_constraint[i],
            name="Layer%d" % i))
      if batchnorm[i]:
        layers.append(keras.layers.BatchNormalization())
      layers.append(keras.layers.Activation(activation[i]))
      if layer_dropout[i] > 0 and i != nlayers - 1:
        layers.append(keras.layers.Dropout(rate=layer_dropout[i]))
    if 0. < output_dropout < 1.:
      layers.append(keras.layers.Dropout(output_dropout))
    super().__init__(start_layers=start_layers,
                     layers=layers,
                     extra_layers=extra_layers,
                     name=name)

  def transpose(self, input_shape=None, tied_weights=False):
    if tied_weights:
      raise NotImplementedError(
          "No support for tied_weights in ConvNetwork.transpose")
    if id(self) in _STORED_TRANSPOSE:
      return _STORED_TRANSPOSE[id(self)]
    args = {
        k: v[::-1] if isinstance(v, tuple) else v
        for k, v in self.init_arguments.items()
    }
    rank = args['rank']
    # input_shape: infer based on output of ConvNetwork
    start_layers = []
    if hasattr(self, 'output_shape'):
      if input_shape is None:
        start_layers.append(keras.Input(input_shape=self.output_shape[1:]))
      else:
        input_shape = as_tuple(input_shape)
        shape = [
            l.output_shape[1:]
            for l in self.layers[::-1]
            if isinstance(l, _Conv)
        ][0] # last convolution layer
        start_layers = [keras.layers.Flatten(input_shape=input_shape)]
        if input_shape != shape:
          if np.prod(input_shape) != np.prod(shape):
            start_layers.append(
                keras.layers.Dense(units=int(np.prod(shape)),
                                   use_bias=False,
                                   activation='linear'))
          start_layers.append(keras.layers.Reshape(shape))
    # output_shape: simple projection, no bias or activation
    extra_layers = []
    if hasattr(self, 'input_shape'):
      output_channel = self.input_shape[-1]
      if output_channel != args['filters'][-1]:
        if rank == 3:
          raise NotImplementedError
        elif rank == 2:
          layer_type = keras.layers.Conv2DTranspose
        elif rank == 1:
          layer_type = Conv1DTranspose
        extra_layers.append(
            layer_type(filters=output_channel,
                       kernel_size=1,
                       padding='same',
                       use_bias=False,
                       activation='linear'))
    # create the transposed network
    transposed = DeconvNetwork(
        filters=args['filters'],
        rank=args['rank'],
        kernel_size=args['kernel_size'],
        strides=args['strides'],
        padding=args['padding'],
        dilation_rate=args['dilation_rate'],
        activation=args['activation'],
        use_bias=args['use_bias'],
        kernel_initializer=args['kernel_initializer'],
        bias_initializer=args['bias_initializer'],
        kernel_regularizer=args['kernel_regularizer'],
        bias_regularizer=args['bias_regularizer'],
        activity_regularizer=args['activity_regularizer'],
        kernel_constraint=args['kernel_constraint'],
        bias_constraint=args['bias_constraint'],
        batchnorm=args['batchnorm'],
        input_dropout=args['input_dropout'],
        output_dropout=args['output_dropout'],
        layer_dropout=args['layer_dropout'],
        start_layers=start_layers,
        extra_layers=extra_layers)
    _STORED_TRANSPOSE[id(self)] = transposed
    return transposed


class DeconvNetwork(SequentialNetwork):
  r""" Multi-layers neural network """

  def __init__(self,
               filters,
               rank=2,
               kernel_size=3,
               strides=1,
               padding='same',
               output_padding=None,
               dilation_rate=1,
               activation='relu',
               use_bias=True,
               kernel_initializer='glorot_uniform',
               bias_initializer='zeros',
               kernel_regularizer=None,
               bias_regularizer=None,
               activity_regularizer=None,
               kernel_constraint=None,
               bias_constraint=None,
               batchnorm=True,
               input_dropout=0.,
               output_dropout=0.,
               layer_dropout=0.,
               input_shape=None,
               start_layers=[],
               extra_layers=[],
               name=None):
    rank, input_shape = _rank_and_input_shape(rank, input_shape)
    (filters, kernel_size, strides, padding, output_padding, dilation_rate,
     activation, use_bias, kernel_initializer, bias_initializer,
     kernel_regularizer, bias_regularizer, activity_regularizer,
     kernel_constraint, bias_constraint,
     batchnorm, layer_dropout), nlayers = _as_arg_tuples(
         filters, kernel_size, strides, padding, output_padding, dilation_rate,
         activation, use_bias, kernel_initializer, bias_initializer,
         kernel_regularizer, bias_regularizer, activity_regularizer,
         kernel_constraint, bias_constraint, batchnorm, layer_dropout)
    _store_arguments(locals())

    layers = []
    if input_shape is not None:
      layers.append(keras.Input(shape=input_shape))
    if 0. < input_dropout < 1.:
      layers.append(keras.layers.Dropout(input_dropout))

    if rank == 3:
      raise NotImplementedError
    elif rank == 2:
      layer_type = keras.layers.Conv2DTranspose
    elif rank == 1:
      layer_type = Conv1DTranspose

    for i in range(nlayers):
      layers.append(
          layer_type(\
            filters=filters[i],
            kernel_size=kernel_size[i],
            strides=strides[i],
            padding=padding[i],
            output_padding=output_padding[i],
            dilation_rate=dilation_rate[i],
            activation='linear',
            use_bias=(False if batchnorm else True) and use_bias[i],
            kernel_initializer=kernel_initializer[i],
            bias_initializer=bias_initializer[i],
            kernel_regularizer=kernel_regularizer[i],
            bias_regularizer=bias_regularizer[i],
            activity_regularizer=activity_regularizer[i],
            kernel_constraint=kernel_constraint[i],
            bias_constraint=bias_constraint[i],
            name="Layer%d" % i))
      if batchnorm[i]:
        layers.append(keras.layers.BatchNormalization())
      layers.append(keras.layers.Activation(activation[i]))
      if layer_dropout[i] > 0 and i != nlayers - 1:
        layers.append(keras.layers.Dropout(rate=layer_dropout[i]))
    if 0. < output_dropout < 1.:
      layers.append(keras.layers.Dropout(output_dropout))
    super().__init__(start_layers=start_layers,
                     layers=layers,
                     extra_layers=extra_layers,
                     name=name)
