# -*- coding: utf-8 -*-
"""
Created on Mon Jun 17 07:55:44 2019

@author: Chun

Usage:
    python train.py --dataroot <IMDb_folder_path> --mpath <model_output_path>
"""
import torch
import argparse
import os
import sys
import csv
import numpy as np

from torch.optim import lr_scheduler 
from torch.utils.data import DataLoader
import torchvision.transforms as transforms
import torch.nn as nn

from model_res50 import FeatureExtractorFace, Classifier 
from imdb import CandDataset, CastDataset
from tri_loss import triplet_loss
import evaluate
import evaluate_rerank
import final_eval
import utils

y = {
    'train_loss': [],
    'val_mAP': []
}

newline = '' if sys.platform.startswith('win') else '\n'

def train(castloader: DataLoader, candloader: DataLoader, cand_data, 
          feature_extractor: nn.Module, classifier: nn.Module, scheduler, optimizer, 
          epoch, device, opt, feature_dim=1024) -> (nn.Module, nn.Module, float):   
    """
      Return:
      - feature_extractor
      - classifier
      - train_loss: average with movies
    """
    scheduler.step()
    feature_extractor.train()
    classifier.train()
    
    movie_loss = 0.0
    
    for i, (cast, label_cast, mov) in enumerate(castloader, 1):
        mov = mov[0]
        num_cast = len(label_cast[0])
        running_loss = 0.0

        cand_data.set_mov_name_train(mov)

        for j, (cand, label_cand, _) in enumerate(candloader, 1):    
            bs = cand.size()[0]                         # cand.shape: batchsize, 3, 224, 224
            optimizer.zero_grad()
            
            inputs = torch.cat((cast.squeeze(0), cand), dim=0)
            label  = torch.cat((label_cast[0], label_cand), dim=0).tolist()
            inputs = inputs.to(device)
            
            # print('input size :', inputs.size())      # input.shape: batchsize, 3, 224, 224
            
            out = feature_extractor(inputs)
            out = classifier(out)
            loss = triplet_loss(out, label, num_cast)   # Size averaged loss
            loss.backward()
            optimizer.step()
            
            running_loss += loss.item() * bs
            
            if j % opt.log_interval == 0:
                print('Epoch [%d/%d] Movie [%d/%d] Iter [%d] Loss: %.4f'
                      % (epoch, opt.epochs, i, len(castloader),
                         j, running_loss / (j * bs)))
        
        movie_loss += running_loss

    return feature_extractor, classifier, movie_loss / len(castloader)
                
            
def val(castloader: DataLoader, candloader: DataLoader, cast_data, cand_data, 
        feature_extractor: nn.Module, classifier: nn.Module, criterion,
        epoch, opt, device, feature_dim=1024) -> (float, float):    
    """
      Return: 
      - mAP:
      - loss:
    """
    feature_extractor.eval()
    classifier.eval()
    
    loss = 0.0
    results = []

    with torch.no_grad():
        for i, (cast, label_cast, mov) in enumerate(castloader, 1):
            mov = mov[0]                        # Un-packing list
            
            cast = cast.to(device)              # cast.shape: 1, num_cast+1, 3, 448, 448
            cast_out = feature_extractor(cast.squeeze(0))
            cast_out = classifier(cast_out)
            cast_out = cast_out.detach().cpu().view(-1, feature_dim)
            
            cand_out  = torch.tensor([])
            index_out = torch.tensor([], dtype=torch.long)

            cand_data.set_mov_name_train(mov)

            # print("[Validating] Number of candidates should be equal to: {}".format(
            #     len(os.listdir(os.path.join(opt.dataroot, 'val', mov, 'candidates')))))

            for j, (cand, label_cand, index) in enumerate(candloader):
                cand = cand.to(device)          # cand.shape: bs, 3, 448, 448
                out = feature_extractor(cand)
                out = classifier(out)
                out = out.detach().cpu().view(-1, feature_dim)
                cand_out = torch.cat((cand_out, out), dim=0)
                index_out = torch.cat((index_out, index), dim=0)      

            print('[Validating] {}/{} {} processed, get {} features'.format(i, len(castloader), mov, cand_out.size()[0]))

            cast_feature = cast_out.to(device) #.numpy()
            candidate_feature = cand_out.to(device) #.numpy()

            # Calculate L2 Loss if needed.
            # if criterion is not None:
            #     for i in range(label_cast):
            #         pred = candidate_feature[index_out == i]
            #         gt   = cast_feature.expand_as(pred)
            #         loss += criterion(pred, gt).item()
            #     pass

            # Getting the labels name from dataframe
            cast_name = cast_data.casts
            cast_name = cast_name['index'].str[-23:-4].to_numpy()
            
            candidate_name = cand_data.all_candidates[mov]
            candidate_name = candidate_name['index'].str[-18:-4].to_numpy()
            
            result = evaluate.cosine_similarity(cast_feature, cast_name, candidate_feature, candidate_name)
            # result = evaluate_rerank.predict_1_movie(cast_feature, cast_name, candidate_feature, candidate_name)   
            results.extend(result)
    
    with open('result.csv', 'w', newline=newline) as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=['Id','Rank'])
        writer.writeheader()
        
        for r in results:
            writer.writerow(r)
    
    mAP, AP_dict = final_eval.eval('result.csv', os.path.join(opt.dataroot , "val_GT.json"))
    
    for key, val in AP_dict.items():
        record = '[Epoch {}] AP({}): {:.2%}'.format(epoch, key, val)
        print(record)
        write_record(record, 'val_seperate_AP.txt', opt.log_path)

    return mAP, loss

# ---------- #
# Save model #
# ---------- #
def save_network(network, epoch, device, opt, num_fill=3):
    # os.makedirs(opt.mpath, exist_ok=True)
    save_path = os.path.join(opt.mpath, 'net_{}.pth'.format(str(epoch).zfill(num_fill)))
    torch.save(network.cpu().state_dict(), save_path)

    if torch.cuda.is_available():
        network.to(device)

    return

def write_record(record, filename, folder):
    path = os.path.join(folder, filename)
    
    with open(path, 'a') as textfile:
        textfile.write(str(record) + '\n')

    return

# ------------- #
# main function #
# ------------- #
def main(opt):
    os.environ['CUDA_VISIBLE_DEVICES'] = str(opt.gpu)
    device = torch.device("cuda")
    
    transform1 = transforms.Compose([
                        transforms.Resize((224,224), interpolation=3),
                        transforms.ToTensor(),
                        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
                    ])

    # Candidates Datas    
    train_data = CandDataset(opt.dataroot, os.path.join(opt.dataroot, 'train'),
                                  mode='classify',
                                  drop_others=True,
                                  transform=transform1,
                                  debug=opt.debug)
    
    val_data   = CandDataset(opt.dataroot, os.path.join(opt.dataroot, 'val'),
                                  mode='classify',
                                  drop_others=False,
                                  transform=transform1,
                                  debug=opt.debug)

    train_cand = DataLoader(train_data, batch_size=opt.batchsize, shuffle=True, num_workers=opt.threads)
    val_cand   = DataLoader(val_data, batch_size=opt.batchsize, shuffle=False, num_workers=opt.threads)
    
    # Cast Datas
    train_cast_data = CastDataset(opt.dataroot, os.path.join(opt.dataroot, 'train'),
                                  mode='classify',
                                  drop_others=True,
                                  transform=transform1,
                                  debug=opt.debug,
                                  action='train')
    
    val_cast_data = CastDataset(opt.dataroot, os.path.join(opt.dataroot, 'val'),
                                  mode='classify',
                                  drop_others=False,
                                  transform=transform1,
                                  debug=opt.debug,
                                  action='train')
    
    train_cast = DataLoader(train_cast_data, batch_size=1, shuffle=False, num_workers=0)
    val_cast   = DataLoader(val_cast_data, batch_size=1, shuffle=False, num_workers=0)
    
    # Models
    feature_extractor = FeatureExtractorFace().to(device)
    classifier = Classifier(2048).to(device)
    
    optimizer = torch.optim.Adam(
                    classifier.parameters(), 
                    lr=opt.lr,
                    weight_decay=opt.weight_decay,
                    betas=(opt.b1, opt.b2)
                )
      
    scheduler = lr_scheduler.MultiStepLR(optimizer, milestones=opt.milestones, gamma=opt.gamma)
    criterion = nn.MSELoss(reduction='sum')

    # Testing pre-trained model mAP performance
    # val_mAP, val_loss = val(val_cast, val_cand,val_cast_data, val_data,
    #                         feature_extractor, classifier, criterion,
    #                         0, opt, device, feature_dim=opt.feature_dim)
    # record = 'Pre-trained Epoch [{}/{}]  Valid_mAP: {:.2%} Valid_loss: {:.4f}\n'.format(0, opt.epochs, val_mAP, val_loss)
    # print(record)
    # write_record(record, 'val_mAP.txt', opt.log_path)

    best_mAP = 0.0
    for epoch in range(1, opt.epochs + 1):
        # Dynamic adjust the loss margin
        pass

        # Train the models
        model, training_loss = train(train_cast, train_cand, train_data,
                                     feature_extractor, classifier, scheduler, optimizer,
                                     epoch, device, opt, feature_dim=opt.feature_dim)

        # Print and log the training loss
        record = 'Epoch [%d/%d] TrainingLoss: %.4f' % (epoch, opt.epochs, training_loss)
        print(record)
        write_record(record, 'train_movie_avg_loss.txt', opt.log_path )

        # Save the network
        if epoch % opt.save_interval == 0:
            save_network(classifier, epoch, device, opt)
        
        # Validate the model performatnce
        if epoch % opt.save_interval == 0:
            val_mAP, val_loss = val(val_cast, val_cand, val_cast_data, val_data, 
                                    feature_extractor, classifier, 
                                    epoch, opt, device, feature_dim=opt.feature_dim)
            
            # Print and log the validation loss
            record = 'Epoch [{}/{}]  Valid_mAP: {:.2%} Valid_loss: {:.4f}\n'.format(epoch, opt.epochs, val_mAP, val_loss)
            print(record)
            write_record(record, 'val_mAP.txt', opt.log_path)
    
            # Save the best model
            if val_mAP > best_mAP:
                save_path = os.path.join(opt.mpath, 'net_best.pth')
                torch.save(classifier.cpu().state_dict(), save_path)
    
                if torch.cuda.is_available():
                    model.to(device)
                
                val_mAP = best_mAP
        
if __name__ == '__main__':
    
    parser = argparse.ArgumentParser(description='Training')
    # Model Setting
    # parser.add_argument('--drop_others', action='store_true', help='if true, the image of type others will be keeped.')
    # parser.add_argument('--fp16', action='store_true', help='use float16 instead of float32, which will save about 50% memory' )
    # parser.add_argument('--droprate', default=0.5, type=float, help='drop rate')
    # parser.add_argument('--img_size', default=[448, 448], type=int, nargs='*')

    # Training setting
    parser.add_argument('--batchsize', default=64, type=int, help='batchsize in training')
    parser.add_argument('--lr', default=5e-5, type=float, help='learning rate')
    parser.add_argument('--milestones', default=[10, 20, 30], nargs='*', type=int)
    parser.add_argument('--gamma', default=0.1, type=float)
    parser.add_argument('--epochs', default=100, type=int)
    parser.add_argument('--weight_decay', default=5e-4, type=float)
    parser.add_argument('--momentum', default=0.9, type=float)
    parser.add_argument('--b1', default=0.9, type=float)
    parser.add_argument('--b2', default=0.999, type=float)
    parser.add_argument('--feature_dim', default=1024, type=int)
    
    # I/O Setting (important !!!)
    parser.add_argument('--mpath',  default='models', help='folder to output images and model checkpoints')
    parser.add_argument('--log_path',  default='log', help='folder to output loss record')
    parser.add_argument('--dataroot', default='./IMDb_Resize/', type=str, help='Directory of dataroot')
    # parser.add_argument('--gt_file', default='./IMDb_Resize/val_GT.json', type=str, help='Directory of training set.')
    # parser.add_argument('--resume', type=str, help='If true, resume training at the checkpoint')
    # parser.add_argument('--trainset', default='/media/disk1/EdwardLee/dataset/IMDb_Resize/train', type=str, help='Directory of training set.')
    # parser.add_argument('--valset', default='/media/disk1/EdwardLee/dataset/IMDb_Resize/val', type=str, help='Directory of validation set')
    
    # Device Setting
    parser.add_argument('--gpu', default=0, nargs='*', type=int, help='')
    parser.add_argument('--threads', default=0, type=int)

    # Others Setting
    parser.add_argument('--debug', action='store_true', help='use debug mode (print shape)' )
    parser.add_argument('--log_interval', default=10, type=int)
    parser.add_argument('--save_interval', default=3, type=int, help='Validation and save the network')

    opt = parser.parse_args()

    # Make directories
    os.makedirs(opt.log_path, exist_ok=True)
    os.makedirs(opt.mpath, exist_ok=True)

    # Show the settings, and start to train
    utils.details(opt)
    main(opt)
