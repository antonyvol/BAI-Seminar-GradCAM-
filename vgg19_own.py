from keras.preprocessing.image import load_img
from keras.preprocessing.image import img_to_array
from keras.applications.vgg19 import preprocess_input
from keras.applications.vgg19 import decode_predictions
from keras.applications.vgg19 import VGG19
from tensorflow.python.framework import ops
from keras.models import Model
from keras import backend as K
from keras.layers.core import Lambda
import tensorflow as tf
import numpy as np
import cv2
import keras


# ____________________________________GRAD CAM____________________________________________

# np.set_printoptions(threshold=np.nan)

def normalize(x):
    # utility function to normalize a tensor by its L2 norm
    return x / (K.sqrt(K.mean(K.square(x))) + 1e-5)

model = VGG19()

image = load_img('cat.jpg', target_size=(224, 224))
image = img_to_array(image)
image = image.reshape((1, image.shape[0], image.shape[1], image.shape[2]))
image = preprocess_input(image)

predictions = model.predict(image)
predicted_class = np.argmax(predictions)

# inp = model.input 										  # input placeholder
# outputs = [layer.output for layer in model.layers]          # all layer outputs
# functors = [K.function([inp], [out]) for out in outputs]    # evaluation functions

# layer_outs = [func([image]) for func in functors]


def target_category_loss(x, category_index, nb_classes):
    return tf.multiply(x, K.one_hot([category_index], nb_classes))

def target_category_loss_output_shape(input_shape):
    return input_shape


activation_layer = 'block5_conv4'
nb_classes = model.get_layer('predictions').output.shape[1]		# get number of classes
target_layer = lambda x: target_category_loss(x, predicted_class, nb_classes)
x = Lambda(target_layer, output_shape = target_category_loss_output_shape)(model.output)

gcam_model = Model(inputs=model.input, outputs=x)


def _compute_gradients(tensor, var_list):
	grads = tf.gradients(tensor, var_list)
	return [grad if grad is not None else tf.zeros_like(var) for var, grad in zip(var_list, grads)]


loss = K.sum(gcam_model.output)
conv_output =  [l for l in gcam_model.layers if l.name is activation_layer][0].output
grads = normalize(_compute_gradients(loss, [conv_output])[0])
gradient_function = K.function([gcam_model.input], [conv_output, grads])

output, grads_val = gradient_function([image])
output, grads_val = output[0, :], grads_val[0, :, :, :]

weights = np.mean(grads_val, axis = (0, 1))
cam = np.ones(output.shape[0 : 2], dtype = np.float32)

for i, w in enumerate(weights):
    cam += w * output[:, :, i]

cam = cv2.resize(cam, (224, 224))
cam = np.maximum(cam, 0)
heatmap = cam / np.max(cam)

    #Return to BGR [0..255] from the preprocessed image
image = image[0, :]
image -= np.min(image)
image = np.minimum(image, 255)

cam = cv2.applyColorMap(np.uint8(255*heatmap), cv2.COLORMAP_JET)
cam = np.float32(cam) + np.float32(image)
cam = 255 * cam / np.max(cam)

cv2.imwrite("gradcam.jpg", cam)

# input_img = model.input
# layer_dict = dict([(layer.name, layer) for layer in model.layers[1:]])
# layer_output = layer_dict[activation_layer].output
# max_output = K.max(layer_output, axis=3)
# saliency = K.gradients(K.sum(max_output), input_img)[0]

# gradients = K.gradients(model.output, model.trainable_weights)
# sess = tf.InteractiveSession()
# sess.run(tf.initialize_all_variables())
# evaluated_gradients = sess.run(gradients, feed_dict={model.input:image})

# print(evaluated_gradients)
	
# cat = model.predict(image)

# label = decode_predictions(cat)
# label = label[0][0]
# print('%s (%.2f%%)' % (label[1], label[2]*100))

# _____________________________________GUIDED GRAD CAM__________________________________________

def register_gradient():
    if "GuidedBackProp" not in ops._gradient_registry._registry:
        @ops.RegisterGradient("GuidedBackProp")
        def _GuidedBackProp(op, grad):
            dtype = op.inputs[0].dtype
            return grad * tf.cast(grad > 0., dtype) * \
                tf.cast(op.inputs[0] > 0., dtype)


def compile_saliency_function(model, activation_layer):
    input_img = model.input
    layer_dict = dict([(layer.name, layer) for layer in model.layers[1:]])
    layer_output = layer_dict[activation_layer].output
    max_output = K.max(layer_output, axis=3)
    saliency = K.gradients(K.sum(max_output), input_img)[0]
    return K.function([input_img, K.learning_phase()], [saliency])


def modify_backprop(model, name):
    g = tf.get_default_graph()
    with g.gradient_override_map({'Relu': name}):

        # get layers that have an activation
        layer_dict = [layer for layer in model.layers[1:]
                      if hasattr(layer, 'activation')]

        # replace relu activation
        for layer in layer_dict:
            if layer.activation == keras.activations.relu:
                layer.activation = tf.nn.relu

        # re-instanciate a new model
        new_model = VGG19()
    return new_model


def deprocess_image(x):
    '''
    Same normalization as in:
    https://github.com/fchollet/keras/blob/master/examples/conv_filter_visualization.py
    '''
    if np.ndim(x) > 3:
        x = np.squeeze(x)
    # normalize tensor: center on 0., ensure std is 0.1
    x -= x.mean()
    x /= (x.std() + 1e-5)
    x *= 0.1

    # clip to [0, 1]
    x += 0.5
    x = np.clip(x, 0, 1)

    # convert to RGB array
    x *= 255
    if K.image_dim_ordering() == 'th':
        x = x.transpose((1, 2, 0))
    x = np.clip(x, 0, 255).astype('uint8')
    return x

image = load_img('cat.jpg', target_size=(224, 224))
image = img_to_array(image)
image = image.reshape((1, image.shape[0], image.shape[1], image.shape[2]))
image = preprocess_input(image)

register_gradient()
guided_model = modify_backprop(gcam_model, 'GuidedBackProp')
saliency_fn = compile_saliency_function(guided_model, activation_layer)
saliency = saliency_fn([image, 0])
gradcam = saliency[0] * heatmap[..., np.newaxis]
cv2.imwrite("guided_gradcam.jpg", deprocess_image(gradcam))