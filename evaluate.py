"""
  FileName     [ evaluate.py ]
  PackageName  [ final ]
  Synopsis     [ Normalization function ]
"""

import torch
import numpy as np
from numpy import linalg as LA

def normalize_tensor(a: torch.Tensor, dim: int) -> torch.Tensor:
    """
      Normalize the tensor as unit vector
    """
    norm = torch.norm(a, dim=dim, keepdims=True)
    a = a / norm.expand_as(a)

    return a

def normalize_ndarray(a: np.ndarray, axis: int) -> np.ndarray:
    """
      Normalize the ndarray as unit vector
    """
    norm = LA.norm(a, axis=axis, keepdims=True)
    a = a / np.repeat(norm, a.shape[axis], axis=axis)
    
    return a

def cosine_similarity(cast_feature: torch.Tensor, cast_name: np.ndarray, candidate_feature: torch.Tensor, candidate_name: np.ndarray) -> list:
    """
      Using cosine_similarity to sorting the query priorities.

      Return:
      - result: {'Id': 'Rank'} dicts in list
    """
    result = []

    cast_feature      = normalize_tensor(cast_feature, dim=1)
    candidate_feature = normalize_tensor(candidate_feature, dim=1)
    
    distance = torch.mm(cast_feature, candidate_feature.transpose(0, 1))
    index    = torch.argsort(distance, dim=1, descending=True).numpy()

    # Return the index with the descending priority 
    # np.argsort() only support ascending sorting
    # distance = np.dot(cast_feature, np.transpose(candidate_feature))
    # distance = distance * (-1)
    # index = np.argsort(distance, axis=1)

    print("Distance.shape: ", distance.shape)
    print(distance)
    print(index)

    for i in range(index.shape[0]):
        result.append({
            'Id': cast_name[i],
            'Rank': ' '.join(candidate_name[index[i]])
        })

    return result
