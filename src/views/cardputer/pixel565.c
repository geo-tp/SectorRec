/* SectorRec framebuffer RGB888->RGB565 blit.
 * Deliberately freestanding: no libc or allocation, no syscalls.
 * The caller validates all source and destination bounds.
 */
#include <stdint.h>
#include <stddef.h>

void sr_blit_rgb16(const uint8_t *src, uint8_t *dst,
                   size_t width, size_t height, size_t dst_stride,
                   unsigned red_offset, unsigned red_bits,
                   unsigned green_offset, unsigned green_bits,
                   unsigned blue_offset, unsigned blue_bits)
{
    for (size_t y = 0; y < height; ++y) {
        uint8_t *out = dst + y * dst_stride;
        const uint8_t *row = src + y * width * 3;
        for (size_t x = 0; x < width; ++x) {
            unsigned r = row[3*x] >> (8u-red_bits);
            unsigned g = row[3*x+1] >> (8u-green_bits);
            unsigned b = row[3*x+2] >> (8u-blue_bits);
            uint16_t p = (uint16_t)((r<<red_offset)|(g<<green_offset)|(b<<blue_offset));
            out[2*x] = (uint8_t)p;
            out[2*x+1] = (uint8_t)(p>>8);
        }
    }
}
