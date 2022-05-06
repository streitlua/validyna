import os

import dysts.base
import dysts.flows
import pytorch_lightning as pl
from dysts.base import DynSysDelay
from pytorch_lightning.callbacks import EarlyStopping
from pytorch_lightning.loggers import WandbLogger
from torch.utils.data import TensorDataset, DataLoader, random_split
from tqdm import tqdm

from config import ROOT_DIR
from ecodyna.data import TripletDataset, build_sliced_dataset, load_or_generate_and_save
from ecodyna.mutitask_models import MyRNN
from ecodyna.pl_wrappers import LightningFeaturizer, LightningClassifier

if __name__ == '__main__':
    params = {
        'experiment': {
            'project': 'lstm-forecasting-comparison',
            'train_part': 0.75,
            'random_seed': 26,
            'n_splits': 5
        },
        'data': {
            'attractor': 'Lorenz',
            'trajectory_count': 100,
            'trajectory_length': 1000,
            'resample': True,
            'pts_per_period': 50,
            'ic_noise': 0.01
        },
        'models': {
            'common': {
                'n_features': 5,
                'n_layers': 1
            },
            'list': [
                (MyRNN, {'model': 'GRU'}),
                (MyRNN, {'model': 'LSTM'})
            ]
        },
        'dataloader': {
            'batch_size': 64,
            'num_workers': 8
        },
        'trainer': {
            'max_epochs': 50,
            'deterministic': True,
            'callbacks': [EarlyStopping('val_loss', patience=5)]
        },
        'metric_loggers': [],
        'in_out': {
            'n_in': 5
        }
    }
    params['models']['common'].update(params['in_out'])

    # Sets random seed for random, numpy and torch
    pl.seed_everything(params['experiment']['random_seed'], workers=True)

    if not os.path.isdir(f'{ROOT_DIR}/results'):
        os.mkdir(f'{ROOT_DIR}/results')

    train_size = int(params['experiment']['train_part'] * params['data']['trajectory_count'])
    val_size = params['data']['trajectory_count'] - train_size

    attractors_per_dim = {}
    for attractor_name in dysts.base.get_attractor_list():
        attractor = getattr(dysts.flows, attractor_name)()

        # For speedup TODO remove
        if hasattr(attractor, '_postprocessing') or isinstance(attractor, DynSysDelay):
            continue

        space_dim = len(attractor.ic)

        if space_dim not in attractors_per_dim:
            attractors_per_dim[space_dim] = []
        attractors_per_dim[space_dim].append(attractor)

    for space_dim, attractors in attractors_per_dim.items():
        datasets = {}
        print(f'Generating trajectories for attractors of dimension {space_dim}')
        for attractor in tqdm(attractors):
            attractor_x0 = attractor.ic.copy()
            data = load_or_generate_and_save(attractor, **params['data'])
            datasets[attractor.name] = TensorDataset(data)

        for split in range(params['experiment']['n_splits']):
            datasets = {
                attractor_name: dict(zip(['train', 'val'], random_split(dataset, [train_size, val_size])))
                for attractor_name, dataset in datasets.items()
            }
            train_datasets = {attractor_name: dataset['train'] for attractor_name, dataset in datasets.items()}
            val_datasets = {attractor_name: dataset['val'] for attractor_name, dataset in datasets.items()}

            # TODO
            train_datasets

            for Model, model_params in params['models']['all']:
                model = Model(space_dim=space_dim, **model_params, **params['models']['common'])

                wandb_logger = WandbLogger(
                    save_dir=f'{ROOT_DIR}/results',
                    project='featurization-triplet-loss',
                    name=f'{model.name()}_dim_{space_dim}_split_{split + 1}'
                )

                wandb_logger.experiment.config.update({
                    'split_n': split + 1,
                    'featurizer': {'name': model.name(), **model.hyperparams},
                    'data': params['data'],
                    'dataloader': params['dataloader'],
                    'experiment': params['experiment'],
                    'trainer': {k: f'{v}' for k, v in params['trainer']}
                })

                model_trainer = pl.Trainer(logger=wandb_logger, deterministic=True, **params['trainer'])
                classifier = LightningClassifier(model=model)
                model_trainer.fit(classifier, train_dataloaders=, val_dataloaders=)

                wandb_logger.experiment.finish(quiet=True)
