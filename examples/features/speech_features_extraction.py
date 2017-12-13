# ===========================================================================
# Without PCA:
#   ncpu=1:  16s
#   ncpu=2:  9.82
#   ncpu=4:  5.9s
#   ncpu=8:  4.3
#   ncpu=12: 4.0
# ===========================================================================
from __future__ import print_function, division, absolute_import
import matplotlib
matplotlib.use('Agg')

import os
os.environ['ODIN'] = 'float32,cpu=1,thread=1,gpu=1'
import sys
import shutil
import pickle

import numpy as np

from odin import visual as V, nnet as N
from odin.utils import ctext, unique_labels, Progbar, UnitTimer
from odin import fuel as F, utils, preprocessing as pp

# ===========================================================================
# set LOG path
# ===========================================================================
LOG_PATH = utils.get_logpath('speech_features_extraction.log',
                             override=True)
utils.stdio(LOG_PATH)
# ===========================================================================
# Const
# ===========================================================================
if False:
  audio = F.WDIGITS.get_dataset()
  filter_func = lambda x: len(x.split('_')[-1]) == 1
  key_func = lambda x: x.split('_')[-1]
else:
  audio = F.DIGITS.get_dataset()
  filter_func = lambda x: True
  key_func = lambda x: int(x[0])
print(audio)
all_files = sorted(list(audio['indices'].keys()))
labels_fn, labels = unique_labels(y=[f for f in all_files if filter_func(f)],
                                  key_func=key_func,
                                  return_labels=True)
print("Found %d (.wav) files" % len(all_files))
output_path = utils.get_datasetpath(name='digit')
figpath = '/tmp/digits'
# ===========================================================================
# Extractor
# ===========================================================================
padding = False
frame_length = 0.025
step_length = 0.005
dtype = 'float16'
bnf_network = N.models.BNF_2048_MFCC39()
extractors = pp.make_pipeline(steps=[
    pp.speech.AudioReader(sr_new=8000, best_resample=True,
                          remove_dc_n_dither=True, preemphasis=0.97,
                          dataset=audio),
    pp.speech.SpectraExtractor(frame_length=frame_length,
                               step_length=step_length,
                               nfft=512, nmels=40, nceps=13,
                               fmin=100, fmax=4000, padding=padding),
    # pp.speech.CQTExtractor(frame_length=frame_length,
    #                        step_length=step_length,
    #                        nbins=96, nmels=40, nceps=20,
    #                        fmin=64, fmax=4000, padding=padding),
    # pp.speech.PitchExtractor(frame_length=0.05, step_length=step_length,
    #                          threshold=0.5, f0=False, algo='swipe',
    #                          fmin=64, fmax=400),
    # pp.speech.openSMILEpitch(frame_length=0.06, step_length=step_length,
    #                          voiceProb=True, loudness=True),
    pp.speech.SADextractor(nb_mixture=3, nb_train_it=25,
                           feat_name='energy'),
    pp.base.DeltaExtractor(width=9, order=(0, 1, 2), axis=0,
                           feat_name='mfcc'),
    # BNF
    pp.base.StackFeatures(context=10, feat_name='mfcc'),
    # pp.speech.ApplyingSAD(stack_context={'mfcc': 10}, smooth_win=8,
    #                       keep_unvoiced=True, feat_name='mfcc'),
    pp.speech.BNFExtractor(input_feat='mfcc', network=bnf_network,
                           pre_mvn=True),
    # normalization
    pp.speech.AcousticNorm(mean_var_norm=True, windowed_mean_var_norm=True,
                           sad_stats=True, sad_name='sad', ignore_sad_error=True,
                           feat_name=('spec', 'mspec', 'mfcc', 'bnf',
                                      'qspec', 'qmfcc', 'qmspec')),
    pp.base.EqualizeShape0(feat_name=('spec', 'mspec', 'mfcc', 'bnf',
                                      'qspec', 'qmspec', 'qmfcc',
                                      'pitch', 'f0', 'sad', 'energy',
                                      'sap', 'loudness')),
    pp.base.RunningStatistics(),
    pp.base.AsType({'spec': dtype, 'mspec': dtype, 'mfcc': dtype,
                    'qspec': dtype, 'qmspec': dtype, 'qmfcc': dtype,
                    'pitch': dtype, 'f0': dtype, 'sap': dtype,
                    'sad': dtype, 'energy': dtype, 'loudness': dtype,
                    'raw': dtype, 'bnf': dtype}),
], debug=False)
# tmp = extractors.transform(all_files[0])
# V.plot_features(tmp)
# V.plot_save('/tmp/tmp.pdf')
# exit()
# ===========================================================================
# Processor
# ===========================================================================
processor = pp.FeatureProcessor(all_files, extractors, output_path,
                                ncache=0.12, ncpu=None, override=True)
with utils.UnitTimer():
  processor.run()
readme_path = os.path.join(audio.path, [i for i in os.listdir(audio.path)
                                        if 'README' in i][0])
shutil.copy(readme_path,
            os.path.join(output_path, 'README.md'))
pp.calculate_pca(processor, override=True)
# ====== check the preprocessed dataset ====== #
ds = F.Dataset(output_path, read_only=True)
pp.validate_features(ds, path=figpath, nb_samples=8, override=True)
print(ds)
# ====== print all indices ====== #
print("All indices:")
for k in ds.keys():
  if 'indices' in k:
    print(' - ', ctext(k, 'yellow'))
# ====== print pipeline ====== #
padding = '  '
print(ctext("* Pipeline:", 'red'))
for _, extractor in ds['pipeline'].steps:
  for line in str(extractor).split('\n'):
    print(padding, line)
# ====== print config ====== #
print(ctext("* Configurations:", 'red'))
for i, j in ds['config'].items():
  print(padding, i, ':', j)
# ====== check PCA components ====== #
for n in ds.keys():
  if '_pca' in n:
    pca = ds[n]
    if pca.components_ is None:
      print(ctext(n, 'yellow'), 'components is None !')
    elif np.any(np.isnan(pca.components_)):
      print(ctext(n, 'yellow'), 'contains NaN !')
    else:
      print(ctext(n, 'yellow'),
          ':', ' '.join(['%.2f' % i + '-' + '%.2f' % j
          for i, j in zip(pca.explained_variance_ratio_[:8],
                          pca.explained_variance_[:8])]))
# ====== check if any pitch or f0 allzeros ====== #
if 'pitch' in ds:
  indices = sorted([(name, s, e) for name, (s, e) in ds['indices']],
                   key=lambda x: x[1])
  for name, start, end in indices:
    pitch = ds['pitch'][start:end][:]
    if not np.any(pitch):
      print("Pitch and f0 of name: %s contains only zeros" % name)
# ====== Visual cluster ====== #
for feat in ('bnf', 'mspec', 'spec', 'mfcc'):
  if feat not in ds:
    continue
  from sklearn.manifold import TSNE
  X = []; y = []
  # get right feat and indices
  feat_pca = ds.find_prefix(feat, 'pca')
  indices = ds.find_prefix(feat, 'indices')
  # transform
  prog = Progbar(target=len(indices),
                 print_summary=True, print_report=True,
                 name="PCA transform: %s" % feat)
  for f, (start, end) in indices:
    if filter_func(f):
      X.append(np.mean(
          feat_pca.transform(ds[feat][start:end]),
          axis=0, keepdims=True))
      y.append(labels_fn(f))
    prog.add(1)
  X_pca = np.concatenate(X, axis=0)
  y = np.asarray(y)
  with UnitTimer(name="TSNE: feat='%s' N=%d" % (feat, X_pca.shape[0])):
    X_tsne = TSNE(n_components=2).fit_transform(X_pca)
  colors = V.generate_random_colors(len(labels), seed=12082518)
  y = [colors[i] for i in y]
  legend = {c: str(i) for i, c in enumerate(colors)}
  with V.figure(ncol=1, nrow=5, title='PCA: %s' % feat):
    V.plot_scatter(X_pca[:, 0], X_pca[:, 1], color=y, legend=legend)
  with V.figure(ncol=1, nrow=5, title='TSNE: %s' % feat):
    V.plot_scatter(X_tsne[:, 0], X_tsne[:, 1], color=y, legend=legend)
# ====== save all the figure ====== #
V.plot_save(os.path.join(figpath, 'pca_tsne.pdf'),
            tight_plot=True)
# ====== print log ====== #
print('Output path:', ctext(output_path, 'cyan'))
print('Figure path:', ctext(figpath, 'cyan'))
