import json
import os

# Disable tensorflow warnings
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

import tensorflow as tf
import numpy as np
import time

from tensorflow import keras
from tensorflow.keras.layers import LSTM, Embedding, Dense, Dropout, TimeDistributed, Bidirectional, Attention, Input, RepeatVector

from generateData import generateData, toSparse, generateDataMulti, inputFileBase, outputFileBase, fileExt, fillInt, stickerLen, turnLen
from scrambler import maxScrambleLen


# Hyperparameters
trainingSize = 10000000
batchSize = 512
epochs = 10
numFiles = 5

modelName = "rubiks-cube-lstm-{}".format(int(time.time()))
checkpointPath = "logs/checkpoints/checkpoint.keras"

maxInputLen = 54
hiddenSize = 128


# Loads data from specified input and output files, returns features and labels
def loadData(numFiles=0):
    encInput = np.load(inputFileBase + str(numFiles) + fileExt)
    decInput = np.load(outputFileBase + str(numFiles) + fileExt)

    # decOutput = toSparse(decInput, 13)

    X = {
        "encInput": encInput,
        "decInput": decInput
    }

    Y = {
        "decDense": decInput
    }

    return X, Y


# Partitions data into train/dev/test sets
# Specified partition weights determine how large each set is
#       E.g. trainWeight=98, devWeight=1, testWeight=1
#           --> (XTrain, YTrain) will have 98% of input's examples,
#               (XDev, YDev) and (XTest, YTest) will have 1% respectively
def partitionData(X, Y, trainWeight=3, devWeight=1, testWeight=1):

    singleWeight = int(X.shape[0] / (trainWeight + devWeight + testWeight))

    partition1 = trainWeight * singleWeight
    partition2 = partition1 + devWeight * singleWeight

    XTrain = X[:partition1]
    YTrain = Y[:partition1]
    XDev = X[partition1:partition2]
    YDev = Y[partition1:partition2]
    XTest = X[partition2:]
    YTest = Y[partition2:]

    return (XTrain, YTrain), (XDev, YDev), (XTest, YTest)


# Creates encoder layers
def createEncoderLayers(Tx, inputVocabLen, embedDim=128, hiddenDim=512):
    layers = {}

    encInput = Input(shape=(Tx, ), name="encInput")
    encEmbedding = Embedding(input_dim=inputVocabLen,
                             output_dim=embedDim, input_length=Tx, name="encEmbedding")
    encLSTM = LSTM(units=hiddenDim, return_state=True, name="encLSTM")

    layers["encInput"] = encInput
    layers["encEmbedding"] = encEmbedding
    layers["encLSTM"] = encLSTM

    return layers


# Connects encoder layers together
def connectEncoder(layers):
    net = layers["encInput"]
    net = layers["encEmbedding"](net)
    _, h, c = layers["encLSTM"](net)

    encOutput = [h, c]
    return encOutput


# Creates decoder layers
def createDecoderLayers(Ty, outputVocabLen, embedDim=128, hiddenDim=512):
    layers = {}

    decInput = Input(shape=(Ty, ), name="decInput")
    decInitialStateH = Input(shape=(hiddenDim, ), name="decInitialStateH")
    decInitialStateC = Input(shape=(hiddenDim, ), name="decInitialStateC")

    decEmbedding = Embedding(input_dim=outputVocabLen,
                             output_dim=embedDim, input_length=Ty, name="decEmbedding")
    decLSTM = LSTM(units=hiddenDim, return_state=True, return_sequences=True, name="decLSTM")
    decDense = TimeDistributed(Dense(outputVocabLen, activation="softmax"), name="decDense")

    layers["decInput"] = decInput
    layers["decInitialStateH"] = decInitialStateH
    layers["decInitialStateC"] = decInitialStateC
    layers["decEmbedding"] = decEmbedding
    layers["decLSTM"] = decLSTM
    layers["decDense"] = decDense

    return layers


# Connects decoder layers together
def connectDecoder(layers, initialState):
    net = layers["decInput"]
    net = layers["decEmbedding"](net)
    net, _, _ = layers["decLSTM"](net, initial_state=initialState)
    net = layers["decDense"](net)

    decOutput = net
    return decOutput


# Defines model layers, compiles model
def createModel(Tx, Ty, inputVocabLen, outputVocabLen, embedDim=128, hiddenDim=512):
    # Create and connect encoder layers
    encLayers = createEncoderLayers(Tx, inputVocabLen)
    encOutput = connectEncoder(encLayers)

    # Create and connect decoder layers
    decLayers = createDecoderLayers(Ty, outputVocabLen)
    decOutput = connectDecoder(decLayers, initialState=encOutput)

    # Training model
    encInput = encLayers["encInput"]
    decInput = decLayers["decInput"]
    model = keras.Model(inputs=[encInput, decInput], outputs=[decOutput], name="trainingModel")

    encModel = keras.Model(inputs=encLayers["encInput"], outputs=encOutput)

    decInitialState = [decLayers["decInitialStateH"], decLayers["decInitialStateC"]]
    decOutput = connectDecoder(decLayers, initialState=decInitialState)    # re-assign decOutput for decModel
    decModel = keras.Model(inputs=[decInput] + decInitialState, outputs=decOutput)

    # Compile model
    model.compile(loss="sparse_categorical_crossentropy",
                  optimizer="adam", metrics=["accuracy"])

    return model, encModel, decModel


# Trains model
def trainModel(loadPrev=True):
    model, encoderModel, decoderModel = createModel(Tx=maxInputLen, Ty=maxScrambleLen, inputVocabLen=stickerLen, outputVocabLen=turnLen + 1)

    if loadPrev:
        model.load_weights(checkpointPath)
        encoderModel.load_weights(checkpointPath, by_name=True)
        decoderModel.load_weights(checkpointPath, by_name=True)

    for i in range(numFiles):
        X, Y = loadData(i)

        callbacks = getCallbacks()

        model.fit(
            x=X, y=Y, epochs=epochs, batch_size=batchSize, validation_split=0.02, callbacks=callbacks)

        encoderModel.save_weights(filepath="data/models/encoderModel.hdf5", save_format="h5")
        decoderModel.save_weights(filepath="data/models/decoderModel.hdf5", save_format="h5")
        model.save(filepath="data/model.hdf5", save_format="h5")

    model.summary()
    

# Get callbacks for model.fit()
def getCallbacks():
    checkpoint = keras.callbacks.ModelCheckpoint(filepath=checkpointPath, monitor="val_loss", verbose=1, save_weights_only=True, save_best_only=True)
    earlyStopping = keras.callbacks.EarlyStopping(monitor="val_loss", patience=3, verbose=1)
    tensorboard = keras.callbacks.TensorBoard(log_dir="logs/{}".format(modelName))

    return [checkpoint, earlyStopping, tensorboard]


# Predicts solution from single sticker mapping
def predict(stickers, encoderModel, decoderModel):
    h, c = encoderModel.predict(stickers)

    targetSeq = np.zeros((stickers.shape[0], maxScrambleLen))
    
    prevMoves = np.zeros((fillInt + 1, ))
    for i in range(maxScrambleLen):

        X = {
            "decInput": targetSeq,
            "decInitialStateH": h,
            "decInitialStateC": c
        }

        outputs = decoderModel.predict(X)
        print(outputs)
        prevMoves = outputs[:, i, :]
        targetSeq[:, i] = np.argmax(prevMoves, axis=-1)

    return targetSeq


if __name__ == "__main__":
    # generateDataMulti(trainingSize, totalFiles=numFiles)
    trainModel(loadPrev=False)

    model, encoderModel, decoderModel = createModel(54, 25, 6, 13)
    model.load_weights(checkpointPath)
    encoderModel.load_weights(checkpointPath, by_name=True)
    decoderModel.load_weights(checkpointPath, by_name=True)

    X = np.load("data/features/X0.npy")[:20]
    Y = np.load("data/labels/Y0.npy")[:20]

    print("Prediction: ")
    print(predict(X, encoderModel, decoderModel))
    print("Actual: ")
    print(Y)

