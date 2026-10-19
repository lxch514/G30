#ifndef G30_JPEG_FRAME_H
#define G30_JPEG_FRAME_H
#include <stddef.h>

/* Return the complete JPEG length (without padding), or zero for a truncated
 * marker stream. This checks framing, not the validity of all entropy codes.
 * EOI bytes inside APP/COM data must not be mistaken for the image's end. */
static size_t jpeg_complete_size(const unsigned char *data, size_t size) {
    if (size<4 || data[0]!=0xff || data[1]!=0xd8) return 0;
    size_t pos=2;
    int scan=0, saw_scan=0;
    while (pos<size) {
        if (scan) {
            while (pos<size && data[pos]!=0xff) pos++;
        } else if (data[pos]!=0xff) return 0;
        if (pos==size) return 0;
        while (pos<size && data[pos]==0xff) pos++;
        if (pos==size) return 0;
        unsigned marker=data[pos++];
        if (scan && (marker==0 || (marker>=0xd0 && marker<=0xd7))) continue;
        if (marker==0xd9) return saw_scan?pos:0;
        if (marker==0 || marker==0xd8 || (marker>=0xd0 && marker<=0xd7)) return 0;
        if (marker==1) continue;
        if (pos+2>size) return 0;
        size_t length=((size_t)data[pos]<<8)|data[pos+1];
        if (length<2 || length>size-pos) return 0;
        pos+=length;
        scan=marker==0xda;
        if (scan) saw_scan=1;
    }
    return 0;
}
#endif
