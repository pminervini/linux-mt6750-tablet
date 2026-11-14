// SPDX-License-Identifier: GPL-2.0
/* Offline boundary fixture: actual matching-core ioctl/query bodies and driver
 * format/control bodies, real nonrecursive host locks; no devices or DMA. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <linux/videodev2.h>
#include <linux/v4l2-subdev.h>
#include <linux/media-bus-format.h>
typedef uint32_t u32;
struct mutex { pthread_mutex_t native; unsigned bit; };
static _Thread_local unsigned held;
static void mutex_init_bit(struct mutex *m, unsigned bit) {
 pthread_mutexattr_t a; assert(!pthread_mutexattr_init(&a));
 assert(!pthread_mutexattr_settype(&a,PTHREAD_MUTEX_ERRORCHECK));
 assert(!pthread_mutex_init(&m->native,&a)); assert(!pthread_mutexattr_destroy(&a)); m->bit=bit;
}
static void mutex_lock(struct mutex *m) { assert(!(held & m->bit)); assert(m->bit!=1 || !(held&2)); assert(!pthread_mutex_lock(&m->native)); held|=m->bit; }
static int mutex_lock_interruptible(struct mutex *m) { mutex_lock(m); return 0; }
static void mutex_unlock(struct mutex *m) { assert(held&m->bit); held&=~m->bit; assert(!pthread_mutex_unlock(&m->native)); }
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define min_t(t,a,b) ((t)(a)<(t)(b)?(t)(a):(t)(b))
#define atomic_set(p,v) atomic_store(p,v)
#define strscpy(d,s,n) snprintf(d,n,"%s",s)
struct list_head { struct list_head *next,*prev; };
#define list_entry(p,t,m) container_of(p,t,m)
#define list_for_each_entry(p,h,m) for(p=list_entry((h)->next,__typeof__(*p),m); &p->m!=(h); p=list_entry(p->m.next,__typeof__(*p),m))
#define list_for_each_entry_continue(p,h,m) for(p=list_entry(p->m.next,__typeof__(*p),m); &p->m!=(h); p=list_entry(p->m.next,__typeof__(*p),m))
static bool list_empty(struct list_head *h) { return h->next==h; }
struct v4l2_ctrl_handler;
struct v4l2_ctrl { struct v4l2_ctrl_handler *handler; u32 id,type,flags,elem_size,elems,nr_of_dims,dims[4]; const char *name; bool is_array; int val; int64_t minimum,maximum,default_value; uint64_t step; };
struct v4l2_ctrl_ref { struct list_head node; struct v4l2_ctrl *ctrl; };
struct v4l2_ctrl_handler { struct mutex _lock,*lock; struct list_head ctrl_refs; };
static void v4l2_ctrl_handler_init(struct v4l2_ctrl_handler *h,int n) { (void)n; mutex_init_bit(&h->_lock,2); h->lock=&h->_lock; h->ctrl_refs.next=h->ctrl_refs.prev=&h->ctrl_refs; }
static struct v4l2_ctrl_ref *find_ref(struct v4l2_ctrl_handler *h,u32 id) { struct v4l2_ctrl_ref *p; list_for_each_entry(p,&h->ctrl_refs,node) if(p->ctrl->id==id) return p; return NULL; }
static u32 node2id(struct list_head *p) { return list_entry(p,struct v4l2_ctrl_ref,node)->ctrl->id; }
static u32 user_flags(struct v4l2_ctrl *c) { return c->flags; }
struct camera;
struct vb2_queue { struct mutex *lock; bool busy; };
struct v4l2_subdev { struct camera *c; };
struct v4l2_subdev_state { int unused; };
struct m2m { struct mutex *q_lock; };
struct v4l2_fh { struct m2m *m2m_ctx; struct v4l2_ctrl_handler *ctrl_handler; int prio; };
struct media_device { struct mutex req_queue_mutex; };
struct v4l2_device { struct media_device *mdev; };
struct file;
struct v4l2_ioctl_ops { long (*vidioc_default)(struct file *,void *,bool,unsigned int,void *); };
struct video_device { struct mutex *lock; struct vb2_queue *queue; struct v4l2_device *v4l2_dev; const struct v4l2_ioctl_ops *ioctl_ops; int dev_debug,prio; };
struct camera { struct mutex lock; struct v4l2_ctrl_handler controls; struct v4l2_ctrl *exposure; struct v4l2_ctrl_ref reference; struct v4l2_ctrl storage; unsigned index,mode; struct vb2_queue queue; struct video_device video; struct v4l2_fh fh; _Atomic int exposure_lines,gain_units,test_pattern; };
struct file { struct camera *c; };
struct board_mode { unsigned width,height,vts; };
static const struct board_mode board_modes[]={{1632,1224,1246},{3264,2448,2482},{1600,1200,1240}};
static struct camera *video_drvdata(struct file *f) { return f->c; }
static struct camera *v4l2_get_subdevdata(struct v4l2_subdev *s) { return s->c; }
static bool vb2_is_busy(struct vb2_queue *q) { assert(held&1); return q->busy; }
static int modify_error;
static int control_set(struct v4l2_ctrl *ctrl);
static int __v4l2_ctrl_modify_range(struct v4l2_ctrl *c,int64_t lo,int64_t hi,uint64_t step,int64_t def) { assert(held&2); if(modify_error) return modify_error; c->minimum=lo; c->maximum=hi; c->step=step; c->default_value=def; return 0; }
static int __v4l2_ctrl_s_ctrl(struct v4l2_ctrl *c,int val) { assert(held&2); c->val=val; return control_set(c); }
static struct video_device *video_devdata(struct file *f) { return &f->c->video; }
static struct v4l2_fh *file_to_v4l2_fh(struct file *f) { return &f->c->fh; }
static bool video_is_registered(struct video_device *d) { (void)d; return true; }
static bool v4l2_device_supports_requests(struct v4l2_device *d) { (void)d; return false; }
static int v4l2_prio_check(int a,int b) { (void)a;(void)b; return 0; }
#define pr_warn(...) ((void)0)
#define pr_cont(...) ((void)0)
#define video_device_node_name(v) "offline"
#define v4l_printk_ioctl(...) ((void)0)
#define ERESTARTSYS 512
#define INFO_FL_QUEUE 1
#define INFO_FL_CTRL 2
#define INFO_FL_PRIO 4
#define V4L2_DEV_DEBUG_IOCTL 1
#define V4L2_DEV_DEBUG_IOCTL_ARG 2
#define V4L2_DEV_DEBUG_STREAMING 4
#define V4L2_IOCTLS 256
struct v4l2_ioctl_info { unsigned ioctl,flags; long (*func)(const struct v4l2_ioctl_ops *,struct file *,void *); void (*debug)(const void *,bool); };
static struct v4l2_ioctl_info v4l2_ioctls[V4L2_IOCTLS];
static bool v4l2_is_known_ioctl(unsigned cmd) { return v4l2_ioctls[_IOC_NR(cmd)].ioctl==cmd; }
static bool is_valid_ioctl(struct video_device *d,unsigned cmd) { (void)d;(void)cmd; return true; }
static void v4l_print_default(const void *a,bool b) { (void)a;(void)b; }
static struct camera *format_camera;
static unsigned format_mode;
static void *format_memset(void *dest, int value, size_t size) {
 if (format_camera) assert(format_camera->mode == format_mode);
 return memset(dest, value, size);
}
#define memset format_memset
#include "extracted-locking.h"
#undef memset
static long try_format(const struct v4l2_ioctl_ops *o,struct file *f,void *a) { (void)o; return try_fmt(f,NULL,a); }
static long query(const struct v4l2_ioctl_ops *o,struct file *f,void *a) { (void)o; return v4l2_query_ext_ctrl(&f->c->controls,a); }
static long format(const struct v4l2_ioctl_ops *o,struct file *f,void *a) { (void)o; return set_fmt(f,NULL,a); }
static long set_control(const struct v4l2_ioctl_ops *o,struct file *f,void *a) { (void)o; struct v4l2_control *v=a; mutex_lock(f->c->controls.lock); int ret=__v4l2_ctrl_s_ctrl(f->c->exposure,v->value); mutex_unlock(f->c->controls.lock); return ret; }
static long queue(const struct v4l2_ioctl_ops *o,struct file *f,void *a) { (void)o;(void)f;(void)a; assert(held==1); return 0; }
static void init_camera(struct camera *c,unsigned index) {
 memset(c,0,sizeof(*c)); mutex_init_bit(&c->lock,1); c->index=index;
 init_control_lock(c);
 c->exposure=&c->storage; c->storage=(struct v4l2_ctrl){.handler=&c->controls,.id=V4L2_CID_EXPOSURE,.type=V4L2_CTRL_TYPE_INTEGER,.name="Exposure",.val=623,.minimum=6,.maximum=1238,.default_value=623,.step=1};
 c->reference.ctrl=c->exposure; c->reference.node.next=c->reference.node.prev=&c->controls.ctrl_refs;
 c->controls.ctrl_refs.next=c->controls.ctrl_refs.prev=&c->reference.node;
 c->queue.lock=&c->lock; c->video.queue=&c->queue; c->video.lock=&c->lock;
 static const struct v4l2_ioctl_ops ops={0}; c->video.ioctl_ops=&ops; c->fh.ctrl_handler=&c->controls;
}
static void destroy_camera(struct camera *c) { assert(!pthread_mutex_destroy(&c->lock.native)); assert(!pthread_mutex_destroy(&c->controls._lock.native)); }
static void *concurrent(void *arg) {
 struct camera *c=arg; struct file f={.c=c}; struct v4l2_subdev sd={.c=c};
 for(unsigned i=0;i<400;i++) {
  struct v4l2_query_ext_ctrl q={.id=V4L2_CID_EXPOSURE}; assert(!__video_do_ioctl(&f,VIDIOC_QUERY_EXT_CTRL,&q)); assert(q.id==V4L2_CID_EXPOSURE);
  struct v4l2_format fmt={.fmt.pix={.width=i%2?3264:1632,.height=i%2?2448:1224}}; assert(!__video_do_ioctl(&f,VIDIOC_S_FMT,&fmt));
  struct v4l2_control value={.id=V4L2_CID_EXPOSURE,.value=32}; assert(!__video_do_ioctl(&f,VIDIOC_S_CTRL,&value));
  assert(!__video_do_ioctl(&f,VIDIOC_QBUF,NULL)); struct v4l2_subdev_format pad={0}; assert(!pad_fmt(&sd,NULL,&pad));
 }
 assert(!held); return NULL;
}
int main(void) {
 v4l2_ioctls[_IOC_NR(VIDIOC_QUERY_EXT_CTRL)]=(struct v4l2_ioctl_info){.ioctl=VIDIOC_QUERY_EXT_CTRL,.flags=INFO_FL_CTRL,.func=query};
 v4l2_ioctls[_IOC_NR(VIDIOC_TRY_FMT)]=(struct v4l2_ioctl_info){.ioctl=VIDIOC_TRY_FMT,.func=try_format};
 v4l2_ioctls[_IOC_NR(VIDIOC_S_FMT)]=(struct v4l2_ioctl_info){.ioctl=VIDIOC_S_FMT,.func=format};
 v4l2_ioctls[_IOC_NR(VIDIOC_S_CTRL)]=(struct v4l2_ioctl_info){.ioctl=VIDIOC_S_CTRL,.flags=INFO_FL_CTRL,.func=set_control};
 v4l2_ioctls[_IOC_NR(VIDIOC_QBUF)]=(struct v4l2_ioctl_info){.ioctl=VIDIOC_QBUF,.flags=INFO_FL_QUEUE,.func=queue};
 for(unsigned index=0;index<2;index++) {
  struct camera c; init_camera(&c,index); struct file f={.c=&c};
  struct v4l2_ctrl pattern={.handler=&c.controls,.id=V4L2_CID_TEST_PATTERN};
  for(unsigned value=0;value<2;value++) {
   pattern.val=value; mutex_lock(c.controls.lock); assert(!control_set(&pattern)); mutex_unlock(c.controls.lock);
   assert(atomic_load(&c.test_pattern)==(int)value);
  }
  struct v4l2_query_ext_ctrl q={.id=V4L2_CTRL_FLAG_NEXT_CTRL}; assert(!__video_do_ioctl(&f,VIDIOC_QUERY_EXT_CTRL,&q)); assert(q.id==V4L2_CID_EXPOSURE);
  struct v4l2_format fmt={.fmt.pix={.width=3264,.height=2448}}; assert(!__video_do_ioctl(&f,VIDIOC_S_FMT,&fmt)); assert(fmt.fmt.pix.width==(index?1600U:3264U));
  q=(struct v4l2_query_ext_ctrl){.id=V4L2_CID_EXPOSURE}; assert(!__video_do_ioctl(&f,VIDIOC_QUERY_EXT_CTRL,&q)); assert(q.maximum==(index?1232:2474));
  c.queue.busy=true; assert(__video_do_ioctl(&f,VIDIOC_S_FMT,&fmt)==-EBUSY);
  for(unsigned mode=0;mode<(index?1U:2U);mode++) {
   c.mode=mode;
   for(unsigned requested=0;requested<2;requested++) {
    fmt=(struct v4l2_format){.fmt.pix={.width=requested?3264:1632,.height=requested?2448:1224}};
    format_camera=&c; format_mode=mode;
    assert(!__video_do_ioctl(&f,VIDIOC_TRY_FMT,&fmt));
    format_camera=NULL;
    assert(c.mode==mode && fmt.fmt.pix.width==(index?1600U:requested?3264U:1632U));
   }
  }
  c.queue.busy=false;
  modify_error=-EIO; assert(__video_do_ioctl(&f,VIDIOC_S_FMT,&fmt)==-EIO); modify_error=0; assert(!held);
  pthread_t threads[3]; for(unsigned i=0;i<3;i++) assert(!pthread_create(&threads[i],NULL,concurrent,&c)); for(unsigned i=0;i<3;i++) assert(!pthread_join(threads[i],NULL));
  destroy_camera(&c);
 }
 puts("PASS matching-core ioctl/query locks: both sensors, NEXT_CTRL/metadata, S_FMT range locking and error unlock, busy queue, concurrent controls/format/queue/pad callers; offline only"); return 0;
}
