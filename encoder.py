'''Encode target locations and labels.'''
import torch

import math
import itertools

class DataEncoder:
    def __init__(self, input_size, feature_map_sizes, aspect_ratios):
        '''Compute default box sizes with scale and aspect transform.

        Args:
          input_size: (int) net input size.
          feature_map_sizes: (list) size of each intermediate layer output.
          aspect_ratios: (list) aspect ratios.

        Returns:
            (tensor) of default boxes, sized [8732,4].
        '''
        scale = 300.
        steps = [s / scale for s in (8, 16, 32, 64, 100, 300)]
        sizes = [s / scale for s in (30, 60, 111, 162, 213, 264, 315)]
        aspect_ratios = ((2,), (2, 3), (2, 3), (2, 3), (2,), (2,))
        feature_map_sizes_ = (38, 19, 10, 5, 3, 1)

        num_layers = len(feature_map_sizes)
        #min_ratio = 20
        #max_ratio = 90
        #step = (max_ratio-min_ratio) / (num_layers-2)

        #min_sizes = [input_size * 0.1]
        #max_sizes = [input_size * 0.2]
        #for ratio in range(min_ratio, max_ratio+1, step):
        #    min_sizes.append(input_size * ratio / 100.)
        #    max_sizes.append(input_size * (ratio+step) / 100.)

        boxes = []
        for i in range(num_layers):
            fmsize = feature_map_sizes_[i]
            for h,w in itertools.product(range(fmsize), repeat=2):
                cx = (w + 0.5)*steps[i] 
                cy = (h + 0.5)*steps[i]

                s = sizes[i]
                boxes.append((cx, cy, s, s))

                s = math.sqrt(sizes[i] * sizes[i+1])
                boxes.append((cx, cy, s, s))

                s = sizes[i] 
                for ar in aspect_ratios[i]:
                    boxes.append((cx, cy, s * math.sqrt(ar), s / math.sqrt(ar)))
                    boxes.append((cx, cy, s / math.sqrt(ar), s * math.sqrt(ar)))

        self.default_boxes = torch.Tensor(boxes)

    def iou(self, box1, box2):
        '''Compute the intersection over union of two set of boxes, each box is [x1,y1,x2,y2].

        Args:
          box1: (tensor) bounding boxes, sized [N,4].
          box2: (tensor) bounding boxes, sized [M,4].

        Return:
          (tensor) iou, sized [N,M].
        '''
        N = box1.size(0)
        M = box2.size(0)

        lt = torch.max(
            box1[:,:2].unsqueeze(1).expand(N,M,2),  # [N,2] -> [N,1,2] -> [N,M,2]
            box2[:,:2].unsqueeze(0).expand(N,M,2),  # [M,2] -> [1,M,2] -> [N,M,2]
        )

        rb = torch.min(
            box1[:,2:].unsqueeze(1).expand(N,M,2),  # [N,2] -> [N,1,2] -> [N,M,2]
            box2[:,2:].unsqueeze(0).expand(N,M,2),  # [M,2] -> [1,M,2] -> [N,M,2]
        )

        wh = rb - lt  # [N,M,2]
        wh[wh<0] = 0  # clip at 0
        inter = wh[:,:,0] * wh[:,:,1]  # [N,M]

        area1 = (box1[:,2]-box1[:,0]) * (box1[:,3]-box1[:,1])  # [N,]
        area2 = (box2[:,2]-box2[:,0]) * (box2[:,3]-box2[:,1])  # [M,]
        area1 = area1.unsqueeze(1).expand_as(inter)  # [N,] -> [N,1] -> [N,M]
        area2 = area2.unsqueeze(0).expand_as(inter)  # [M,] -> [1,M] -> [N,M]

        iou = inter / (area1 + area2 - inter)
        return iou

    def encode(self, boxes, classes, threshold=0.5):
        '''Transform target bounding boxes and class labels to SSD boxes and classes.

        Match each object box to all the default boxes, pick the ones with the
        Jaccard-Index > 0.5:
            Jaccard(A,B) = AB / (A+B-AB)

        Args:
          boxes: (tensor) object bounding boxes (xmin,ymin,xmax,ymax) of a image, sized [#obj, 4].
          classes: (tensor) object class labels of a image, sized [#obj,].
          threshold: (float) Jaccard index threshold

        Returns:
          boxes: (tensor) bounding boxes, sized [#obj, 8732, 4].
          classes: (tensor) class labels, sized [8732,]
        '''
        default_boxes = self.default_boxes
        num_default_boxes = default_boxes.size(0)
        num_objs = boxes.size(0)

        iou = self.iou(  # [#obj,8732]
            boxes,
            torch.cat([default_boxes[:,:2] - default_boxes[:,2:]/2,
                       default_boxes[:,:2] + default_boxes[:,2:]/2], 1)
        )

        iou, max_idx = iou.max(0)  # [1,8732]
        max_idx.squeeze_(0)        # [8732,]
        iou.squeeze_(0)            # [8732,]

        boxes = boxes[max_idx]     # [8732,4]
        variances = [0.1, 0.2]
        cxcy = (boxes[:,:2] + boxes[:,2:])/2 - default_boxes[:,:2]  # [8732,2]
        cxcy /= variances[0] * default_boxes[:,2:]
        wh = (boxes[:,2:] - boxes[:,:2]) / default_boxes[:,2:]      # [8732,2]
        wh = torch.log(wh) / variances[1]
        loc = torch.cat([cxcy, wh], 1)  # [8732,4]

        conf = 1 + classes[max_idx]   # [8732,], background class = 0
        conf[iou<threshold] = 0       # background
        return loc, conf
