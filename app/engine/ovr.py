# -*- coding: utf-8 -*-
"""
ovr

One-versus-Rest approach for multi-label classification, predicting each label
independently using Convolutional Neural Network similar architecture to VGG-16

VERY DEEP CONVOLUTIONAL NETWORKS FOR LARGE-SCALE IMAGE RECOGNITION
Simonyan K. & Zisserman A. (2015)
"""

import tensorflow as tf

import numpy as np

from __main__ import EVAL, TRAIN, ENSEMBLE, CONT
from ..models.cnn import ConvolutionalNeuralNetwork
from ..settings import (IMAGE_PATH, IMAGE_SHAPE, BATCH_SIZE, MODEL_PATH,
                        MAX_STEPS, ALPHA, BETA, TAGS, TAGS_WEIGHTINGS, EXT,
                        TAGS_THRESHOLDS, VALID_SIZE, KEEP_RATE, OUTPUT_PATH,
                        N_THREADS, AUGMENT)
from ..pipeline import data_pipe, generate_data_skeleton
from ..controllers import train, save_session, predict, restore_session, submit


def vgg_16(class_balance, l2_norm):

    global x, y_, keep_prob, is_train, y_pred, loss, train_step, accuracy, \
           saver

    cnn = ConvolutionalNeuralNetwork(shape=IMAGE_SHAPE, num_classes=17)
    x, y_, keep_prob, is_train = cnn.x, cnn.y_, cnn.keep_prob, cnn.is_train

    conv_layer_1 = cnn.add_conv_layer(x, [[3, 3, IMAGE_SHAPE[-1], 32], [32]])
    conv_layer_2 = cnn.add_conv_layer(conv_layer_1, [[3, 3, 32, 32], [32]])
    max_pool_1 = cnn.add_pooling_layer(conv_layer_2)
    conv_layer_3 = cnn.add_conv_layer(max_pool_1, [[3, 3, 32, 64], [64]])
    conv_layer_4 = cnn.add_conv_layer(conv_layer_3, [[3, 3, 64, 64], [64]])
    max_pool_2 = cnn.add_pooling_layer(conv_layer_4)
    conv_layer_5 = cnn.add_conv_layer(max_pool_2, [[3, 3, 64, 128], [128]])
    conv_layer_6 = cnn.add_conv_layer(conv_layer_5, [[3, 3, 128, 128], [128]])
    conv_layer_7 = cnn.add_conv_layer(conv_layer_6, [[3, 3, 128, 128], [128]])
    max_pool_3 = cnn.add_pooling_layer(conv_layer_7)
    conv_layer_8 = cnn.add_conv_layer(max_pool_3, [[3, 3, 128, 256], [256]])
    conv_layer_9 = cnn.add_conv_layer(conv_layer_8, [[3, 3, 256, 256], [256]])
    conv_layer_10 = cnn.add_conv_layer(conv_layer_9, [[3, 3, 256, 256], [256]])
    max_pool_4 = cnn.add_pooling_layer(conv_layer_10)
    conv_layer_11 = cnn.add_conv_layer(max_pool_4, [[3, 3, 256, 256], [256]])
    conv_layer_12 = cnn.add_conv_layer(conv_layer_11, [[3, 3, 256, 256], [256]])
    conv_layer_13 = cnn.add_conv_layer(conv_layer_12, [[3, 3, 256, 256], [256]])
    max_pool_5 = cnn.add_pooling_layer(conv_layer_13)
    dense_layer_1 = cnn.add_dense_layer(max_pool_5, [[4 * 4 * 256, 2048], [2048]])
    drop_out_1 = cnn.add_drop_out_layer(dense_layer_1)
    dense_layer_2 = cnn.add_dense_layer(drop_out_1, [[2048, 500], [500]])
    drop_out_2 = cnn.add_drop_out_layer(dense_layer_2)
    logits = cnn.add_read_out_layer(drop_out_2)
    # [batch_size, 17] of logits (θ transpose X) for each of 17 classes

    # Tensorflow cross_entropy loss API
    cross_entropy = tf.nn.sigmoid_cross_entropy_with_logits(logits=logits,
                                                            labels=y_)
    # [batch_size, 17] of logistic loss for each of 17 classes

    # Explicit logistic loss function
    # cross_entropy = - (y_ * tf.log(1 / (1 + tf.exp(-logits)) + 1e-9) +
    #                  (1 - y_) * tf.log(1 - 1 / (1 + tf.exp(-logits)) + 1e-9))

    if class_balance:
        # applying inverted label frequencies as weights to loss function
        class_weights = tf.constant([[TAGS_WEIGHTINGS]],
                                    shape=[1, cnn._n_class])
        cross_entropy *= class_weights

    if l2_norm:
        # add L2 regularization on weights from readout layer and dense layers
        weights2norm = [var for var in tf.trainable_variables()
                        if var.name.startswith(('weight', 'bias'))][-6:]
        regularizers = tf.add_n([tf.nn.l2_loss(var) for var in weights2norm])
        cross_entropy += BETA * regularizers

    for n in tf.global_variables():
        print(n)

    loss = tf.reduce_mean(cross_entropy)

    update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
    with tf.control_dependencies(update_ops):
        # Numerical Optimisation only ran after updating moving avg and var
        train_step = tf.train.RMSPropOptimizer(learning_rate=ALPHA,
                                               decay=0.7,
                                               momentum=.5,
                                               epsilon=1e-10,
                                               use_locking=False,
                                               centered=False).minimize(loss)
    # eval
    y_pred = tf.nn.sigmoid(logits)
    correct_pred = tf.equal(
                   tf.cast(y_pred > TAGS_THRESHOLDS, tf.int8),
                   tf.cast(y_, tf.int8))
    # accuracy = tf.reduce_mean(tf.cast(correct_prediction, tf.float32))
    all_correct_pred = tf.reduce_min(tf.cast(correct_pred, tf.float32), 1)
    accuracy = tf.reduce_mean(all_correct_pred)

    # saver, to include all variables including moving mean and var for bn
    saver = tf.train.Saver(max_to_keep=5, var_list=tf.global_variables())


ensemble_probs = list()

for iteration in range(ENSEMBLE):
    tf.reset_default_graph()
    # tensorflow prioritises gpu when available
    vgg_16(class_balance=False, l2_norm=False)

    if TRAIN:
        with tf.Session() as sess, tf.device('/cpu:0'):
            train_file_array, train_label_array, valid_file_array,\
                valid_label_array = generate_data_skeleton(
                                                root_dir=IMAGE_PATH + 'train',
                                                valid_size=VALID_SIZE,
                                                ext=EXT)
            train_image_batch, train_label_batch = data_pipe(
                                                train_file_array,
                                                train_label_array,
                                                num_epochs=None,
                                                shape=IMAGE_SHAPE,
                                                batch_size=BATCH_SIZE,
                                                # no augmentation given bn
                                                augmentation=AUGMENT,
                                                shuffle=True,
                                                threads=N_THREADS)
            valid_image_batch, valid_label_batch = data_pipe(
                                                valid_file_array,
                                                valid_label_array,
                                                num_epochs=None,
                                                shape=IMAGE_SHAPE,
                                                batch_size=BATCH_SIZE,
                                                augmentation=False,
                                                shuffle=True,
                                                threads=N_THREADS)

            init_op = tf.group(tf.local_variables_initializer(),
                               tf.global_variables_initializer())
            sess.run(init_op)
            if CONT:
                # option continue from last checkpoint
                restore_session(sess, MODEL_PATH)
            # raise exception to check memory leak
            # sess.graph.finalize()
            train(MAX_STEPS, sess, x, y_, keep_prob, is_train, y_pred,
                  train_image_batch, train_label_batch, valid_image_batch,
                  valid_label_batch, train_step, accuracy, loss,
                  TAGS_THRESHOLDS, KEEP_RATE)
            save_session(sess, path=MODEL_PATH, sav=saver)

    if EVAL:
        with tf.Session() as sess, tf.device('/cpu:0'):
            test_file_array, _ = generate_data_skeleton(
                                                root_dir=IMAGE_PATH + 'test',
                                                valid_size=None,
                                                ext=EXT)
            # !!! no shuffling and only 1 epoch of test set.
            test_image_batch, _ = data_pipe(
                                                test_file_array,
                                                _,
                                                num_epochs=1,
                                                shape=IMAGE_SHAPE,
                                                batch_size=BATCH_SIZE,
                                                augmentation=False,
                                                shuffle=False)

            init_op = tf.group(tf.local_variables_initializer(),
                               tf.global_variables_initializer())
            sess.run(init_op)
            restore_session(sess, MODEL_PATH)
            # raise exception to check memory leak
            # sess.graph.finalize()
            probs = predict(sess, x, keep_prob, is_train, y_pred,
                            test_image_batch)
            ensemble_probs.append(probs)

if EVAL:
    if not CONT:
        final_probs = np.mean(ensemble_probs, axis=0)
        submit(final_probs, OUTPUT_PATH, TAGS, TAGS_THRESHOLDS)
    elif CONT:
        submit(probs, OUTPUT_PATH, TAGS, TAGS_THRESHOLDS)

# delete session manually to prevent exit error.
del sess
