from __future__ import print_function, division, absolute_import

import numpy as np


def read(path):
    """
    Return
    ------
    Always return 3D images
    (n_frames, channels, width, height)
    """
    import imageio
    vid = imageio.get_reader(path)
    metadata = vid.get_meta_data()
    fps = metadata['fps']
    try:
        frames = []
        for i in vid:
            # it is bizzare why width and height are swapped
            if i.ndim == 3: # swap channel first
                i = i.transpose(2, 1, 0)
            else:
                i = np.expand_dims(i.transpose(1, 0), 1)
            frames.append(i)
    except RuntimeError:
        pass
    frames = np.array(frames, dtype=frames[0].dtype)
    return frames, fps