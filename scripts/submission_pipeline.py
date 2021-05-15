# -*- coding: utf-8 -*-
"""submission_pipeline_v0515_1100.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1V8CHxvTZDpYBAmgX1PiA3HeKXYPU5Wab
"""

!pip install -q pysndfx SoundFile audiomentations pretrainedmodels efficientnet_pytorch resnest

# Commented out IPython magic to ensure Python compatibility.
import os
import sys

if "google.colab" in sys.modules:
    from google.colab import drive
    drive.mount('/content/drive')
#     %cd /content/drive/MyDrive/kaggle/kaggle-birdclef-2021/notebook/
    !pip install timm
    # !pip install "../input/resnest50-fast-package/resnest-0.0.6b20200701/resnest"
    if not os.path.exists("/content/birdclef-2021"):
        !unzip ../download/birdclef-2021.zip -d /content/birdclef-2021/
        # !unzip ../download/kkiller-birdclef-2021.zip -d /content/kkiller-birdclef-2021/
        !unzip ../download/effic-brid.zip -d /content/effic-brid/
    
from resnest.torch import resnest50
import numpy as np
import librosa as lb
import soundfile as sf
import pandas as pd
import cv2
from pathlib import Path
import re
import timm
import torch
from torch import nn
from  torch.utils.data import Dataset, DataLoader
from efficientnet_pytorch import EfficientNet

from tqdm.notebook import tqdm
import time

NUM_CLASSES = 397
SR = 32_000 # サンプリリングレート
DURATION = 5
THRESH = 0.15

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print("DEVICE:", DEVICE)

TEST_AUDIO_ROOT = Path("/content/birdclef-2021/test_soundscapes")
SAMPLE_SUB_PATH = "/content/birdclef-2021/sample_submission.csv"
TARGET_PATH = None
    
if not len(list(TEST_AUDIO_ROOT.glob("*.ogg"))):
    TEST_AUDIO_ROOT = Path("/content/birdclef-2021/train_soundscapes")
    SAMPLE_SUB_PATH = None
    # SAMPLE_SUB_PATH = "../input/birdclef-2021/sample_submission.csv"
    TARGET_PATH = Path("/content/birdclef-2021/train_soundscape_labels.csv")

!ls -alh ../input/effic-brid/tf_efficientnet_b4_sr32000_d7_v1_v1/birdclef_tf_efficientnet_b4_fold0_epoch_19_f1_val_07583_20210508074617.pth





class MelSpecComputer:
    def __init__(self, sr, n_mels, fmin, fmax, **kwargs):
        self.sr = sr
        self.n_mels = n_mels
        self.fmin = fmin
        self.fmax = fmax
        kwargs["n_fft"] = kwargs.get("n_fft", self.sr//10)
        kwargs["hop_length"] = kwargs.get("hop_length", self.sr//(10*4))
        self.kwargs = kwargs

    def __call__(self, y):

        melspec = lb.feature.melspectrogram(
            y, sr=self.sr, n_mels=self.n_mels, fmin=self.fmin, fmax=self.fmax, **self.kwargs,
        )

        melspec = lb.power_to_db(melspec).astype(np.float32)
        return melspec

def mono_to_color(X, eps=1e-6, mean=None, std=None):
    mean = mean or X.mean()
    std = std or X.std()
    X = (X - mean) / (std + eps)
    
    _min, _max = X.min(), X.max()

    if (_max - _min) > eps:
        V = np.clip(X, _min, _max)
        V = 255 * (V - _min) / (_max - _min)
        V = V.astype(np.uint8)
    else:
        V = np.zeros_like(X, dtype=np.uint8)

    return V

def crop_or_pad(y, length):
    if len(y) < length:
        y = np.concatenate([y, length - np.zeros(len(y))])
    elif len(y) > length:
        y = y[:length]
    return y

class BirdCLEFDataset(Dataset):
    def __init__(
        self,
        data,
        sr=SR,
        n_mels=512,
        fmin=0,
        fmax=None,
        duration=DURATION,
        step=None,
        res_type="kaiser_fast",
        resample=True
    ):    
        self.data = data  
        self.sr = sr
        self.n_mels = n_mels
        self.fmin = fmin
        self.fmax = fmax or self.sr//2

        self.duration = duration
        self.audio_length = self.duration*self.sr
        self.step = step or self.audio_length
        
        self.res_type = res_type
        self.resample = resample

        self.mel_spec_computer = MelSpecComputer(
            sr=self.sr,
            n_mels=self.n_mels,
            fmin=self.fmin,
            fmax=self.fmax
        )

    def __len__(self):
        return len(self.data)
    
    @staticmethod
    def normalize(image):
        image = image.astype("float32", copy=False) / 255.0
        image = np.stack([image, image, image])
        return image
    
    def audio_to_image(self, audio):
        melspec = self.mel_spec_computer(audio) 
        image = mono_to_color(melspec)
        image = self.normalize(image)
        return image

    def read_file(self, filepath):
        audio, orig_sr = sf.read(filepath, dtype="float32")

        if self.resample and orig_sr != self.sr:
            audio = lb.resample(audio, orig_sr, self.sr, res_type=self.res_type)
          
        audios = []
        for i in range(self.audio_length, len(audio) + self.step, self.step):
            start = max(0, i - self.audio_length)
            end = start + self.audio_length
            audios.append(audio[start:end])
            
        if len(audios[-1]) < self.audio_length:
            audios = audios[:-1]
            
        images = [self.audio_to_image(audio) for audio in audios]
        images = np.stack(images)
        
        return images
    
        
    def __getitem__(self, idx):
        return self.read_file(self.data.loc[idx, "filepath"])

data = pd.DataFrame(
     [(path.stem, *path.stem.split("_"), path) for path in Path(TEST_AUDIO_ROOT).glob("*.ogg")],
    columns = ["filename", "id", "site", "date", "filepath"]
)
print(data.shape)
data.head()

df_train = pd.read_csv("/content/birdclef-2021/train_metadata.csv")

LABEL_IDS = {label: label_id for label_id,label in enumerate(sorted(df_train["primary_label"].unique()))}
INV_LABEL_IDS = {val: key for key,val in LABEL_IDS.items()}

test_data = BirdCLEFDataset(data=data)
len(test_data), test_data[0].shape

def get_model(name, num_classes=NUM_CLASSES):
    """
    Loads a pretrained model. 
    Supports ResNest, ResNext-wsl, EfficientNet, ResNext and ResNet.

    Arguments:
        name {str} -- Name of the model to load

    Keyword Arguments:
        num_classes {int} -- Number of classes to use (default: {1})

    Returns:
        torch model -- Pretrained model
    """
    if "resnest" in name:
        model = getattr(resnest_torch, name)(pretrained=True)
    elif "wsl" in name:
        model = torch.hub.load("facebookresearch/WSL-Images", name)
    elif name.startswith("resnext") or  name.startswith("resnet"):
        model = torch.hub.load("pytorch/vision:v0.6.0", name, pretrained=True)
    elif name.startswith("tf_efficientnet_b"):
        model = getattr(timm.models.efficientnet, name)(pretrained=False)
    elif "efficientnet-b" in name:
        model = EfficientNet.from_pretrained(name)
    else:
        model = pretrainedmodels.__dict__[name](pretrained='imagenet')

    if hasattr(model, "fc"):
        nb_ft = model.fc.in_features
        model.fc = nn.Linear(nb_ft, num_classes)
    elif hasattr(model, "_fc"):
        nb_ft = model._fc.in_features
        model._fc = nn.Linear(nb_ft, num_classes)
    elif hasattr(model, "classifier"):
        nb_ft = model.classifier.in_features
        model.classifier = nn.Linear(nb_ft, num_classes)
    elif hasattr(model, "last_linear"):
        nb_ft = model.last_linear.in_features
        model.last_linear = nn.Linear(nb_ft, num_classes)

    return model

def load_resnest50(checkpoint_path, num_classes=NUM_CLASSES):
    net = timm.create_model("tf_efficientnet_b4", pretrained=False)
    net.classifier = nn.Linear(net.classifier.in_features, num_classes)
    dummy_device = torch.device("cpu")
    d = torch.load(checkpoint_path, map_location=dummy_device)
    for key in list(d.keys()):
        d[key.replace("model.", "")] = d.pop(key)
    net.load_state_dict(d)
    net = net.to(DEVICE)
    net = net.eval()
    return net

def load_effnetb3(checkpoint_path, num_classes=NUM_CLASSES):
    #cf. https://www.kaggle.com/andradaolteanu/ii-shopee-model-training-with-pytorch-x-rapids
    net = EfficientNet.from_name("efficientnet-b3").cuda()
    if hasattr(net, "fc"):
        nb_ft = net.fc.in_features
        net.fc = nn.Linear(nb_ft, num_classes)
    elif hasattr(net, "_fc"):
        nb_ft = net._fc.in_features
        net._fc = nn.Linear(nb_ft, num_classes)
    elif hasattr(net, "classifier"):
        nb_ft = net.classifier.in_features
        net.classifier = nn.Linear(nb_ft, num_classes)
    elif hasattr(net, "last_linear"):
        nb_ft = net.last_linear.in_features
        net.last_linear = nn.Linear(nb_ft, num_classes)
    dummy_device = torch.device("cpu")
    d = torch.load(checkpoint_path, map_location=dummy_device)
    for key in list(d.keys()):
        d[key.replace("model.", "")] = d.pop(key)
    net.load_state_dict(d)
    net = net.to(DEVICE)
    net = net.eval()
    return net

def load_wsl(
    name:str,
    checkpoint_path:Path,
    num_classes=NUM_CLASSES
):
    net = torch.hub.load("facebookresearch/WSL-Images", name)
    if hasattr(net, "fc"):
        nb_ft = net.fc.in_features
        net.fc = nn.Linear(nb_ft, num_classes)
    elif hasattr(net, "_fc"):
        nb_ft = net._fc.in_features
        net._fc = nn.Linear(nb_ft, num_classes)
    elif hasattr(net, "classifier"):
        nb_ft = net.classifier.in_features
        net.classifier = nn.Linear(nb_ft, num_classes)
    elif hasattr(net, "last_linear"):
        nb_ft = net.last_linear.in_features
        net.last_linear = nn.Linear(nb_ft, num_classes)
    dummy_device = torch.device("cpu")
    d = torch.load(checkpoint_path, map_location=dummy_device)
    for key in list(d.keys()):
        d[key.replace("model.", "")] = d.pop(key)
    net.load_state_dict(d)
    net = net.to(DEVICE)
    net = net.eval()
    return net

nets = []
# resnest
nets.append(
    load_resnest50(
        checkpoint_path = Path("../input/effic-brid/tf_efficientnet_b4_sr32000_d7_v1_v1/birdclef_tf_efficientnet_b4_fold0_epoch_18_f1_val_07574_20210508073903.pth")
    )
)

# effnet-b3
nets.append(
    load_effnetb3(
        checkpoint_path = Path("./efficientnet-b3_sr32000_d7_v1_v1/birdclef_efficientnet-b3_fold0_epoch_01_f1_val_03859_20210514211130.pth")
    )
)

# ws
nets.append(
    load_wsl(
        name            = "resnext101_32x8d_wsl",
        checkpoint_path = Path("./resnext101_32x8d_wsl_sr32000_d7_v1_v1/birdclef_resnext101_32x8d_wsl_fold0_epoch_12_f1_val_07210_20210515025813.pth")
    )
)

@torch.no_grad()
def get_thresh_preds(out, thresh=None):
    thresh = thresh or THRESH
    o = (-out).argsort(1)
    npreds = (out > thresh).sum(1)
    preds = []
    for oo, npred in zip(o, npreds):
        preds.append(oo[:npred].cpu().numpy().tolist())
    return preds

def get_bird_names(preds):
    bird_names = []
    for pred in preds:
        if not pred:
            bird_names.append("nocall")
        else:
            bird_names.append(" ".join([INV_LABEL_IDS[bird_id] for bird_id in pred]))
    return bird_names

def predict(nets, test_data, names=True):
    preds = []
    with torch.no_grad():
        for idx in  tqdm(list(range(len(test_data)))):
            xb = torch.from_numpy(test_data[idx]).to(DEVICE)
            pred = 0.
            for net in nets:
                o = net(xb)
                o = torch.sigmoid(o)

                pred += o

            pred /= len(nets)
            
            if names:
                pred = get_bird_names(get_thresh_preds(pred))

            preds.append(pred)
    return preds

pred_probas = predict(nets, test_data, names=False)
print(len(pred_probas))

preds = [get_bird_names(get_thresh_preds(pred, thresh=THRESH)) for pred in pred_probas]

def preds_as_df(data, preds):
    sub = {
        "row_id": [],
        "birds": [],
    }
    
    for row, pred in zip(data.itertuples(False), preds):
        row_id = [f"{row.id}_{row.site}_{5*i}" for i in range(1, len(pred)+1)]
        sub["birds"] += pred
        sub["row_id"] += row_id
        
    sub = pd.DataFrame(sub)
    
    if SAMPLE_SUB_PATH:
        sample_sub = pd.read_csv(SAMPLE_SUB_PATH, usecols=["row_id"])
        sub = sample_sub.merge(sub, on="row_id", how="left")
        sub["birds"] = sub["birds"].fillna("nocall")
    return sub

sub = preds_as_df(data, preds)
print(sub.shape)
sub

sub.to_csv("submission.csv", index=False)

def get_metrics(s_true, s_pred):
    s_true = set(s_true.split())
    s_pred = set(s_pred.split())
    n, n_true, n_pred = len(s_true.intersection(s_pred)), len(s_true), len(s_pred)
    
    prec = n/n_pred
    rec = n/n_true
    f1 = 2*prec*rec/(prec + rec) if prec + rec else 0
    
    return {"f1": f1, "prec": prec, "rec": rec, "n_true": n_true, "n_pred": n_pred, "n": n}

if TARGET_PATH:
    sub_target = pd.read_csv(TARGET_PATH)
    sub_target = sub_target.merge(sub, how="left", on="row_id")
    
    print(sub_target["birds_x"].notnull().sum(), sub_target["birds_x"].notnull().sum())
    assert sub_target["birds_x"].notnull().all()
    assert sub_target["birds_y"].notnull().all()
    
    df_metrics = pd.DataFrame([get_metrics(s_true, s_pred) for s_true, s_pred in zip(sub_target.birds_x, sub_target.birds_y)])
    
    print(df_metrics.mean())

sub_target[sub_target.birds_y != "nocall"]

sub_target[sub_target.birds_x != "nocall"]
