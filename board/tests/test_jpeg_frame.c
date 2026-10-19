#include "../jpeg_frame.h"
#include <assert.h>
#include <stdio.h>
int main(void) {
    const unsigned char valid[]={0xff,0xd8,0xff,0xe0,0,4,0xff,0xd9,0xff,0xda,0,2,1,2,0xff,0,0xd9,0xff,0xd0,3,0xff,0xd9,0,0};
    assert(jpeg_complete_size(valid,sizeof(valid))==22);
    assert(jpeg_complete_size(valid,20)==0); /* Thumbnail EOI cannot hide truncated image. */
    assert(jpeg_complete_size(valid,21)==0);
    const unsigned char broken[]={0xff,0xd8,0xff,0xe0,0xff,0xff,0xff,0xd9};
    assert(jpeg_complete_size(broken,sizeof(broken))==0);
    const unsigned char rescanned[]={0xff,0xd8,0xff,0xda,0,2,1,0xff,0xc4,0,2,0xff,0xda,0,2,2,0xff,0xd9};
    assert(jpeg_complete_size(rescanned,sizeof(rescanned))==sizeof(rescanned));
    const unsigned char no_scan[]={0xff,0xd8,0xff,0xd9};
    assert(jpeg_complete_size(no_scan,sizeof(no_scan))==0);
    puts("PASS JPEG framing: padding, stuffed bytes, restart, APP thumbnail, truncation and multiple scans");
}
