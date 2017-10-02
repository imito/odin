from __future__ import print_function, division, absolute_import

import os

from six import add_metaclass
from abc import ABCMeta, abstractmethod
from collections import Mapping

from odin.utils import get_all_files, is_string, as_tuple, is_pickleable


from sklearn.base import BaseEstimator, TransformerMixin
from .signal import compute_delta


@add_metaclass(ABCMeta)
class Extractor(BaseEstimator, TransformerMixin):
    """ Extractor

    The developer must override the `_transform` method.
     - Any returned features in form of `Mapping` (e.g. dictionary) will
       be stored with the new extracted features.
     - If the returned new features is not `Mapping`, all previous extracted
       features will be ignored, and only return the new features.

    """

    def __init__(self):
        super(Extractor, self).__init__()

    def fit(self, X, y=None):
        # Do nothing here
        return self

    @abstractmethod
    def _transform(self, X):
        raise NotImplementedError

    def transform(self, X):
        # NOTE: do not override this method
        y = self._transform(X)
        if isinstance(y, Mapping):
            # remove None values
            tmp = {}
            for name, feat in y.iteritems():
                if any(c.isupper() for c in name):
                    raise RuntimeError("name for features cannot contain "
                                       "upper case.")
                if feat is None:
                    continue
                tmp[name] = feat
            y = tmp
            # add old features extracted in X,
            # but do NOT override new features in y
            if isinstance(X, Mapping):
                for name, feat in X.iteritems():
                    if any(c.isupper() for c in name):
                        raise RuntimeError("name for features cannot contain "
                                           "upper case.")
                    if name not in y:
                        y[name] = feat
        return y


# ===========================================================================
# General extractor
# ===========================================================================
def _check_feat_name(feat_type, name):
    if feat_type is None:
        return True
    elif callable(feat_type):
        return bool(feat_type(name))
    return name in feat_type


class DeltaExtractor(Extractor):

    def __init__(self, width=9, order=1, axis=-1, feat_type=None):
        super(DeltaExtractor, self).__init__()
        # ====== check width ====== #
        width = int(width)
        if width % 2 == 0 or width < 3:
            raise ValueError("`width` must be odd integer >= 3, give value: %d" % width)
        self.width = width
        # ====== check order ====== #
        order = int(order)
        if order < 0:
            raise ValueError("`order` must >= 0, given value: %d" % order)
        self.order = order
        # ====== axis ====== #
        self.axis = axis
        self.feat_type = feat_type

    def _transform(self, X):
        pass


class ShiftedDeltaExtractor(Extractor):

    def __init__(self, width=9, axis=-1, feat_type=None):
        super(DeltaExtractor, self).__init__()
        # ====== check width ====== #
        width = int(width)
        if width % 2 == 0 or width < 3:
            raise ValueError("`width` must be odd integer >= 3, give value: %d" % width)
        self.width = width
        # ====== axis ====== #
        self.axis = axis
        self.feat_type = feat_type

    def _transform(self, X):
        pass


class EqualizeShape0(Extractor):
    """ EqualizeShape0 """

    def __init__(self, feat_type):
        super(EqualizeShape0, self).__init__()
        if feat_type is None:
            pass
        elif callable(feat_type):
            if not is_pickleable(feat_type):
                raise ValueError("`feat_type` must be a pickle-able callable.")
        else:
            feat_type = tuple([f.lower() for f in as_tuple(feat_type, t=str)])
        self.feat_type = feat_type

    def _transform(self, X):
        if isinstance(X, Mapping):
            equalized = {}
            n = min(feat.shape[0]
                    for name, feat in X.iteritems()
                    if _check_feat_name(self.feat_type, name))
            # ====== equalize ====== #
            for name, feat in X.iteritems():
                # cut the features in left and right
                # if the shape[0] is longer
                if _check_feat_name(self.feat_type, name) and feat.shape[0] != n:
                    diff = feat.shape[0] - n
                    diff_left = diff // 2
                    diff_right = diff - diff_left
                    feat = feat[diff_left:-diff_right]
                equalized[name] = feat
            X = equalized
        return X
