# ======================================================================
# Author: TrungNT
# ======================================================================
from __future__ import print_function, division

import os
import unittest
from six.moves import zip, range

import numpy as np

from odin.roles import add_updates, add_auxiliary_variable
from odin import backend as K
from odin import nnet as N


class BackendTest(unittest.TestCase):

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_shape(self):
        var = K.variable(np.random.rand(8, 12))
        inp = K.placeholder((None, 1, 20))
        self.assertEquals(K.get_shape(var), (8, 12))
        self.assertEquals(K.get_shape(inp), (None, 1, 20))

    def test_ops(self):
        x = K.variable(np.random.rand(8, 12))
        y = K.variable(np.random.rand(12, 25))
        z = K.placeholder((25, 18, 13))
        w = K.placeholder((25, 18))

        # ====== dot ====== #
        t = K.dot(x, y)
        self.assertEquals(K.get_shape(t), (8, 25))
        self.assertEquals(K.get_shape(t), K.eval(t).shape)
        t = K.dot(t, z)
        self.assertEquals(K.get_shape(t), (8, 18, 13))

        # ====== transpose ====== #
        self.assertEquals(K.get_shape(K.transpose(z)), (13, 18, 25))
        self.assertEquals(K.get_shape(K.transpose(t, axes=(2, 0, 1))),
                         (13, 8, 18))

        # ====== eye ====== #
        self.assertEquals(K.get_shape(K.eye(5)),
                          K.eval(K.eye(5)).shape)
        # ====== diag ====== #
        self.assertEquals(K.get_shape(K.diag(w)), (18,))
        # self.assertEquals(K.get_shape(K.diag(x)),
        # K.eval(K.diag(y)).shape)
        self.assertEquals(K.get_shape(K.square(x)),
                          K.eval(K.square(x)).shape)
        self.assertEquals(K.get_shape(K.abs(x)),
                          K.eval(K.abs(x)).shape)
        self.assertEquals(K.get_shape(K.sqrt(x)),
                          K.eval(K.sqrt(x)).shape)
        self.assertEquals(K.get_shape(K.exp(x)),
                          K.eval(K.exp(x)).shape)
        self.assertEquals(K.get_shape(K.log(x)),
                          K.eval(K.log(x)).shape)
        self.assertEquals(K.get_shape(K.round(x)),
                          K.eval(K.round(x)).shape)
        self.assertEquals(K.get_shape(K.pow(x, 2)),
                          K.eval(K.pow(x, 2)).shape)
        self.assertEquals(K.get_shape(K.clip(x, -1, 1)),
                          K.eval(K.clip(x, -1, 1)).shape)
        self.assertEquals(K.get_shape(K.inv(x)),
                          K.eval(K.inv(x)).shape)

    def test_auto_infer_shape(self):
        x = K.variable(np.random.rand(8, 25, 12))
        y = K.placeholder((None, 25, 12))

        def test_func(func):
            self.assertEquals(K.get_shape(func(x, 0)),
                              K.eval(func(x, 0)).shape)
            self.assertEquals(K.get_shape(func(x, -1)),
                              K.eval(func(x, -1)).shape)
            self.assertEquals(K.get_shape(func(x, 1, True)),
                              K.eval(func(x, 1, True)).shape)

            self.assertEquals(K.get_shape(func(x, 0)),
                              K.get_shape(func(y, 0)))
            self.assertEquals(K.get_shape(func(x, 0, True)),
                              K.get_shape(func(y, 0, True)))

            if func != K.argmax and func != K.argmin:
                self.assertEquals(K.get_shape(func(x, (1, -1))),
                                  K.eval(func(x, (1, -1))).shape)
                self.assertEquals(K.get_shape(func(x, (0, 1))),
                                  K.eval(func(x, (0, 1))).shape)
                self.assertEquals(K.get_shape(func(x, (0, 1), True)),
                                  K.eval(func(x, (0, 1), True)).shape)

        test_func(K.var)
        test_func(K.max)
        test_func(K.min)
        test_func(K.any)
        test_func(K.sum)
        test_func(K.prod)
        test_func(K.mean)
        test_func(K.std)
        test_func(K.any)
        test_func(K.argmax)
        test_func(K.argmin)

        self.assertEquals(K.get_shape(K.argsort(x, -1)),
                          K.eval(K.argsort(x, -1)).shape)

    def test_simple_ops(self):
        x = K.variable(np.random.rand(25, 8, 12))
        y = K.variable(18)
        z = K.variable(np.random.rand(25, 8, 12))
        v = K.variable(np.random.rand(12, 8))
        w = K.variable(np.random.rand(1, 12))
        w = K.addbroadcast(w, 0)

        def test_func(x, y, func):
            self.assertEquals(K.get_shape(func(x, y)),
                              K.eval(func(x, y)).shape)
        test_func(x, y, K.add)
        test_func(x, y, K.sub)
        test_func(x, y, K.mul)
        test_func(x, y, K.div)
        test_func(x, y, K.mod)

        test_func(x, w, K.add)
        test_func(x, w, K.sub)
        test_func(x, w, K.mul)
        test_func(x, w, K.div)
        test_func(x, w, K.mod)

        test_func(x, z, K.minimum)
        test_func(x, z, K.maximum)

        # test_func(x, z, K.concatenate)
        test_func(x, z, K.stack)

        test_func(v, v, K.categorical_crossentropy)

    def test_complex_ops(self):
        x = K.variable(np.random.rand(25, 8, 12))
        y = K.variable(np.random.rand(8, 12))

        def test_func(x, func, *args, **kwargs):
            self.assertEquals(K.get_shape(func(x, *args, **kwargs)),
                              K.eval(func(x, *args, **kwargs)).shape)

        test_func(x, K.reverse, 0)
        test_func(x, K.reverse, -1)
        test_func(x, K.tile, 2)
        test_func(x, K.repeat, 2, -1)
        test_func(x, K.dimshuffle, (2, 0, 1))
        test_func(x, K.expand_dims, 1)
        test_func(x, K.pad, -1, 2)
        test_func(x, K.reshape, (-1, 12))

        test_func(y, K.antirectify)
        test_func(y, K.randrectify, 0.3, 0.8, 'auto')
        test_func(x, K.exp_linear, 1.0)
        test_func(x, K.relu, 0.)
        test_func(x, K.tanh)
        test_func(x, K.softplus)
        test_func(y, K.softmax)
        test_func(x, K.softsign)
        test_func(x, K.linear)
        test_func(x, K.sigmoid)
        test_func(x, K.hard_sigmoid)

    def test_computation_graph(self):
        X = K.placeholder(shape=(None, 32), name='input')
        z = K.variable(np.random.rand(10, 10), name='z')
        f = N.Sequence([
            N.Dense(16, activation=K.relu),
            N.Dense(8, activation=K.softmax)
        ])
        y = f(X)
        add_auxiliary_variable(y, K.constant(10, name='aux_const'))
        add_updates(y, z, z * 2)

        tmp = K.ComputationGraph(y)
        self.assertEqual(len(tmp.placeholders), 1)
        self.assertEqual(len(tmp.trainable_variables), 4)
        self.assertEqual(len(tmp.parameters), 4)
        self.assertEqual(len(tmp.dict_of_placeholders), 1)
        self.assertEqual(len(tmp.auxiliary_variables), 1)
        tmp.intermediary_variables # no idea how to test this
        self.assertEqual(len(tmp.updates), 1)
        self.assertEqual(K.ComputationGraph(y), tmp)

    def test_confusion_matrix(self):
        from sklearn.metrics import confusion_matrix
        y1 = np.random.randint(0, 8, size=100)
        y2 = np.random.randint(0, 8, size=100)
        y_pred = K.variable(y1)
        y_true = K.variable(y2)
        confusion = K.confusion_matrix(y_pred, y_true)

        r1 = K.eval(confusion)
        r2 = confusion_matrix(y1, y2)
        self.assertEqual(np.sum(r1 - r2), 0.)

    def test_rnn_decorator(self):
        @K.rnn_decorator(sequences='X', states='out')
        def rnn(X, out):
            return K.relu(X + out)

        y = rnn(K.ones(shape=(25, 12, 18, 8)),
                K.zeros(shape=(25, 18, 8))
        )
        f = K.function([], y)
        self.assertEqual(f()[0].shape, (25, 12, 18, 8))

if __name__ == '__main__':
    print(' odin.tests.run() to run these tests ')
