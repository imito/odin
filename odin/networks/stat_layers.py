from __future__ import absolute_import, division, print_function

from typing import Callable, Optional, Type, Union

import tensorflow as tf
from tensorflow.python.keras import Model, Sequential
from tensorflow.python.keras import layers as layer_module
from tensorflow.python.keras.layers import Dense, Lambda
from tensorflow_probability.python.distributions import Distribution
from tensorflow_probability.python.layers import DistributionLambda

from odin.bay.distribution_layers import DeterministicLayer
from odin.bay.helpers import Statistic, kl_divergence
from odin.networks.distribution_util_layers import Moments, Sampling

__all__ = ['DeterministicDense', 'DistributionDense']


class DeterministicDense(Sequential):

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
               name=None):
    layers = [
        Dense(
            units,
            activation=activation,
            use_bias=use_bias,
            kernel_initializer=kernel_initializer,
            bias_initializer=bias_initializer,
            kernel_regularizer=kernel_regularizer,
            bias_regularizer=bias_regularizer,
            activity_regularizer=activity_regularizer,
            kernel_constraint=kernel_constraint,
            bias_constraint=bias_constraint,
        ),
        DeterministicLayer(vectorized=True)
    ]
    super(DeterministicDense, self).__init__(layers=layers, name=name)
    self._config = dict(locals())
    del self._config['self']
    del self._config['__class__']
    del self._config['layers']
    self._config['name'] = self.name

  def get_config(self):
    return self._config

  @classmethod
  def from_config(cls, config, custom_objects=None):
    return cls(**config)


class DistributionDense(Model):
  """ DistributionDense

  Parameters
  ----------
  units : int
    number of output units.
  posterior : {`DistributionLambda`, `callable`, `type`}
    posterior distribution, the class or a callable can be given for later
    initialization.
  prior : {`None`, `tensorflow_probability.Distribution`}
    prior distribution, used for calculating KL divergence later.
  use_bias : `bool` (default=`True`)

  call_mode : `odin.bay.helpers.Statistic` (default=Statistic.SAMPLE)

  name : `str` (default='DistributionDense')

  Return
  ------
  sample, mean, variance, stddev : [n_samples, batch_size, units]
    depend on the `call_mode`, multiple statistics could be returned
  """

  def __init__(self,
               units,
               posterior: Union[DistributionLambda, Type[DistributionLambda]],
               prior: Optional[Distribution] = None,
               activation='linear',
               use_bias=True,
               call_mode: Statistic = Statistic.SAMPLE,
               name="DistributionDense"):
    super(DistributionDense, self).__init__(name=name)
    assert prior is None or isinstance(prior, Distribution), \
     "prior can be None or instance of tensorflow_probability.Distribution"
    assert isinstance(call_mode, Statistic), \
      "call_mode must be instance of odin.bay.helpers.Statistic"

    self._units = int(units)
    self._use_bias = bool(use_bias)
    self._posterior = posterior
    self._prior = prior
    self._call_mode = call_mode

    if isinstance(posterior, DistributionLambda):
      pass
    elif isinstance(posterior, type) and issubclass(posterior,
                                                    DistributionLambda):
      posterior = posterior(self.units)
    elif isinstance(posterior, Callable):
      posterior = posterior(self.units)
      assert isinstance(posterior, DistributionLambda), \
        "The callable must return instance of DistributionLambda, but given: %s" \
          % (str(type(posterior)))
    else:
      raise ValueError("No support for posterior of type: %s" %
                       str(type(posterior)))

    params_size = posterior.params_size(self.units)
    layers = [
        Dense(params_size, activation=activation, use_bias=bool(use_bias)),
        posterior
    ]
    self._distribution = Sequential(layers, name="Sequential")
    self._last_distribution = None

  def get_config(self):
    config = {
        'name': self.name,
        'units': self._units,
        'posterior': self._posterior,
        'prior': self._prior,
        'use_bias': self._use_bias,
        'call_mode': self._call_mode,
        'build_input_shape': self._distribution._build_input_shape
    }
    return config

  @classmethod
  def from_config(cls, config, custom_objects=None):
    build_input_shape = config.pop('build_input_shape')
    model = cls(**config)
    if not model.inputs and build_input_shape is not None:
      model.build(build_input_shape)
    return model

  @property
  def prior(self):
    return self._prior

  @property
  def posterior(self):
    """ Return the last parametrized distribution, i.e. the result from `call`
    """
    return self._last_distribution

  @property
  def units(self):
    return self._units

  def _apply_distribution(self, x):
    if hasattr(x, '_distribution') and \
      x._distribution == self._last_distribution:
      dist = x._distribution
    else:
      dist = self._distribution(x)
    return dist

  def mean(self, x):
    dist = self._apply_distribution(x)
    y = Moments(mean=True, variance=False)(dist)
    setattr(y, '_distribution', dist)
    self._last_distribution = y._distribution
    return y

  def variance(self, x):
    dist = self._apply_distribution(x)
    y = Moments(mean=False, variance=True)(dist)
    setattr(y, '_distribution', dist)
    self._last_distribution = y._distribution
    return y

  def stddev(self, x):
    v = self.variance(x)
    y = Lambda(tf.math.sqrt)(v)
    setattr(y, '_distribution', v._distribution)
    return y

  def sample(self, x, n_samples=1):
    if n_samples is None or n_samples <= 0:
      n_samples = 1
    dist = self._apply_distribution(x)
    y = Sampling(n_samples=n_samples)(dist)
    setattr(y, '_distribution', dist)
    self._last_distribution = y._distribution
    return y

  def build(self, input_shape):
    self._distribution.build(input_shape)
    return super(DistributionDense, self).build(input_shape)

  def call(self, x, training=None, n_samples=1, mode=None):
    """
    Parameters
    ----------
    x : {`numpy.ndarray`, `tensorflow.Tensor`}

    training : {`None`, `bool`} (default=`None`)

    n_samples : {`None`, `int`} (default=`1`)

    mode : {`None`, `odin.bay.helpers.Statistic`} (default=`None`)
      decide which of the statistics will be return from the distribution,
      this value will overide the default value of the class
    """
    dtype = tuple(set([w.dtype for w in self._distribution.weights]))[0]
    if x.dtype != dtype:
      raise RuntimeError(
          "Given input with %s, but the layers were created with %s" %
          (str(x.dtype), str(dtype)))

    results = []
    variance = None
    call_mode = self._call_mode if not isinstance(mode, Statistic) else mode
    # special case only need the distribution
    if Statistic.DIST == call_mode:
      dist = self._distribution(x)
      self._last_distribution = dist
      return dist

    if Statistic.SAMPLE in call_mode:
      results.append(self.sample(x, n_samples=n_samples))
    if Statistic.MEAN in call_mode:
      results.append(self.mean(x))
    if Statistic.VAR in call_mode or Statistic.STDDEV in call_mode:
      variance = self.variance(x)
      if Statistic.VAR in call_mode:
        results.append(variance)
      if Statistic.STDDEV in call_mode:
        y = Lambda(tf.math.sqrt)(variance)
        setattr(y, '_distribution', variance._distribution)
        results.append(y)
    if Statistic.DIST in call_mode:
      assert len(results) > 0
      results.append(results[0]._distribution)
    return results[0] if len(results) == 1 else tuple(results)

  def kl_divergence(self, x=None, prior=None, analytic_kl=True, n_samples=1):
    """
    Parameters
    ---------
    x : `Tensor`
      optional input for parametrizing the distribution, if not given,
      used the last result from `call`

    prior : instance of `tensorflow_probability.Distribution`
      prior distribution of the latent

    analytic_kl : `bool` (default=`True`)
      using closed form solution for calculating divergence,
      otherwise, sampling with MCMC

    n_samples : `int` (default=`1`)
      number of MCMC sample if `analytic_kl=False`

    Return
    ------
    kullback_divergence : Tensor [n_samples, batch_size, ...]
    """
    if n_samples is None:
      n_samples = 1
    if prior is None:
      prior = self._prior
    assert isinstance(prior, Distribution), "prior is not given!"
    if x is not None:
      self(x, mode=Statistic.DIST)
    if self.posterior is None:
      raise RuntimeError(
          "DistributionDense must be called to create the distribution before "
          "calculating the kl-divergence.")
    kullback_div = kl_divergence(q=self.posterior,
                                 p=prior,
                                 use_analytic_kl=bool(analytic_kl),
                                 q_sample=int(n_samples),
                                 auto_remove_independent=True)
    if analytic_kl:
      kullback_div = tf.expand_dims(kullback_div, axis=0)
      if n_samples > 1:
        ndims = kullback_div.shape.ndims
        kullback_div = tf.tile(kullback_div, [n_samples] + [1] * (ndims - 1))
    return kullback_div

  def log_prob(self, x):
    """ Calculating the log probability (i.e. log likelihood) """
    assert self.units == x.shape[-1], \
      "Number of features mismatch, units=%d  input_shape=%s" % \
        (self.units, str(x.shape))
    if self.posterior is None:
      raise RuntimeError(
          "DistributionDense must be called to create the distribution before "
          "calculating the log-likelihood.")
