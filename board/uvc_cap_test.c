#define _POSIX_C_SOURCE 200809L
#include <libuvc/libuvc.h>
#include <libusb-1.0/libusb.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <signal.h>
#include <time.h>
#include <unistd.h>
#include <pthread.h>
#include <sys/stat.h>
#include <string.h>
#include "jpeg_frame.h"

#define MAX_CAMERAS 6
struct candidate {
    uvc_device_t *device;
    uint8_t bus, address, ports[8];
    int port_count;
};
static int compare_camera(const void *left, const void *right) {
    const struct candidate *a=left, *b=right;
    if (a->bus!=b->bus) return (int)a->bus-(int)b->bus;
    /* Keep the original hub's ports 4, 3, 2 in the existing display order. */
    for (int i=0;i<a->port_count && i<b->port_count;i++)
        if (a->ports[i]!=b->ports[i]) return (int)b->ports[i]-(int)a->ports[i];
    if (a->port_count!=b->port_count) return b->port_count-a->port_count;
    return (int)a->address-(int)b->address;
}

static volatile sig_atomic_t stopping = 0;
static void on_signal(int sig) { (void)sig; stopping = 1; }
struct stats {
    pthread_mutex_t lock;
    unsigned long frames, bytes, invalid;
    unsigned long received_frames, incomplete;
    unsigned saved;
    int id;
    int publish;
    int width, height, fps, payload, requested_width, requested_height, requested_fps, control_error;
    unsigned long previous_frames, previous_bytes;
    unsigned long long revision;
    double previous_at, last_frame_at;
    const char *control_status;
    char prefix[256];
};
static double monotonic_seconds(void) {
    struct timespec now; clock_gettime(CLOCK_MONOTONIC,&now);
    return now.tv_sec+now.tv_nsec/1e9;
}
static void publish_state(struct stats *s) {
    if (!s->publish) return;
    char temp[320], final[320];
    snprintf(temp,sizeof(temp),"%s/cam%d-state.tmp",s->prefix,s->id);
    snprintf(final,sizeof(final),"%s/cam%d-state.json",s->prefix,s->id);
    FILE *state=fopen(temp,"w");
    if (!state) return;
    pthread_mutex_lock(&s->lock);
    double now=monotonic_seconds(), elapsed=now-s->previous_at;
    if (elapsed<.001) elapsed=.001;
    fprintf(state,"{\"ok\":true,\"result\":{\"encoder\":{\"type\":\"CAMERA_MJPEG\"},\"source\":{\"resolution\":{\"width\":%d,\"height\":%d},\"online\":%s,\"desired_fps\":%d,\"captured_fps\":%.2f,\"captured_mbps\":%.3f,\"frames\":%lu,\"bytes\":%lu,\"bad_headers\":%lu,\"received_frames\":%lu,\"incomplete_frames\":%lu,\"payload_limit\":%d},\"configuration\":{\"revision\":\"%llu\",\"status\":\"%s\",\"error\":%d,\"width\":%d,\"height\":%d,\"fps\":%d}}}\n",
        s->width,s->height,now-s->last_frame_at<2?"true":"false",s->fps,
        (s->frames-s->previous_frames)/elapsed,(s->bytes-s->previous_bytes)*8/elapsed/1e6,
        s->frames,s->bytes,s->invalid,s->received_frames,s->incomplete,s->payload,s->revision,s->control_status,s->control_error,s->requested_width,s->requested_height,s->requested_fps);
    s->previous_at=now;s->previous_frames=s->frames;s->previous_bytes=s->bytes;
    pthread_mutex_unlock(&s->lock);
    if (fclose(state)==0) rename(temp,final);
}
static void publish_formats(uvc_device_handle_t *handle,struct stats *s) {
    char path[320];snprintf(path,sizeof(path),"%s/cam%d-formats.json",s->prefix,s->id);
    FILE *file=fopen(path,"w");if (!file) return;
    fprintf(file,"{\"resolutions\":[");int count=0;
    for (const uvc_format_desc_t *format=uvc_get_format_descs(handle);format;format=format->next) {
        if (format->bDescriptorSubtype!=UVC_VS_FORMAT_MJPEG) continue;
        for (const uvc_frame_desc_t *frame=format->frame_descs;frame;frame=frame->next) {
            const int rates[]={30,25,20};
            for (unsigned r=0;r<sizeof(rates)/sizeof(rates[0]);r++) {
            int supported=0;uint32_t interval=10000000/rates[r];
            if (frame->intervals) {
                for (int j=0;frame->intervals[j];j++) if (frame->intervals[j]==interval) supported=1;
            } else if (interval>=frame->dwMinFrameInterval && interval<=frame->dwMaxFrameInterval &&
                       (!frame->dwFrameIntervalStep || (interval-frame->dwMinFrameInterval)%frame->dwFrameIntervalStep==0)) supported=1;
            if (supported) fprintf(file,"%s{\"width\":%u,\"height\":%u,\"fps\":%d}",count++?",":"",frame->wWidth,frame->wHeight,rates[r]);
            }
        }
    }
    fprintf(file,"]}\n");fclose(file);
}
static void frame_cb(uvc_frame_t *frame, void *user) {
    struct stats *s = user;
    unsigned char *p = frame->data;
    pthread_mutex_lock(&s->lock);
    s->received_frames++;
    if (frame->data_bytes<4 || p[0]!=0xff || p[1]!=0xd8) {
        s->invalid++;pthread_mutex_unlock(&s->lock);return;
    }
    size_t complete_bytes=jpeg_complete_size(p,frame->data_bytes);
    if (!complete_bytes) {
        s->incomplete++;pthread_mutex_unlock(&s->lock);return;
    }
    s->frames++;
    s->bytes += complete_bytes;
    if (frame->width && frame->height) {s->width=frame->width;s->height=frame->height;}
    s->last_frame_at=monotonic_seconds();
    if (s->publish) {
        char temp[320], final[320];
        snprintf(temp,sizeof(temp),"%s/cam%d.tmp",s->prefix,s->id);
        snprintf(final,sizeof(final),"%s/cam%d.jpg",s->prefix,s->id);
        FILE *image=fopen(temp,"wb");
        if (image) {
            size_t written=fwrite(p,1,complete_bytes,image);
            fclose(image);
            if (written==complete_bytes) rename(temp,final);
        }
    }
    if (s->saved < 3 && frame->data_bytes > 4) {
        char name[320];
        snprintf(name, sizeof(name), "%s/cam%d-%u.jpg", s->prefix, s->id, s->saved);
        FILE *out = fopen(name, "wb");
        if (out) { fwrite(frame->data, 1, complete_bytes, out); fclose(out); }
        s->saved++;
    }
    pthread_mutex_unlock(&s->lock);
}
static int begin_camera(uvc_device_handle_t *handle,struct stats *s,int width,int height,int fps) {
    uvc_stream_ctrl_t ctrl;
    int error=uvc_get_stream_ctrl_format_size(handle,&ctrl,UVC_FRAME_FORMAT_MJPEG,width,height,fps);
    if (error<0) return error;
    if (ctrl.dwFrameInterval!=(uint32_t)(10000000/fps)) return UVC_ERROR_INVALID_MODE;
    ctrl.dwMaxPayloadTransferSize=s->payload;
    return uvc_start_streaming(handle,&ctrl,frame_cb,s,0);
}
static int wait_for_frame(struct stats *s,unsigned long before,int width,int height) {
    double deadline=monotonic_seconds()+3;
    while (!stopping && monotonic_seconds()<deadline) {
        pthread_mutex_lock(&s->lock);
        int ready=s->frames>before && s->width==width && s->height==height;
        pthread_mutex_unlock(&s->lock);
        if (ready) return 1;
        const struct timespec delay={0,50000000};nanosleep(&delay,NULL);
    }
    return 0;
}
static unsigned long frame_count(struct stats *s) {
    pthread_mutex_lock(&s->lock);unsigned long count=s->frames;pthread_mutex_unlock(&s->lock);return count;
}
static void apply_control(uvc_device_handle_t *handle,struct stats *s,int *started) {
    char path[320];snprintf(path,sizeof(path),"%s/cam%d-control.txt",s->prefix,s->id);
    FILE *file=fopen(path,"r");if (!file) return;
    unsigned long long revision=0;int width=0,height=0,fps=s->fps;
    int read=fscanf(file,"%llu %d %d %d",&revision,&width,&height,&fps);fclose(file);
    if ((read!=3 && read!=4) || (fps!=20 && fps!=25 && fps!=30) || !revision || revision==s->revision || width<16 || height<16 || width>8192 || height>8192) return;
    pthread_mutex_lock(&s->lock);
    int old_width=s->width,old_height=s->height,old_fps=s->fps;
    s->revision=revision;s->requested_width=width;s->requested_height=height;s->requested_fps=fps;s->control_error=0;
    s->control_status="applying";
    pthread_mutex_unlock(&s->lock);
    if (width==old_width && height==old_height && fps==old_fps && *started) {s->control_status="applied";return;}
    publish_state(s);
    if (*started) uvc_stop_streaming(handle);
    *started=0;
    pthread_mutex_lock(&s->lock);s->fps=fps;pthread_mutex_unlock(&s->lock);
    snprintf(path,sizeof(path),"%s/cam%d.jpg",s->prefix,s->id);unlink(path);
    unsigned long before=frame_count(s);
    int error=begin_camera(handle,s,width,height,fps);
    if (error>=0) {
        *started=1;
        if (wait_for_frame(s,before,width,height)) {
            s->control_status="applied";
            printf("CAMERA %d mode applied: %dx%d at %dfps revision=%llu\n",s->id,width,height,fps,revision);return;
        }
        error=-99;
        uvc_stop_streaming(handle);*started=0;
    }
    s->control_error=error;
    pthread_mutex_lock(&s->lock);s->fps=old_fps;pthread_mutex_unlock(&s->lock);
    before=frame_count(s);
    if (begin_camera(handle,s,old_width,old_height,old_fps)>=0) {
        *started=1;wait_for_frame(s,before,old_width,old_height);
    }
    s->control_status="error";
    fprintf(stderr,"CAMERA %d mode change failed (%d); attempted restore %dx%d at %dfps\n",s->id,error,old_width,old_height,old_fps);
}
int main(int argc, char **argv) {
    if (argc != 8 && argc != 9) {
        fprintf(stderr, "Usage: %s width height fps payload_limit seconds cameras output_dir [publish]\n", argv[0]);
        return 2;
    }
    int width=atoi(argv[1]), height=atoi(argv[2]), fps=atoi(argv[3]);
    int cap=atoi(argv[4]), seconds=atoi(argv[5]), wanted=atoi(argv[6]);
    if (wanted < 1 || wanted > MAX_CAMERAS || width<1 || height<1 || fps<1 || cap < 128 || cap > 3072 || seconds < 0 || seconds > 60) return 2;
    int payloads[MAX_CAMERAS];for (int i=0;i<MAX_CAMERAS;i++) payloads[i]=cap;
    const char *overrides=getenv("G30_UVC_PAYLOADS");
    if (overrides && *overrides) {
        char *copy=strdup(overrides),*save=NULL;int count=0,valid=1;
        if (!copy) return 1;
        for (char *part=strtok_r(copy,",",&save);part;part=strtok_r(NULL,",",&save)) {
            char *end=NULL;long value=strtol(part,&end,10);
            if (count>=wanted || end==part || *end || value<128 || value>3072) {valid=0;break;}
            payloads[count++]=(int)value;
        }
        free(copy);
        if (!valid || count!=wanted) {fprintf(stderr,"Invalid G30_UVC_PAYLOADS: one 128..3072 value per camera required\n");return 2;}
    }
    setvbuf(stdout, NULL, _IOLBF, 0);
    signal(SIGINT, on_signal); signal(SIGTERM, on_signal);
    mkdir(argv[7], 0700);
    uvc_context_t *ctx=NULL;
    uvc_device_t **list=NULL;
    uvc_device_handle_t *handles[MAX_CAMERAS]={0};
    int started[MAX_CAMERAS]={0}, opened=0, result=1;
    struct stats stats[MAX_CAMERAS]={0};
    struct candidate cameras[MAX_CAMERAS]={0};
    int found=0;
    for (int i=0; i<MAX_CAMERAS; i++) {
        pthread_mutex_init(&stats[i].lock, NULL);
        stats[i].id=i;
        stats[i].publish=argc==9 && strcmp(argv[8],"publish")==0;
        stats[i].width=stats[i].requested_width=width;stats[i].height=stats[i].requested_height=height;
        stats[i].fps=stats[i].requested_fps=fps;stats[i].payload=payloads[i];stats[i].control_status="idle";stats[i].previous_at=monotonic_seconds();
        snprintf(stats[i].prefix, sizeof(stats[i].prefix), "%s", argv[7]);
    }
    uvc_error_t err=uvc_init(&ctx,NULL);
    if (err<0) { uvc_perror(err,"uvc_init"); goto done; }
    err=uvc_get_device_list(ctx,&list);
    if (err<0) { uvc_perror(err,"uvc_get_device_list"); goto done; }
    for (int i=0; list[i] && found<MAX_CAMERAS; i++) {
        uvc_device_descriptor_t *desc=NULL;
        if (uvc_get_device_descriptor(list[i], &desc)<0) continue;
        int match=desc->idVendor==0x0bda && desc->idProduct==0x2292;
        uvc_free_device_descriptor(desc);
        if (!match) continue;
        cameras[found].device=list[i];
        cameras[found].bus=uvc_get_bus_number(list[i]);
        cameras[found].address=uvc_get_device_address(list[i]);
        found++;
    }
    if (found<wanted) { fprintf(stderr,"Only %d of %d requested cameras found\n",found,wanted); goto done; }
    /* A separate enumeration context is used only to read USB port paths. */
    libusb_context *usb_context=NULL;
    libusb_device **usb_devices=NULL;
    if (libusb_init(&usb_context)==0) {
        ssize_t count=libusb_get_device_list(usb_context,&usb_devices);
        for (ssize_t j=0;j<count;j++) for (int i=0;i<found;i++) {
            if (libusb_get_bus_number(usb_devices[j])==cameras[i].bus && libusb_get_device_address(usb_devices[j])==cameras[i].address) {
                int ports=libusb_get_port_numbers(usb_devices[j],cameras[i].ports,sizeof(cameras[i].ports));
                if (ports>0) cameras[i].port_count=ports;
            }
        }
        if (usb_devices) libusb_free_device_list(usb_devices,1);
        libusb_exit(usb_context);
    }
    qsort(cameras,found,sizeof(cameras[0]),compare_camera);
    for (int i=0;i<wanted;i++) {
        printf("CAMERA %d USB bus=%u address=%u\n",opened,cameras[i].bus,cameras[i].address);
        err=uvc_open(cameras[i].device,&handles[opened]);
        if (err<0) { uvc_perror(err,"uvc_open"); goto done; }
        opened++;
    }
    if (opened != wanted) { fprintf(stderr,"Only %d of %d requested cameras found\n",opened,wanted); goto done; }
    for (int i=0;i<opened;i++) {
        publish_formats(handles[i],&stats[i]);
        uvc_stream_ctrl_t ctrl;
        err=uvc_get_stream_ctrl_format_size(handles[i],&ctrl,UVC_FRAME_FORMAT_MJPEG,width,height,fps);
        if (err<0) { uvc_perror(err,"negotiate"); goto done; }
        printf("CAMERA %d negotiated payload=%u frame_size=%u interval=%u; configured payload=%d\n",i,ctrl.dwMaxPayloadTransferSize,ctrl.dwMaxVideoFrameSize,ctrl.dwFrameInterval,stats[i].payload);
        ctrl.dwMaxPayloadTransferSize=stats[i].payload;
        err=uvc_start_streaming(handles[i],&ctrl,frame_cb,&stats[i],0);
        if (err<0) { uvc_perror(err,"start_streaming"); goto done; }
        started[i]=1;
    }
    for (int i=0;i<opened;i++) {
        pthread_mutex_lock(&stats[i].lock);
        stats[i].frames=stats[i].bytes=stats[i].invalid=stats[i].received_frames=stats[i].incomplete=0;
        stats[i].previous_at=monotonic_seconds();
        pthread_mutex_unlock(&stats[i].lock);
    }
    for (int second=1;(seconds==0 || second<=seconds) && !stopping;second++) {
        sleep(1);
        for (int i=0;i<opened;i++) {
            if (stats[i].publish) apply_control(handles[i],&stats[i],&started[i]);
            pthread_mutex_lock(&stats[i].lock);
            if (!stats[i].publish || second%5==0)
                printf("T=%d CAM=%d frames=%lu fps=%.2f bytes=%lu bad_headers=%lu incomplete=%lu received=%lu\n",second,i,stats[i].frames,(double)stats[i].frames/second,stats[i].bytes,stats[i].invalid,stats[i].incomplete,stats[i].received_frames);
            pthread_mutex_unlock(&stats[i].lock);
            publish_state(&stats[i]);
        }
    }
    result=0;
    for (int i=0;i<opened;i++) if (!stats[i].frames || stats[i].invalid || stats[i].incomplete) result=1;
done:
    for (int i=0;i<opened;i++) if (started[i]) uvc_stop_streaming(handles[i]);
    for (int i=0;i<opened;i++) if (handles[i]) uvc_close(handles[i]);
    if (list) uvc_free_device_list(list,1);
    if (ctx) uvc_exit(ctx);
    for (int i=0;i<MAX_CAMERAS;i++) pthread_mutex_destroy(&stats[i].lock);
    return result;
}
