"""Direct Linux framebuffer driver; no X11, Wayland, DRM master or SDL needed."""
import ctypes
import fcntl
import mmap
import os
import struct
from pathlib import Path
from PIL import Image


def load_native_blitter(paths=None):
    """Native RGB565 conversion releases the GIL during LCD output.

    The ARM64 .so ships in the .deb. Fallback remains for development and
    non-ARM64 preview environments. No runtime compilation required.
    """
    if paths is None:
        paths = [Path("/usr/lib/sectorrec/libsectorrec565.so"),
                 Path(__file__).with_name("libsectorrec565.so")]
    for path in paths:
        try:
            native = ctypes.CDLL(str(path))
            blit = native.sr_blit_rgb16
            blit.argtypes = [ctypes.c_char_p, ctypes.c_void_p,
                             ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t,
                             ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
                             ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
            blit.restype = None
            # Return library too, so it remains alive for as long as blit is used.
            return native, blit
        except (OSError, AttributeError):
            continue
    return None, None

W,H=320,170

class Framebuffer:
    def __init__(self,path=None):
        self.fd=os.open(path or os.environ.get('SECTORREC_FB','/dev/fb0'),os.O_RDWR|os.O_CLOEXEC)
        try:
            var=bytearray(160)
            fix=bytearray(80)
            fcntl.ioctl(self.fd,0x4600,var,True)
            fcntl.ioctl(self.fd,0x4602,fix,True)
            self.width,self.height,_,_,self.xoffset,self.yoffset=struct.unpack_from('6I',var,0)
            self.bpp=struct.unpack_from('I',var,24)[0]
            self.r=(struct.unpack_from('I',var,32)[0],struct.unpack_from('I',var,36)[0])
            self.g=(struct.unpack_from('I',var,44)[0],struct.unpack_from('I',var,48)[0])
            self.b=(struct.unpack_from('I',var,56)[0],struct.unpack_from('I',var,60)[0])
            self.line=struct.unpack_from('I',fix,48)[0]
            length=struct.unpack_from('I',fix,24)[0]
            if self.width < 1 or self.height < 1 or self.bpp not in (16,32):
                raise RuntimeError('Unsupported LCD pixel format')
            need=self.line*(self.yoffset+self.height)
            self.map=mmap.mmap(self.fd,max(length,need),access=mmap.ACCESS_WRITE)
            self.native_library, self.native_blit = load_native_blitter()
            self.native_frames = 0
            self.fallback_frames = 0
        except BaseException:
            os.close(self.fd)
            raise

    def render(self,im):
        if (self.width,self.height)!=(W,H):
            im=im.resize((self.width,self.height),Image.Resampling.BILINEAR)
        if self.bpp==16 and self.native_blit is not None and all(
                1 <= bits <= 8 and 0 <= offset < 16 and offset+bits <= 16
                for offset, bits in (self.r, self.g, self.b)):
            # Mirror the fast RGB565 framebuffer approach of Retro-Zero:
            # convert and blit in native code, directly to the mapped fb0.
            # ctypes releases the Python GIL for this entire call.
            src = im.tobytes('raw', 'RGB')
            start = self.yoffset*self.line + self.xoffset*2
            stride = self.width*2
            if start < 0 or self.line < stride or start + (self.height-1)*self.line + stride > len(self.map):
                raise RuntimeError('Framebuffer size/stride out of range')
            mapped = (ctypes.c_uint8 * len(self.map)).from_buffer(self.map)
            try:
                self.native_blit(src, ctypes.byref(mapped, start),
                                 self.width, self.height, self.line,
                                 self.r[0], self.r[1], self.g[0], self.g[1],
                                 self.b[0], self.b[1])
            finally:
                del mapped  # Do not keep an exported mmap buffer on close().
            self.native_frames += 1
            return
        self.fallback_frames += 1
        if self.bpp==32 and self.r==(16,8) and self.g==(8,8) and self.b==(0,8):
            pixels=im.tobytes('raw','BGRX')
        elif self.bpp==16:
            src=im.tobytes('raw','RGB')
            out=bytearray(self.width*self.height*2)
            ro,rl=self.r; go,gl=self.g; bo,bl=self.b
            pos=0
            for i in range(0,len(src),3):
                v=((src[i]>>(8-rl))<<ro)|((src[i+1]>>(8-gl))<<go)|((src[i+2]>>(8-bl))<<bo)
                out[pos]=v&255;out[pos+1]=(v>>8)&255
                pos+=2
            pixels=out
        else:
            # Generic 32-bit fb_var_screeninfo color bitfields
            src=im.tobytes('raw','RGB')
            out=bytearray(self.width*self.height*4)
            ro,rl=self.r;go,gl=self.g;bo,bl=self.b
            pos=0
            for i in range(0,len(src),3):
                v=((src[i]>>(8-rl))<<ro)|((src[i+1]>>(8-gl))<<go)|((src[i+2]>>(8-bl))<<bo)
                struct.pack_into('<I',out,pos,v)
                pos+=4
            pixels=out
        stride=self.width*(self.bpp//8)
        start=self.yoffset*self.line + self.xoffset*(self.bpp//8)
        for y in range(self.height):
            offset=start+y*self.line
            self.map[offset:offset+stride]=pixels[y*stride:(y+1)*stride]

    def close(self):
        self.map.close()
        os.close(self.fd)
