import os
import random

import numpy as np
import torch
from torch.utils.data import DataLoader
from torchvision import transforms

from medclip import constants
from medclip.dataset import SuperviseImageDataset, SuperviseImageCollator
from medclip.evaluator import Evaluator
from medclip.modeling_medclip import MedClipVisionModel, MedClipVisionModelViT, MedClipClassifier
from medclip.trainer import Trainer

# set random seed
seed = 42
random.seed(seed)
np.random.seed(seed)
torch.manual_seed(seed)
torch.cuda.manual_seed(seed)
os.environ['PYTHONASHSEED'] = str(seed)
os.environ['TOKENIZERS_PARALLELISM'] = 'False'

# set cuda devices
os.environ['CUDA_VISIBLE_DEVICES'] = '1'
device = "cuda:0" if torch.cuda.is_available() else "cpu"

# setup training configurations
train_config = {
    'batch_size': 64,
    'num_epochs': 10,
    'warmup': 0.1,  # the first 10% of training steps are used for warm-up
    'lr': 5e-4,
    'weight_decay': 0,
    'eval_batch_size': 256,
    'eval_steps': 50,
    'save_steps': 50,
}

# setup config
vit = False

# uncomment the following block for experiments
# dataname = 'chexpert-5x200'
# dataname = 'mimic-5x200'
dataname = 'covid'
# dataname = 'covid-2x200'
# dataname = 'rsna-balanced'
# dataname = 'rsna-2x200'

if dataname in ['chexpert-5x200', 'mimic-5x200']:
    tasks = constants.CHEXPERT_COMPETITION_TASKS
    num_class = 5
    mode = 'multiclass'
    train_dataname = f'{dataname}-finetune'
    val_dataname = dataname
elif dataname == 'covid':
    tasks = constants.COVID_TASKS
    num_class = 2
    mode = 'binary'
    """ option 1: use entire training data """
    # train_dataname = f'{dataname}-train'
    """ option 2: use x% training data """
    # train_dataname = f'{dataname}-0.1-train'
    train_dataname = f'{dataname}-0.2-train'
    val_dataname = f'{dataname}-test'
elif dataname == 'covid-2x200':
    tasks = constants.COVID_TASKS
    num_class = 2
    mode = 'binary'
    train_dataname = f'{dataname}-train'
    val_dataname = f'{dataname}-test'
elif dataname in ['rsna-balanced', 'rsna-2x200']:
    tasks = constants.RSNA_TASKS
    num_class = 2
    mode = 'binary'
    train_dataname = f'{dataname}-train'
    val_dataname = f'{dataname}-test'
else:
    raise NotImplementedError

# load the pretrained model and build the classifier
if vit:
    vision_model = MedClipVisionModelViT(
        medclip_checkpoint='/srv/local/data/MedCLIP/checkpoints/vision_text_pretrain/25000/'
    )
    clf = MedClipClassifier(vision_model,
                            num_class=num_class,
                            mode=mode,
                            input_dim=768)
else:
    vision_model = MedClipVisionModel(
        medclip_checkpoint='/srv/local/data/MedCLIP/checkpoints/vision_text_pretrain/21000/'
    )
    clf = MedClipClassifier(vision_model,
                            num_class=num_class,
                            mode=mode,
                            input_dim=512)
clf.cuda()
for name, param in clf.named_parameters():
    if name not in ['fc.weight', 'fc.bias']:
        param.requires_grad = False

# build dataloader
transform = transforms.Compose([
    transforms.RandomHorizontalFlip(0.5),
    transforms.ColorJitter(0.2, 0.2),
    transforms.RandomAffine(degrees=10, scale=(0.8, 1.1), translate=(0.0625, 0.0625)),
    transforms.Resize((256, 256)),
    transforms.RandomCrop((constants.IMG_SIZE, constants.IMG_SIZE)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[constants.IMG_MEAN], std=[constants.IMG_STD])],
)

train_data = SuperviseImageDataset([train_dataname],
                                   class_names=tasks,
                                   imgtransform=transform)
trainloader = DataLoader(train_data, batch_size=train_config['batch_size'],
                         shuffle=True,
                         collate_fn=SuperviseImageCollator(mode=mode),
                         num_workers=8,
                         )
val_data = SuperviseImageDataset([val_dataname],
                                 class_names=tasks,
                                 )
valloader = DataLoader(val_data, batch_size=train_config['eval_batch_size'],
                       shuffle=False,
                       collate_fn=SuperviseImageCollator(mode=mode),
                       num_workers=4,
                       )

# build objective
train_objectives = [(trainloader, clf, 1)]
model_save_path = f'./checkpoints/{dataname}-linear-probe'

# build trainer
trainer = Trainer()

evaluator = Evaluator(
    medclip_clf=clf,
    eval_dataloader=valloader,
    mode=mode,
)
trainer.train(
    clf,
    train_objectives=train_objectives,
    warmup_ratio=train_config['warmup'],
    epochs=train_config['num_epochs'],
    optimizer_params={'lr': train_config['lr']},
    output_path=model_save_path,
    evaluation_steps=train_config['eval_steps'],
    weight_decay=train_config['weight_decay'],
    save_steps=train_config['save_steps'],
    evaluator=evaluator,
    eval_dataloader=valloader,
    use_amp=False,
)

# complete training and start to evaluate
res = evaluator.evaluate()
for key in res.keys():
    if key not in ['pred', 'labels']:
        print(f'{key}: {res[key]}')
