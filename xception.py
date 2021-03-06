from keras.preprocessing.image import load_img
from keras.preprocessing.image import img_to_array
from keras.applications.xception import preprocess_input
from keras.applications.xception import decode_predictions
from keras.applications.xception import Xception
from tensorflow.python.framework import ops
from keras.models import Model
from keras.utils import plot_model
from keras import backend as K
from keras.layers.core import Lambda
import tensorflow as tf
import numpy as np
import cv2
import keras
from input import input_images_dir

#____________________________________________ GRAD CAM RESNET50______________________________________________


def normalize(x):
    # utility function to normalize a tensor by its L2 norm
    return x / (K.sqrt(K.mean(K.square(x))) + 1e-5)

model = Xception()
plot_model(model, to_file='model/xception.png')
image = load_img(input_images_dir+'/input.jpg', target_size=(229, 229))
image = img_to_array(image)
image = image.reshape((1, image.shape[0], image.shape[1], image.shape[2]))
image = preprocess_input(image)

predictions = model.predict(image)
predicted_class = np.argmax(predictions)

def target_category_loss(x, category_index, nb_classes):
    return tf.multiply(x, K.one_hot([category_index], nb_classes))

def target_category_loss_output_shape(input_shape):
    return input_shape

activation_layer = 'block14_sepconv2_act'
nb_classes = model.get_layer('predictions').output.shape[1]		# get number of classes
target_layer = lambda x: target_category_loss(x, predicted_class, nb_classes)
x = Lambda(target_layer, output_shape = target_category_loss_output_shape)(model.output)

gcam_model = Model(inputs=model.input, outputs=x)

def _compute_gradients(tensor, var_list):
	grads = tf.gradients(tensor, var_list)
	return [grad if grad is not None else tf.zeros_like(var) for var, grad in zip(var_list, grads)]


loss = K.sum(gcam_model.output)
conv_output =  [l for l in gcam_model.layers if l.name == activation_layer][0].output
grads = normalize(_compute_gradients(loss, [conv_output])[0])
gradient_function = K.function([gcam_model.input], [conv_output, grads])

output, grads_val = gradient_function([image])
output, grads_val = output[0, :], grads_val[0, :, :, :]

weights = np.mean(grads_val, axis = (0, 1))
cam = np.ones(output.shape[0 : 2], dtype = np.float32)

for i, w in enumerate(weights):
    cam += w * output[:, :, i]

cam = cv2.resize(cam, (229, 229))
cam = np.maximum(cam, 0)
heatmap = cam / np.max(cam)

#Return to BGR [0..255] from the preprocessed image
image = image[0, :]
image -= np.min(image)
image = np.minimum(image, 255)

cam = cv2.applyColorMap(np.uint8(255*heatmap), cv2.COLORMAP_JET)
cam = np.float32(cam)/80. + np.float32(image) # /80 normalization bullshit, need to rewrite
cam = 255 * cam / np.max(cam)

cv2.imwrite(input_images_dir+'/gradcam_xception.jpg', cam)


# # _____________________________________GUIDED GRAD CAM RESNET50__________________________________________

def register_gradient():
    if "GuidedBackProp" not in ops._gradient_registry._registry:
        @ops.RegisterGradient("GuidedBackProp")
        def _GuidedBackProp(op, grad):
            dtype = op.inputs[0].dtype
            return grad * tf.cast(grad > 0., dtype) * tf.cast(op.inputs[0] > 0., dtype)


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
        new_model = Xception()
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

image = load_img(input_images_dir+'/input.jpg', target_size=(229, 229))
image = img_to_array(image)
image = image.reshape((1, image.shape[0], image.shape[1], image.shape[2]))
image = preprocess_input(image)

register_gradient()
guided_model = modify_backprop(model, 'GuidedBackProp')
saliency_fn = compile_saliency_function(guided_model, activation_layer)
saliency = saliency_fn([image, 0])
cv2.imwrite(input_images_dir+'/guided_backprop_xception.jpg', deprocess_image(saliency))

#_______________________________  GUIDED GRADCAM _____________________________________________

gradcam = saliency * heatmap[..., np.newaxis]
cv2.imwrite(input_images_dir+'/guided_gradcam_xception.jpg', deprocess_image(gradcam))


#_______________________________  DECONVOLUTION _____________________________________________
def register_Deconvolve_gradient():
    if "DeconvReLU" not in ops._gradient_registry._registry:
        @ops.RegisterGradient("DeconvReLU")
        def _DeconvReLU(op, grad):
            dtype = op.inputs[0].dtype
            return grad * tf.cast(grad > 0., dtype)


register_Deconvolve_gradient()
deconvolved_model = modify_backprop(model, 'DeconvReLU')
saliency_fn = compile_saliency_function(deconvolved_model, activation_layer)
saliency = saliency_fn([image, 0])
cv2.imwrite(input_images_dir+'/deconvolved_xception.jpg', deprocess_image(saliency))