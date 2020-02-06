from __future__ import absolute_import, division, print_function

import inspect
import os
import pickle
import sys
import warnings
from collections import defaultdict
from numbers import Number

import numpy as np
import tensorflow as tf
from tensorflow.python import keras
from tensorflow.python.eager.def_function import Function
from tensorflow.python.keras import Model
from tensorflow.python.keras.layers import Layer
from tensorflow.python.util import nest

from odin.utils import as_tuple

__all__ = [
    'copy_keras_metadata', 'has_keras_meta', 'add_trainable_weights',
    'layer2text'
]


def has_keras_meta(tensor):
  return hasattr(tensor, '_keras_history') and hasattr(tensor, '_keras_mask')


def copy_keras_metadata(keras_tensor, *new_tensors):
  if not hasattr(keras_tensor, '_keras_history') or \
  not hasattr(keras_tensor, '_keras_mask'):
    pass
  else:
    new_tensors = nest.flatten(new_tensors)
    history = keras_tensor._keras_history
    mask = keras_tensor._keras_mask
    for t in new_tensors:
      setattr(t, '_keras_history', history)
      setattr(t, '_keras_mask', mask)
  return new_tensors[0] if len(new_tensors) == 1 else new_tensors


def add_trainable_weights(layer, *variables):
  from odin.backend import is_variable
  variables = nest.flatten(variables)
  assert all(is_variable(v) for v in variables), \
  "All objects from variables must be instance of tensorflow.Variable"

  assert isinstance(layer, Layer), \
  "layer must be instance of tensorflow.python.keras.layers.Layer"

  variables = [v for v in variables if v not in layer._trainable_weights]
  layer._trainable_weights = layer._trainable_weights + variables
  return layer


def layer2text(layer):
  assert isinstance(layer, keras.layers.Layer)
  from tensorflow.python.keras.layers.convolutional import Conv
  text = str(layer)
  name = '[%s:"%s"]' % (layer.__class__.__name__, layer.name)
  if isinstance(layer, keras.layers.Dense):
    text = '%sunits:%d bias:%s activation:%s' % \
      (name, layer.units, layer.use_bias, layer.activation.__name__)
  elif isinstance(layer, Conv):
    text = '%sfilter:%d kernel:%s stride:%s dilation:%s pad:%s bias:%s activation:%s' % \
      (name, layer.filters, layer.kernel_size, layer.strides,
       layer.dilation_rate, layer.padding, layer.use_bias,
       layer.activation.__name__)
  elif isinstance(layer, keras.layers.Activation):
    text = '%s %s' % (name, layer.activation.__name__)
  elif isinstance(layer, keras.layers.Dropout):
    text = '%s p=%.2f' % (name, layer.rate)
  elif isinstance(layer, keras.layers.BatchNormalization):
    text = '[%s:"%s"]axis=%s center:%s scale:%s trainable:%s' % \
      ('BatchRenorm' if layer.renorm else 'BatchNorm', layer.name,
       [i for i in tf.nest.flatten(layer.axis)],
       layer.center, layer.scale, layer.trainable)
  elif isinstance(layer, keras.layers.Lambda):
    spec = inspect.getfullargspec(layer.function)
    kw = dict(layer.arguments)
    if spec.defaults is not None:
      kw.update(spec.defaults)
    text = '%s <%s>(%s) default:%s' % \
      (name, layer.function.__name__,
       ', '.join(spec.args), kw)
  elif isinstance(layer, keras.layers.Reshape):
    text = '%s %s' % (name, layer.target_shape)
  elif isinstance(layer, keras.layers.Flatten):
    text = '%s %s' % (name, layer.data_format)
  return text


# ===========================================================================
# For training schedule
# ===========================================================================
_BEST_WEIGHTS = {}
_BEST_OPTIMIZER = {}
_CHECKPOINT_MANAGER = {}


class Trainer(object):
  r"""
  """
  SIGNAL_TERMINATE = '__signal_terminate__'
  SIGNAL_BEST = '__signal_best__'

  @staticmethod
  def save_weights(models, optimizers=None):
    r""" Fast store the weights of models in-memory, only the lastest
    version of the weights are kept """
    for i in tf.nest.flatten(models):
      if not isinstance(i, keras.layers.Layer):
        continue
      _BEST_WEIGHTS[id(i)] = i.get_weights()
    if optimizers is not None:
      for i in tf.nest.flatten(optimizers):
        if isinstance(i, tf.optimizers.Optimizer):
          _BEST_OPTIMIZER[id(i)] = i.get_weights()

  @staticmethod
  def restore_weights(models, optimizers=None):
    r""" Fast recover the weights of models, only the lastest version
    of the weights are kept """
    for i in tf.nest.flatten(models):
      if not isinstance(i, keras.layers.Layer):
        continue
      if id(i) in _BEST_WEIGHTS:
        i.set_weights(_BEST_WEIGHTS[id(i)])
    if optimizers is not None:
      for i in tf.nest.flatten(optimizers):
        if isinstance(i, tf.optimizers.Optimizer):
          if id(i) in _BEST_OPTIMIZER:
            i.set_weights(_BEST_OPTIMIZER[id(i)])

  @staticmethod
  def save_checkpoint(dir_path,
                      models,
                      optimizers=None,
                      trainer=None,
                      max_to_keep=5):
    r""" Save checkpoint """
    if optimizers is None:
      optimizers = []
    optimizers = tf.nest.flatten(optimizers)
    assert all(isinstance(opt, tf.optimizers.Optimizer) for opt in optimizers), \
      "optimizer must be instance of tf.optimizers.Optimizer"
    dir_path = os.path.abspath(dir_path)
    if not os.path.exists(dir_path):
      os.mkdir(dir_path)
    elif os.path.isfile(dir_path):
      raise ValueError("dir_path must be path to a folder")
    models = [
        i for i in tf.nest.flatten(models)
        if isinstance(i, (tf.Variable, keras.layers.Layer))
    ]
    footprint = dir_path + \
      ''.join(sorted([str(id(i)) for i in optimizers])) + \
      ''.join(sorted([str(id(i)) for i in models]))
    if footprint in _CHECKPOINT_MANAGER:
      manager, cp = _CHECKPOINT_MANAGER[footprint]
    else:
      models = {"model%d" % idx: m for idx, m in enumerate(models)}
      models.update(
          {"optimizer%d" % idx: m for idx, m in enumerate(optimizers)})
      cp = tf.train.Checkpoint(**models)
      manager = tf.train.CheckpointManager(cp,
                                           directory=dir_path,
                                           max_to_keep=max_to_keep)
      _CHECKPOINT_MANAGER[footprint] = (manager, cp)
    manager.save()
    with open(os.path.join(dir_path, 'optimizers.pkl'), 'wb') as f:
      pickle.dump(
          [(opt.__class__.__name__, opt.get_config()) for opt in optimizers], f)
    with open(os.path.join(dir_path, 'max_to_keep'), 'wb') as f:
      pickle.dump(max_to_keep, f)
    if trainer is not None:
      with open(os.path.join(dir_path, 'trainer.pkl'), 'wb') as f:
        pickle.dump(trainer, f)

  @staticmethod
  def restore_checkpoint(dir_path, models=None, optimizers=None, index=-1):
    r""" Restore saved checkpoint

    Returns:
      models : list of `keras.Model` or `tf.Variable`
      optimizers : list of `tf.optimizers.Optimizer`
      trainer : `odin.backend.Trainer` or `None`
    """
    dir_path = os.path.abspath(dir_path)
    kwargs = {}
    if not os.path.exists(dir_path):
      os.mkdir(dir_path)
    elif os.path.isfile(dir_path):
      raise ValueError("dir_path must be path to a folder")
    # check models and optimizers
    if models is None and optimizers is None:
      footprint = [name for name in _CHECKPOINT_MANAGER \
                   if dir_path in name]
      if len(footprint) == 0:
        raise ValueError("Cannot find checkpoint information for path: %s" %
                         dir_path)
      footprint = footprint[0]
      manager, cp = _CHECKPOINT_MANAGER[footprint]
    # given models
    elif models is not None:
      models = [
          i for i in tf.nest.flatten(models)
          if isinstance(i, (tf.Variable, keras.layers.Layer))
      ]
      models_ids = ''.join(sorted([str(id(i)) for i in models]))
      with open(os.path.join(dir_path, 'max_to_keep'), 'rb') as f:
        max_to_keep = pickle.load(f)
      # not provided optimizers
      if optimizers is None:
        footprint = [name for name in _CHECKPOINT_MANAGER \
                     if dir_path in name and models_ids in name]
        if len(footprint) > 0:
          manager, cp = _CHECKPOINT_MANAGER[footprint[0]]
        else:  # create optimizer
          with open(os.path.join(dir_path, 'optimizers.pkl'), 'rb') as f:
            optimizers = pickle.load(f)
          optimizers = [
              tf.optimizers.get(name).from_config(config)
              for name, config in optimizers
          ]
          kwargs = {"model%d" % idx: m for idx, m in enumerate(models)}
          kwargs.update(
              {"optimizer%d" % idx: m for idx, m in enumerate(optimizers)})
          cp = tf.train.Checkpoint(**kwargs)
          manager = tf.train.CheckpointManager(cp,
                                               directory=dir_path,
                                               max_to_keep=max_to_keep)
      # both models and optimizers available
      else:
        optimizers = tf.nest.flatten(optimizers)
        optimizers_ids = ''.join(sorted([str(id(i)) for i in optimizers]))
        footprint = [name for name in _CHECKPOINT_MANAGER \
                     if dir_path in name and \
                       optimizers_ids in name and \
                         models_ids in name]
        if len(footprint) > 0:
          manager, cp = _CHECKPOINT_MANAGER[footprint[0]]
        else:
          kwargs = {"model%d" % idx: m for idx, m in enumerate(models)}
          kwargs.update(
              {"optimizer%d" % idx: m for idx, m in enumerate(optimizers)})
          cp = tf.train.Checkpoint(**kwargs)
          manager = tf.train.CheckpointManager(cp,
                                               directory=dir_path,
                                               max_to_keep=max_to_keep)
    # only given optimizers, cannot do anything
    else:
      raise ValueError("Not support for models=%s optimizers=%s" %
                       (str(models), str(optimizers)))
    cp.restore(manager.checkpoints[int(index)])
    # restore trainer (if exist)
    trainer = None
    trainer_path = os.path.join(dir_path, 'trainer.pkl')
    if os.path.exists(trainer_path):
      with open(trainer_path, 'rb') as f:
        trainer = pickle.load(f)
    # return
    models = [getattr(cp, k) for k in sorted(dir(cp)) if 'model' in k[:5]]
    optimizers = [
        getattr(cp, k) for k in sorted(dir(cp)) if 'optimizer' in k[:9]
    ]
    return models, optimizers, trainer

  @staticmethod
  def early_stop(losses,
                 threshold=0.2,
                 progress_length=5,
                 patience=5,
                 min_epoch=-np.inf,
                 terminate_on_nan=True,
                 verbose=0):
    r""" Early stopping based on generalization loss and the three rules:
      - Stop when generalization error exceeds threshold in a number of
          successive steps.
      - Stop as soon as the generalization loss exceeds a certain threshold.
      - Supress stopping if the training is still progress rapidly.

    Generalization loss: `GL(t) = losses[-1] / min(losses[:-1]) - 1`

    Progression: `PG(t) = 10 * sum(L) / (k * min(L))` where `L = losses[-k:]`

    The condition for early stopping: `GL(t) / PG(t) >= threshold`

    Arugments:
      losses : List of loss values (smaller is better)
      threshold : Float. Determine by `generalization_error / progression`
      progress_length : Integer. Number of steps to look into the past for
        estimating the training progression. If smaller than 2, turn-off
        progression for early stopping.
      patience : Integer. Number of successive steps that yield loss exceeds
        `threshold / 2` until stopping. If smaller than 2, turn-off patience
        for early stopping
      min_epoch: Minimum number of epoch until early stop kicks in.
        Note, all the metrics won't be updated until the given epoch.
      terminate_on_nan : Boolean. Terminate the training progress if NaN or Inf
        appear in the losses.

    Return:
      Trainer.SIGNAL_TERMINATE : stop training
      Trainer.SIGNAL_BEST : best model achieved

    Reference:
      Prechelt, L. (1998). "Early Stopping | but when?".
    """
    if len(losses) == 0:
      return
    if terminate_on_nan and (np.isnan(losses[-1]) or np.isinf(losses[-1])):
      return Trainer.SIGNAL_TERMINATE
    if len(losses) < max(2., min_epoch):
      tf.print("[EarlyStop] First 2 warmup epochs.")
      return Trainer.SIGNAL_BEST
    current = losses[-1]
    best = np.min(losses[:-1])
    # patience
    if patience > 1 and len(losses) > patience and \
      all((i / best - 1) >= (threshold / 2) for i in losses[-patience:]):
      if verbose:
        tf.print("[EarlyStop] %d successive step without enough improvement." %
                 patience)
      return Trainer.SIGNAL_TERMINATE
    # generalization error (smaller is better)
    generalization = current / best - 1
    if generalization <= 0:
      if verbose:
        tf.print("[EarlyStop] Best model, generalization improved: %.4f" %
                 (best / current - 1.))
      return Trainer.SIGNAL_BEST
    # progression (bigger is better)
    if progress_length > 1:
      progress = losses[-progress_length:]
      progression = 10 * \
        (np.sum(progress) / (progress_length * np.min(progress)) - 1)
    else:
      progression = 1.
    # thresholding
    error = generalization / progression
    if error >= threshold:
      tf.print(
          "[EarlyStop] Exceed threshold:%.2f  generalization:%.4f progression:%.4f"
          % (threshold, generalization, progression))
      return Trainer.SIGNAL_TERMINATE

  @staticmethod
  def validate_optimize(func):
    assert callable(func), "optimize must be callable."
    if isinstance(func, Function):
      args = func.function_spec.arg_names
    else:
      args = inspect.getfullargspec(func).args
    template = ['tape', 'training', 'n_iter']
    assert 'tape' in args, \
      "tape (i.e. GradientTape) must be in arguments list of optimize function."
    assert all(a in template for a in args[1:]), \
      "optimize function must has the following arguments: %s; but given: %s"\
        % (template, args)
    return args

  @staticmethod
  def apply_gradients(tape, optimizer, loss, model_or_weights):
    r"""
    Arguments:
      tape : GradientTape (optional). If not given, no optimization is
        performed.
      optimizer : Instance of `tf.optimizers.Optimizer`.
      loss : a Tensor value with rank 0.
      model_or_weights : List of keras.layers.Layer or tf.Variable, only
        `trainable` variables are optimized.

    Returns:
      gradients : List of gradient Tensor
    """
    if tape is None:
      return
    assert isinstance(tape, tf.GradientTape)
    assert isinstance(optimizer, tf.optimizers.Optimizer)
    model_or_weights = tf.nest.flatten(model_or_weights)
    weights = []
    for i in model_or_weights:
      if not i.trainable:
        continue
      if isinstance(i, tf.Variable):
        weights.append(i)
      else:
        weights += i.trainable_variables
    with tape.stop_recording():
      grads = tape.gradient(loss, weights)
      optimizer.apply_gradients(grads_and_vars=zip(grads, weights))
    return grads

  @staticmethod
  def prepare(ds,
              preprocess=None,
              postprocess=None,
              batch_size=128,
              epochs=1,
              cache='',
              drop_remainder=False,
              shuffle=None,
              parallel_preprocess=0,
              parallel_postprocess=0):
    r""" A standarlized procedure for preparing `tf.data.Dataset` for training
    or evaluation.

    Arguments:
      ds : `tf.data.Dataset`
      preprocess : Callable (optional). Call at the beginning when a single
        example is fetched.
      postprocess : Callable (optional). Called after `.batch`, which processing
        the whole minibatch.
      parallel_preprocess, parallel_postprocess : Integer.
        - if value is 0, process sequentially
        - if value is -1, using `tf.data.experimental.AUTOTUNE`
        - if value > 0, explicitly specify the number of process for running
            map task
    """
    parallel_preprocess = None if parallel_preprocess == 0 else \
      (tf.data.experimental.AUTOTUNE if parallel_preprocess == -1 else
       int(parallel_preprocess))
    parallel_postprocess = None if parallel_postprocess == 0 else \
      (tf.data.experimental.AUTOTUNE if parallel_postprocess == -1 else
       int(parallel_postprocess))

    if not isinstance(ds, tf.data.Dataset):
      ds = tf.data.Dataset.from_tensor_slices(ds)

    if shuffle:
      if isinstance(shuffle, Number):
        ds = ds.shuffle(buffer_size=int(shuffle), reshuffle_each_iteration=True)
      else:
        ds = ds.shuffle(buffer_size=10000, reshuffle_each_iteration=True)

    if preprocess is not None:
      ds = ds.map(preprocess, num_parallel_calls=parallel_preprocess)
      if cache is not None:
        ds = ds.cache(cache)
    ds = ds.repeat(epochs).batch(batch_size, drop_remainder=drop_remainder)

    if postprocess is not None:
      ds = ds.map(postprocess, num_parallel_calls=parallel_postprocess)
    ds = ds.prefetch(tf.data.experimental.AUTOTUNE)
    return ds

  #######################################################
  def __init__(self):
    super().__init__()
    self.n_iter = tf.Variable(0,
                              dtype=tf.float32,
                              trainable=False,
                              name='n_iter')
    self.train_loss = []
    self.train_metrics = defaultdict(list)
    # store the average value
    self.valid_loss = []
    self.valid_metrics = defaultdict(list)
    # store all iterations results per epoch
    self.valid_loss_epoch = []
    self.valid_metrics_epoch = []

  def __getstate__(self):
    return self.n_iter.numpy(), self.train_loss, self.train_metrics, \
      self.valid_loss, self.valid_metrics

  def __setstate__(self, states):
    self.n_iter, self.train_loss, self.train_metrics, \
      self.valid_loss, self.valid_metrics = states
    self.n_iter = tf.Variable(self.n_iter,
                              dtype=tf.float32,
                              trainable=False,
                              name='n_iter')

  def fit(self,
          ds,
          optimize,
          valid_ds=None,
          valid_freq=1000,
          persistent_tape=True,
          autograph=True,
          logging_interval=2,
          log_path=None,
          max_iter=-1,
          callback=lambda: None):
    r""" A simplified fitting API

    Arugments:
      ds : tf.data.Dataset. Training dataset.
      optimize : Callable. Optimization function, return loss and a list of
        metrics. The input arguments must be:
          - ('inputs', 'tape', 'training', 'n_iter');
        and the function returns:
          - ('loss': `tf.Tensor`, 'metrics': `dict(str, tf.Tensor)`)
      valid_ds : tf.data.Dataset. Validation dataset
      valid_freq : Integer. The frequency of validation task, based on number
        of iteration in training.
      persistent_tape : Boolean. Using persistent GradientTape, so multiple
        call to gradient is feasible.
      autograph : Boolean. Enable static graph for the `optimize` function.
      logging_interval : Scalar. Interval for print out log information
        (in second).
      max_iter : An Interger or `None`. Maximum number of iteration for
        training. If `max_iter <= 0`, iterate the training data until the end.
      callback : Callable. The callback will be called after every fixed number
        of iteration according to `valid_freq`.
    """
    autograph = int(autograph)
    output_stream = sys.stdout
    if log_path is not None:
      output_stream = 'file://%s' % log_path
    ### Prepare the data
    assert isinstance(ds, tf.data.Dataset), \
      'ds must be instance of tf.data.Datasets'
    if valid_ds is not None:
      assert isinstance(valid_ds, tf.data.Dataset), \
        'valid_ds must be instance of tf.data.Datasets'
    valid_freq = int(valid_freq)
    ### optimizing function
    optimize_args = Trainer.validate_optimize(optimize)

    ### helper function for training iteration
    def step(n_iter, inputs, training):
      kw = dict()
      if 'n_iter' in optimize_args:
        kw['n_iter'] = n_iter
      if 'training' in optimize_args:
        kw['training'] = training
      if training:  # for training
        with tf.GradientTape(persistent=persistent_tape) as tape:
          loss, metrics = optimize(inputs, tape=tape, **kw)
      else:  # for validation
        loss, metrics = optimize(inputs, tape=None, **kw)
      assert isinstance(metrics, dict), "Metrics must be instance of dictionary"
      return loss, metrics

    if autograph and not isinstance(optimize, Function):
      step = tf.function(step)

    def valid():
      epoch_loss = []
      epoch_metrics = defaultdict(list)
      start_time = tf.timestamp()
      last_it = 0.
      for it, inputs in enumerate(valid_ds.repeat(1)):
        it = tf.cast(it, tf.float32)
        _loss, _metrics = step(it, inputs, training=False)
        assert isinstance(_metrics, dict), \
          "Metrics must be instance of dictionary"
        # store for calculating average
        epoch_loss.append(_loss)
        for k, v in _metrics.items():
          epoch_metrics[k].append(v)
        # print log
        end_time = tf.timestamp()
        if end_time - start_time >= logging_interval:
          it_per_sec = tf.cast(
              (it - last_it) / tf.cast(end_time - start_time, tf.float32),
              tf.int32)
          tf.print(" [Valid] #",
                   it + 1,
                   " ",
                   it_per_sec,
                   "(it/s)",
                   sep="",
                   output_stream=output_stream)
          start_time = tf.timestamp()
          last_it = it
      self.valid_loss_epoch.append(epoch_loss)
      self.valid_metrics_epoch.append(epoch_metrics)
      return tf.reduce_mean(epoch_loss, axis=0), \
        {k: tf.reduce_mean(v, axis=0) for k, v in epoch_metrics.items()}

    def train():
      total_time = 0.
      start_time = tf.timestamp()
      last_iter = tf.identity(self.n_iter)
      total_iter = 0
      for inputs in ds:
        self.n_iter.assign_add(1.)
        # ====== validation ====== #
        if self.n_iter % valid_freq == 0:
          if valid_ds is not None:
            total_time += tf.cast(tf.timestamp() - start_time, tf.float32)
            # finish the validation
            valid_loss, valid_metrics = valid()
            self.valid_loss.append(valid_loss.numpy())
            for k, v in valid_metrics.items():
              self.valid_metrics[k].append(v)
            tf.print(" [Valid#",
                     len(self.valid_loss),
                     "]",
                     " loss:%.4f" % valid_loss,
                     " metr:",
                     valid_metrics,
                     sep="",
                     output_stream=output_stream)
            # reset start_time
            start_time = tf.timestamp()
          # callback always called
          signal = callback()
          if signal == Trainer.SIGNAL_TERMINATE:
            break
        # ====== train ====== #
        loss, metrics = step(self.n_iter, inputs, training=True)
        self.train_loss.append(loss.numpy())
        for k, v in metrics.items():
          self.train_metrics[k].append(v)
        interval = tf.cast(tf.timestamp() - start_time, tf.float32)
        # ====== logging ====== #
        if interval >= logging_interval:
          total_time += interval
          tf.print("#",
                   self.n_iter,
                   " loss:%.4f" % loss,
                   " metr:",
                   metrics,
                   "  ",
                   tf.cast(total_time, tf.int32),
                   "(s) ",
                   tf.cast((self.n_iter - last_iter) / interval, tf.int32),
                   "(it/s)",
                   sep="",
                   output_stream=output_stream)
          start_time = tf.timestamp()
          last_iter = tf.identity(self.n_iter)
        # ====== check maximum iteration ====== #
        total_iter += 1
        if max_iter > 0 and total_iter >= max_iter:
          break

    ### train and return
    # train = tf.function(train)
    train()

  def plot_learning_curves(self,
                           path="/tmp/tmp.pdf",
                           summary_steps=100,
                           show_validation=True,
                           dpi=180):
    r""" Learning curves

    Arguments:
      path: save path for the figure
      summary_steps: number of iteration for estimating the mean and variance
    """
    from odin import visual as vs
    from matplotlib import pyplot as plt
    import seaborn as sns
    sns.set()
    summary_steps = as_tuple(summary_steps, N=2, t=int)
    is_validated = bool(len(self.valid_loss) > 0) and bool(show_validation)
    n_metrics = len(self.train_metrics)
    metrics_name = list(self.train_metrics.keys())
    metrics_name = ["loss"] + tf.nest.flatten(metrics_name)
    # create the figure
    ncol = 2 if is_validated else 1
    nrow = 1 + n_metrics
    fig = plt.figure(figsize=(8, nrow * 3))
    # prepare the results
    train = [self.train_loss] + \
      [self.train_metrics[i] for i in metrics_name[1:]]
    train_name = metrics_name
    if is_validated:
      valid = [tf.nest.flatten(self.valid_loss_epoch)] + \
        [tf.nest.flatten([epoch[i] for epoch in self.valid_metrics_epoch])
         for i in metrics_name[1:]]
      valid_name = ['val_' + i for i in metrics_name]
      all_data = zip([i for pair in zip(train_name, valid_name) for i in pair],
                     [i for pair in zip(train, valid) for i in pair])
    else:
      all_data = zip(train_name, train)
    # plotting
    subplots = []
    for idx, (name, data) in enumerate(all_data):
      ax = plt.subplot(nrow, ncol, idx + 1)
      subplots.append(ax)
      batch_size = summary_steps[1 if 'val_' == name[:4] else 0]
      if batch_size > len(data):
        warnings.warn(
            "Given summary_steps=%d but only has %d data points, skip plot!" %
            (batch_size, len(data)))
        return self
      data = [batch for batch in tf.data.Dataset.from_tensor_slices(\
        data).batch(batch_size)]
      data_avg = np.array([np.mean(i) for i in data])
      data_std = np.array([np.std(i) for i in data])
      data_min = np.min(data_avg)
      data_max = np.max(data_avg)
      plt.plot(data_avg, label='Avg.')
      plt.plot(np.argmin(data_avg),
               data_min,
               marker='o',
               color='green',
               alpha=0.5,
               label='Min:%.2f' % data_min)
      plt.plot(np.argmax(data_avg),
               data_max,
               marker='o',
               color='red',
               alpha=0.5,
               label='Max:%.2f' % data_max)
      plt.fill_between(np.arange(len(data_avg)),
                       data_avg + data_std,
                       data_avg - data_std,
                       alpha=0.3)
      plt.title(name)
      plt.legend(fontsize=10)
    # set the xlabels
    for ax, step in zip(subplots[-2:], summary_steps):
      ax.set_xlabel("#Iter * %d" % step)
    plt.tight_layout()
    vs.plot_save(path, figs=[fig], dpi=dpi, clear_all=False)
    plt.close(fig)
    return self

  def commit(self):
    # TODO
    pass

  def select(self):
    # TODO
    pass
