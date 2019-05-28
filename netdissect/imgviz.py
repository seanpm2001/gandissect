import PIL, torch
from netdissect import upsample, renormalize
from torchvision import transforms
from matplotlib import cm

# to use:
# def activation_surface(data, target_shape=None, source_shape=None,
#         scale_offset=None, deg=1, pad=True):
# def activation_visualization(image, data, level, alpha=0.5, source_shape=None,
#        target_shape=None, crop=False, zoom=None, border=2,
#        negate=False, return_mask=False, **kwargs)

class ImageVisualizer:
    def __init__(self, size, input_size=None, data_size=None,
            renormalizer=None, scale_offset=None, level=None, actrange=None,
            source=None, convolutions=None, quantiles=None):
        if input_size is None and source is not None:
            input_size = input_size_from_source(source)
        if renormalizer is None and source is not None:
            renormalizer = renormalize.renormalizer(source=source, mode='byte')
        if scale_offset is None and convolutions is not None:
            scale_offset = upsample.sequence_scale_offset(convolutions)
        if data_size is None and convolutions is not None:
            data_size = upsample.sequence_data_size(convolutions, input_size)
        if level is None and quantiles is not None:
            level = quantiles.quantiles([0.99])[:,0]
        if actrange is None and quantiles is not None:
            actrange = quantiles.quantiles([0.01, 0.99])
        self.size = size
        self.input_size = input_size
        self.data_size = data_size
        self.renormalizer = renormalizer
        self.scale_offset = scale_offset
        self.level = level
        self.actrange = actrange
        self.upsampler = None
        if self.data_size is not None:
            self.upsampler = upsample.upsampler(data_size, size,
                    input_shape=self.input_size,
                    scale_offset=scale_offset)

    def heatmap(self, activations, unit=None, mode='bilinear'):
        amin, amax = self.range_for(activations, unit)
        if unit is None:
            a = activations
        else:
            a = activations[unit]
        upsampler = self.upsampler_for(a)
        a = upsampler(a[None,None,...], mode=mode)[0,0].cpu()
        return PIL.Image.fromarray(
                (cm.hot((a - amin) / (1e-10 + amax - amin)) * 255
                    ).astype('uint8'))

    def segmentation(self, segmentations, label=None):
        # returns a color-coded segmentation
        pass

    def image(self, imagedata):
        return PIL.Image.fromarray(self.scaled_image(imagedata)
                .permute(1, 2, 0).byte().cpu().numpy())

    def masked_image(self, imagedata, activations, unit=None):
        scaled_image = self.scaled_image(imagedata).float()
        mask = self.pytorch_mask(activations, unit)
        border = border_from_mask(mask)
        inside = mask & (~border)
        outside = ~mask & (~border)
        inside, outside, border = [d.float() for d in [inside, outside, border]]
        yellow = torch.tensor([255.0, 255.0, 0],
                dtype=border.dtype, device=border.device)[:,None,None]
        result_image = (
                scaled_image * inside +
                yellow * border +
                0.5 * scaled_image * outside).clamp(0, 255).byte()
        return PIL.Image.fromarray(
            result_image.permute(1, 2, 0).cpu().numpy())

    def masked_segmentation(self, imagedata, activations, unit):
        # returns a dissection-style image overlay
        pass

    def pytorch_mask(self, activations, unit):
        if unit is None:
            a = activations
        else:
            a = activations[unit]
        level = self.level_for(activations, unit)
        upsampler = self.upsampler_for(a)
        return (upsampler(a[None, None,...])[0,0] > level)

    def scaled_image(self, imagedata):
        if len(imagedata.shape) == 4:
            imagedata = imagedata[0]
        renormalizer = self.renormalizer_for(imagedata)
        return torch.nn.functional.interpolate(
                renormalizer(imagedata).float()[None,...],
                size=self.size)[0]

    def upsampler_for(self, a):
        if self.upsampler is not None:
            return self.upsampler
        return upsample.upsampler(a.shape, self.size,
                    input_shape=self.input_size,
                    scale_offset=self.scale_offset,
                    dtype=a.dtype, device=a.device)

    def range_for(self, activations, unit):
        if unit is not None and self.actrange is not None:
            if hasattr(unit, '__len__'):
                unit = unit[1]
            return self.actrange[unit]
        return activations.min(), activations.max()

    def level_for(self, activations, unit):
        if unit is not None and self.level is not None:
            if hasattr(unit, '__len__'):
                unit = unit[1]
            return self.level[unit]
        s, _ = activations.view(-1).sort()
        return s[int(len(s) * 0.99)]

    def renormalizer_for(self, image):
        if self.renormalizer is not None:
            return self.renormalizer
        return renormalize.renormalizer('zc', 'rgb')

def border_from_mask(a):
    out = torch.zeros_like(a)
    h = (a[:-1,:] != a[1:,:])
    v = (a[:,:-1] != a[:,1:])
    out[:-1,:] |= h
    out[1:,:] |= h
    out[:,:-1] |= v
    out[:,1:] |= v
    return out

def input_size_from_source(source):
    sizer = find_sizer(source)
    size = sizer.size
    if hasattr(size, '__len__'):
        return size
    return size

def find_sizer(source):
    '''
    Crawl around the transforms attached to a dataset looking for
    the last crop or resize transform to return.
    '''
    if source is None:
        return None
    if isinstance(source, (transforms.Resize, transforms.RandomCrop,
        transforms.RandomResizedCrop, transforms.CenterCrop)):
        return source
    t = getattr(source, 'transform', None)
    if t is not None:
        return reverse_normalize_from_transform(t)
    ts = getattr(source, 'transforms', None)
    if ts is not None:
        for t in reversed(ts):
            result = find_sizer(t)
            if result is not None:
                return result
    return None
