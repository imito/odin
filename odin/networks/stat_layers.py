from __future__ import absolute_import, division, print_function

import inspect
from functools import partial
from typing import Callable, Optional, Text, Type, Union

import tensorflow as tf
from six import string_types
from tensorflow.python.keras import Model, Sequential
from tensorflow.python.keras import layers as layer_module
from tensorflow.python.keras.layers import Dense, Lambda
from tensorflow_probability.python.distributions import Distribution
from tensorflow_probability.python.layers import DistributionLambda
from tensorflow_probability.python.layers.distribution_layer import (
    DistributionLambda, _get_convert_to_tensor_fn, _serialize,
    _serialize_function)

from odin.bay.distribution_alias import _dist_mapping, parse_distribution
from odin.bay.distribution_layers import VectorDeterministicLayer
from odin.bay.helpers import Statistic, kl_divergence
from odin.networks.distribution_util_layers import Moments, Sampling

__all__ = ['DenseDeterministic', 'DenseDistribution']


class DenseDeterministic(Dense):
  """ Similar to `keras.Dense` layer but return a
  `tensorflow_probability.Deterministic` distribution to represent the output,
  hence, make it compatible to probabilistic frameworks
  """

  def __init__(self,
               units,
               activation=None,
               use_bias=True,
               kernel_initializer='glorot_uniform',
               bias_initializer='zeros',
               kernel_regularizer=None,
               bias_regularizer=None,
               activity_regularizer=None,
               kernel_constraint=None,
               bias_constraint=None,
               **kwargs):
    super(DenseDeterministic,
          self).__init__(units=units,
                         activation=activation,
                         use_bias=use_bias,
                         kernel_initializer=kernel_initializer,
                         bias_initializer=bias_initializer,
                         kernel_regularizer=kernel_regularizer,
                         bias_regularizer=bias_regularizer,
                         activity_regularizer=activity_regularizer,
                         kernel_constraint=kernel_constraint,
                         bias_constraint=bias_constraint,
                         **kwargs)

  def call(self, inputs, **kwargs):
    outputs = super(DenseDeterministic, self).call(inputs)
    return VectorDeterministicLayer()(outputs)


class DenseDistribution(Dense):
  r""" Using `Dense` layer to parameterize the tensorflow_probability
  `Distribution`

  Arguments:
    units : `int`
      number of output units.
    posterior : the posterior distribution, a distribution alias or Distribution
      type can be given for later initialization (Default: 'normal').
    prior : {`None`, `tensorflow_probability.Distribution`}
      prior distribution, used for calculating KL divergence later.
    use_bias : `bool` (default=`True`)
      enable biases for the Dense layers
    posterior_kwargs : `dict`. Keyword arguments for initializing the posterior
      `DistributionLambda`

  Return
  ------
  `tensorflow_probability.Distribution`
  """

  def __init__(self,
               event_shape=(),
               posterior='normal',
               prior=None,
               posterior_kwargs={},
               dropout=0.0,
               activation='linear',
               use_bias=True,
               kernel_initializer='glorot_uniform',
               bias_initializer='zeros',
               kernel_regularizer=None,
               bias_regularizer=None,
               activity_regularizer=None,
               kernel_constraint=None,
               bias_constraint=None,
               **kwargs):
    assert prior is None or isinstance(prior, Distribution), \
      "prior can be None or instance of tensorflow_probability.Distribution"
    # duplicated event_shape or event_size in posterior_kwargs
    posterior_kwargs = dict(posterior_kwargs)
    if 'event_shape' in posterior_kwargs:
      event_shape = posterior_kwargs.pop('event_shape')
    if 'event_size' in posterior_kwargs:
      event_shape = posterior_kwargs.pop('event_size')
    if 'convert_to_tensor_fn' in posterior_kwargs:
      posterior_kwargs.pop('convert_to_tensor_fn')
    # process the posterior
    post_layer, _ = parse_distribution(posterior)
    self._n_mcmc = [1]
    self._posterior_layer = post_layer(event_shape,
                                       convert_to_tensor_fn=partial(
                                           Distribution.sample,
                                           sample_shape=self._n_mcmc),
                                       **posterior_kwargs)
    # create layers
    self._posterior = posterior
    self._prior = prior
    self._event_shape = event_shape
    self._posterior_kwargs = posterior_kwargs
    self._dropout = dropout
    super(DenseDistribution,
          self).__init__(units=post_layer.params_size(event_shape),
                         activation=activation,
                         use_bias=use_bias,
                         kernel_initializer=kernel_initializer,
                         bias_initializer=bias_initializer,
                         kernel_regularizer=kernel_regularizer,
                         bias_regularizer=bias_regularizer,
                         activity_regularizer=activity_regularizer,
                         kernel_constraint=kernel_constraint,
                         bias_constraint=bias_constraint,
                         **kwargs)
    # basics
    self._last_distribution = None

  def get_config(self):
    config = super().get_config()
    config['event_shape'] = self._event_shape
    config['posterior'] = self._posterior
    config['prior'] = self._prior
    config['dropout'] = self._dropout
    config['posterior_kwargs'] = self._posterior_kwargs
    return config

  @property
  def prior(self):
    return self._prior

  @property
  def distribution_layer(self):
    return self._posterior_layer

  @property
  def posterior(self):
    """ Return the last parametrized distribution, i.e. the result from `call`
    """
    return self._last_distribution

  def mean(self, x):
    dist = self.call(x)
    y = Moments(mean=True, variance=False)(dist)
    setattr(y, '_distribution', dist)
    self._last_distribution = y._distribution
    return y

  def variance(self, x):
    dist = self.call(x)
    y = Moments(mean=False, variance=True)(dist)
    setattr(y, '_distribution', dist)
    self._last_distribution = y._distribution
    return y

  def stddev(self, x):
    v = self.variance(x)
    y = Lambda(tf.math.sqrt)(v)
    setattr(y, '_distribution', v._distribution)
    return y

  def call(self, x, training=None, n_mcmc=1):
    if n_mcmc is None:
      n_mcmc = 1
    params = super().call(x)
    if self._dropout > 0:
      params = bk.dropout(params, p_drop=self._dropout, training=training)
    # modifying the Lambda to return given number of n_mcmc samples
    self._n_mcmc[0] = n_mcmc
    posterior = self._posterior_layer(params, training=training)
    self._last_distribution = posterior
    return posterior

  def kl_divergence(self,
                    x=None,
                    training=None,
                    prior=None,
                    analytic_kl=True,
                    n_mcmc=1):
    r"""
    Arguments:
      x : `Tensor` (optional) . The Input for parametrizing the distribution,
        if not given, used the last result from `call`
      prior : instance of `tensorflow_probability.Distribution`
        prior distribution of the latent
      analytic_kl : `bool` (default=`True`). Using closed form solution for
        calculating divergence, otherwise, sampling with MCMC
      n_mcmc : `int` (default=`1`)
        number of MCMC sample if `analytic_kl=False`

    Return:
      kullback_divergence : Tensor [n_mcmc, batch_size, ...]
    """
    if n_mcmc is None:
      n_mcmc = 1
    if prior is None:
      prior = self._prior
    assert isinstance(prior, Distribution), "prior is not given!"

    if x is not None:
      posterior = self.call(x, training=training)
    else:
      posterior = self.posterior
    if posterior is None:
      raise RuntimeError(
          "DenseDistribution must be called to create the distribution before "
          "calculating the kl-divergence.")

    kullback_div = kl_divergence(q=posterior,
                                 p=prior,
                                 use_analytic_kl=bool(analytic_kl),
                                 q_sample=int(n_mcmc),
                                 auto_remove_independent=True)
    if analytic_kl:
      kullback_div = tf.expand_dims(kullback_div, axis=0)
      if n_mcmc > 1:
        ndims = kullback_div.shape.ndims
        kullback_div = tf.tile(kullback_div, [n_mcmc] + [1] * (ndims - 1))
    return kullback_div

  def log_prob(self, x, training=None, n_mcmc=1):
    """ Calculating the log probability (i.e. log likelihood) """
    return self.call(x, training=training, n_mcmc=n_mcmc).log_prob(x)

  def __repr__(self):
    return self.__str__()

  def __str__(self):
    return '<DenseDistribution units:%d #params:%g posterior:%s prior:%s>' %\
      (self.units, self._params_size / self.units,
       self.layers[-1].__class__.__name__,
       self.prior.__class__.__name__)
