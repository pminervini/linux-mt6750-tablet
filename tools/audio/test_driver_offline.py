#!/usr/bin/env python3
"""Compile actual source-extracted callbacks with memory-only MMIO/PMIC stubs.

This is not a load/runtime/hardware test. No kernel or device files are opened.
Host pthread locks exercise state transactions, not Linux IRQ/DMA coherency.
Pass the matching kernel source as the second argument for real ALSA intervals.
"""
from pathlib import Path
import re
import subprocess
import sys

source = Path(__file__).resolve().parent
output = Path(sys.argv[1]).resolve()
output.mkdir(parents=True, exist_ok=True)
text = (source / 'mt6755-tablet.c').read_text()


def function(name, contents=text):
    start = re.search(r'^(?:static )?[^;\n]*\b' + name + r'\(', contents, re.M).start()
    brace = contents.index('{', start)
    level, end = 1, brace + 1
    while level:
        level += (contents[end] == '{') - (contents[end] == '}')
        end += 1
    return contents[start:end]


def declaration(start, end='\n};'):
    at = text.index(start)
    return text[at:text.index(end, at) + len(end)]


shim = r'''
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
#include <limits.h>
#include <pthread.h>
#include "mt6755-audio-core.h"
#define __iomem
#define BIT(x) (1U << (x))
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define mutex_lock(x) ((void)(x))
#define mutex_unlock(x) ((void)(x))
#define spin_lock_irqsave(x,f) ((f)=0,host_lock(x))
#define spin_unlock_irqrestore(x,f) ((void)(f),host_unlock(x))
#define dma_wmb() ((void)0)
#define dma_rmb() ((void)0)
#define GFP_KERNEL 0
#define kmalloc(n,flags) malloc(n)
#define kfree(p) free(p)
#define HRTIMER_MODE_REL 0
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define SNDRV_PCM_POS_XRUN ((unsigned long)-1)
#define bytes_to_frames(r,n) ((n)/4)
typedef unsigned long snd_pcm_uframes_t;
enum hrtimer_restart { HRTIMER_NORESTART, HRTIMER_RESTART };
#define SNDRV_PCM_TRIGGER_START 1
#define SNDRV_PCM_TRIGGER_STOP 0
#define SNDRV_PCM_TRIGGER_SUSPEND 4
#define SNDRV_PCM_FORMAT_S16_LE 2
#define dev_err(...) ((void)0)
#define usleep_range(...) ((void)0)
#define msleep(...) ((void)0)
#define udelay(...) ((void)0)
#define lower_32_bits(x) ((u32)(x))
#define frames_to_bytes(r,n) ((n)*4)
typedef unsigned long dma_addr_t;
typedef pthread_mutex_t spinlock_t;
static _Thread_local int lock_depth, starter;
static void host_lock(spinlock_t *lock);
static void host_unlock(spinlock_t *lock) { assert(lock_depth>0);lock_depth--;assert(!pthread_mutex_unlock(lock)); }
struct mutex { int unused; };
struct device { void *driver_data; };
struct device_attribute { int unused; };
#define sysfs_emit(buf, ...) snprintf(buf, 4096, __VA_ARGS__)
struct gpio_desc { int unused; };
struct gpio_device { int unused; };
struct snd_soc_card { struct device *dev; void *drvdata; };
struct hrtimer { bool active; };
struct snd_interval { unsigned min,max,openmin:1,openmax:1,integer:1,empty:1; };
enum { SNDRV_PCM_HW_PARAM_BUFFER_BYTES, SNDRV_PCM_HW_PARAM_PERIOD_BYTES, SNDRV_PCM_HW_PARAM_PERIODS };
typedef int snd_pcm_hw_param_t;
struct snd_pcm_hw_constraints { struct snd_interval intervals[3]; unsigned steps[3]; };
#define constrs_interval(c,v) (&(c)->intervals[v])
#define snd_pcm_runtime runtime
#define snd_BUG_ON(x) (x)
#define SNDRV_PCM_INFO_INTERLEAVED 1
#define SNDRV_PCM_INFO_BLOCK_TRANSFER 2
#define SNDRV_PCM_FMTBIT_S16_LE 1
#define SNDRV_PCM_RATE_48000 1
struct snd_pcm_hardware { unsigned info,formats,rates,rate_min,rate_max,channels_min,channels_max,
    buffer_bytes_max,period_bytes_min,period_bytes_max,periods_min,periods_max; };
struct runtime { void *dma_area; dma_addr_t dma_addr; size_t dma_bytes, period_size;
    struct snd_pcm_hardware hw; struct snd_pcm_hw_constraints hw_constraints; };
struct snd_pcm_substream { int stream; struct runtime *runtime; };
struct snd_soc_component { struct device *dev; struct snd_soc_card *card; };
struct snd_kcontrol { struct snd_soc_component *component; };
struct snd_ctl_elem_value { struct { struct { long value[2]; } integer; } value; };
struct iov_iter { char *buffer; size_t size, at; };
struct snd_pcm_hw_params { size_t bytes, period; unsigned rate, channels, format; };
#define params_buffer_bytes(p) ((p)->bytes)
#define params_period_bytes(p) ((p)->period)
#define params_rate(p) ((p)->rate)
#define params_channels(p) ((p)->channels)
#define params_format(p) ((p)->format)

static struct snd_soc_component *snd_kcontrol_chip(struct snd_kcontrol *k) { return k->component; }
static void writel(u32 v, void *p) { memcpy(p, &v, 4); }
static u32 readl(const void *p) { u32 v; memcpy(&v,p,4); return v; }
static void hrtimer_cancel(struct hrtimer *t) { assert(!lock_depth);t->active=false; }
static void hrtimer_start(struct hrtimer *t, u64 ns, int mode) { assert(lock_depth==1);(void)ns;(void)mode;t->active=true; }
static u64 ns_to_ktime(u64 ns) { return ns; }
static u64 test_clock=1000000;
static u64 ktime_get_ns(void) { return test_clock; }
static void hrtimer_forward_now(struct hrtimer *t, u64 ns) { (void)t;(void)ns; }
static void *dma_alloc_coherent(struct device *d, size_t n, dma_addr_t *addr, int flags)
{ (void)d;(void)flags;*addr=0x40000000;return calloc(1,n); }
static void dma_free_coherent(struct device *d, size_t n, void *p, dma_addr_t addr)
{ (void)d;(void)n;(void)addr;free(p); }
static size_t copy_from_iter(void *to, size_t n, struct iov_iter *it)
{ if(n>it->size-it->at)n=it->size-it->at;memcpy(to,it->buffer+it->at,n);it->at+=n;return n; }
static size_t copy_to_iter(const void *from, size_t n, struct iov_iter *it)
{ if(n>it->size-it->at)n=it->size-it->at;memcpy(it->buffer+it->at,from,n);it->at+=n;return n; }
static u32 pmic[0x10000];
static int pmic_calls, fail_call, fail_forever;
static int read_fail;
static int mt6755_pmic_read(u16 reg, u16 *value)
{ if(read_fail==reg)return -EIO;*value=pmic[reg];return 0; }
static int mt6755_pmic_write(u16 reg, u16 val)
{
    pmic_calls++;
    if(pmic_calls==fail_call || (fail_forever && pmic_calls>=fail_forever))return -EIO;
    pmic[reg]=val;return 0;
}
static int mt6755_pmic_rmw(u16 reg, u16 mask, int shift, u16 val)
{ return mt6755_pmic_write(reg,(pmic[reg]&~(mask<<shift))|((val&mask)<<shift)); }
'''
structs = text[text.index('struct mt_audio;'):text.index('static const unsigned int mt_pins')]
structs += declaration('static const unsigned int mt_pins', ';')
structs += '\nstatic int mt_domain(struct mt_audio *a, bool on) { (void)a; (void)on; return 0; }\n'
structs += '''
static bool allow_playback=true, allow_capture=true;
static void mt_stop(struct mt_stream *s);
static int period_callbacks, xrun_callbacks;
static struct mt_audio *test_audio;
static void snd_pcm_period_elapsed(struct snd_pcm_substream *s) { (void)s;period_callbacks++; }
static void snd_pcm_stop_xrun(struct snd_pcm_substream *s)
{ xrun_callbacks++;mt_stop(&test_audio->stream[s->stream]); }
'''
kernel = Path(sys.argv[2])
pcm_lib = (kernel / 'sound/core/pcm_lib.c').read_text()
pcm_params = (kernel / 'include/sound/pcm_params.h').read_text()
intervals = [function(n, pcm_params) for n in ['snd_interval_any', 'snd_interval_none',
    'snd_interval_checkempty', 'snd_interval_empty', 'snd_interval_setinteger']]
intervals += [function(n, pcm_lib) for n in ['div32', 'snd_interval_refine', 'snd_interval_step',
    'snd_interval_div', 'snd_pcm_hw_constraint_minmax', 'snd_pcm_hw_constraint_integer']]
# Rule registration is a memory shim; refinement/division are exact kernel code.
intervals.append('''
static int snd_pcm_hw_constraint_step(struct runtime *r, unsigned cond, int var, unsigned long step)
{ (void)cond;r->hw_constraints.steps[var]=step;return 0; }
''')
drvdata_apis = []
for header, names in [
    ('include/linux/device.h', ['dev_get_drvdata', 'dev_set_drvdata']),
    ('include/sound/soc-component.h', ['snd_soc_component_get_drvdata']),
    ('include/sound/soc-card.h', ['snd_soc_card_get_drvdata', 'snd_soc_card_set_drvdata']),
]:
    contents = (kernel / header).read_text()
    drvdata_apis.extend(function(n, contents) for n in names)
assert 'dev_set_drvdata(card->dev, card);' in (kernel / 'sound/soc/soc-core.c').read_text()
assert 'snd_soc_card_set_drvdata(&a->card, a);' in function('mt_probe')
parts = [shim, *drvdata_apis, structs, *intervals, r'''
static pthread_mutex_t schedule_lock=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t schedule_cond=PTHREAD_COND_INITIALIZER;
static int pause_clear, at_clear, release_clear, start_attempt, start_done;
static void host_lock(spinlock_t *lock)
{
    if(starter && lock==&test_audio->state_lock) {
        int ret=pthread_mutex_trylock(lock);
        if(!ret) { lock_depth++;return; }
        assert(ret==EBUSY);
        assert(!pthread_mutex_lock(&schedule_lock));start_attempt=1;
        assert(!pthread_cond_broadcast(&schedule_cond));assert(!pthread_mutex_unlock(&schedule_lock));
    }
    assert(!pthread_mutex_lock(lock));lock_depth++;
}
static void before_update(struct mt_audio *a, u32 reg, u32 mask, u32 value)
{
    (void)a;
    if(pause_clear && !starter && reg==0x010 && mask==1 && !value) {
        assert(!pthread_mutex_lock(&schedule_lock));at_clear=1;
        assert(!pthread_cond_broadcast(&schedule_cond));
        while(!release_clear)assert(!pthread_cond_wait(&schedule_cond,&schedule_lock));
        assert(!pthread_mutex_unlock(&schedule_lock));
    }
}
''']
for name in ['mt_update', 'mt_steps']:
    body = function(name)
    if name == 'mt_update':
        # Scheduling hook before reg_lock: preserves every actual callback statement.
        body = body.replace('unsigned long flags;', 'unsigned long flags;\n before_update(a, reg, mask, value);')
    parts.append(body)
parts.append('#define AFE_STEPS(a, seq) mt_steps(a, seq, ARRAY_SIZE(seq))')
for name in ['mt_pin_mode', 'mt_gpio_out', 'mt_amp_off', 'mt_amp_on']:
    parts.append(function(name))
parts.extend([declaration('struct mt_codec_step', '};'),
              declaration('static const struct mt_codec_step codec_up'),
              declaration('static const struct mt_codec_step codec_down')])
parts.append(declaration('static const struct snd_pcm_hardware mt_hw'))
for name in ['mt_codec_write', 'mt_codec_off', 'mt_codec_on', 'mt_get_stream',
             'mt_open', 'mt_stop', 'mt_sync_stop', 'mt_hw_free', 'mt_close', 'mt_hw_params',
             'mt_prepare', 'mt_trigger', 'mt_copy', 'mt_volume_get', 'mt_volume_put',
             'mt_pointer', 'mt_poll', 'analogue_status_show']:
    parts.append(function(name))
parts.append(r'''
static u32 afe[1024], gpio[1024], spm[1024];
static struct runtime runtime[2];
static struct snd_pcm_substream sub[2];
static struct mt_audio audio;
static struct snd_soc_component component;
static struct device component_device;
static void reset(void)
{
    static bool initialized;
    if(initialized) { assert(!pthread_mutex_destroy(&audio.state_lock));assert(!pthread_mutex_destroy(&audio.reg_lock)); }
    memset(&audio,0,sizeof(audio));memset(afe,0,sizeof(afe));memset(gpio,0,sizeof(gpio));
    assert(!pthread_mutex_init(&audio.state_lock,NULL));assert(!pthread_mutex_init(&audio.reg_lock,NULL));initialized=true;
    memset(spm,0,sizeof(spm));memset(pmic,0,sizeof(pmic));memset(runtime,0,sizeof(runtime));
    pmic_calls=fail_call=fail_forever=0;audio.afe=afe;audio.gpio=gpio;audio.spm=spm;
    test_audio=&audio;period_callbacks=xrun_callbacks=0;test_clock=1000000;
    audio.volume=30;audio.card.dev=&component_device;
    component.dev=&component_device;component.card=&audio.card;
    snd_soc_card_set_drvdata(&audio.card,&audio);
    /* Exact snd_soc_register_card device-data replacement; not an a-pointer shim. */
    dev_set_drvdata(component.card->dev,component.card);
    assert(snd_soc_component_get_drvdata(&component)==&audio.card);
    for(int i=0;i<2;i++) { audio.stream[i].audio=&audio;sub[i].stream=i;sub[i].runtime=&runtime[i];
        runtime[i].period_size=480;audio.stream[i].substream=&sub[i]; }
}
static void allocate(int i)
{
    struct snd_pcm_hw_params p={9600,1920,48000,2,SNDRV_PCM_FORMAT_S16_LE};
    assert(mt_hw_params(&component,&sub[i],&p)==0);
}
static void test_fm_lifecycle(void)
{
    reset();allocate(1);afe[0x28/4]=0x03fd0100;
    assert(mt_prepare(&component,&sub[1])==0);
    assert((afe[0x28/4]&0x03fd0000)==0x00410000);assert(afe[0x28/4]&0x100);
    assert(pmic_calls==0);for(unsigned i=0;i<ARRAY_SIZE(gpio);i++)assert(!gpio[i]);
    assert(!(afe[0x108/4]&1));assert(!(afe[0x124/4]&1));
    assert(mt_trigger(&component,&sub[1],SNDRV_PCM_TRIGGER_START)==0);
    assert(afe[0x18/4]&1);assert((afe[0x500/4]&0x41)==0x41);
    assert((afe[0x10/4]&0x41)==0x41);assert(!(afe[0x10/4]&4));
    assert(mt_trigger(&component,&sub[1],SNDRV_PCM_TRIGGER_START)==-EINVAL);
    assert(mt_trigger(&component,&sub[1],2)==-EINVAL);
    audio.stream[0].running=true;afe[0x10/4]|=5;
    assert(mt_trigger(&component,&sub[1],SNDRV_PCM_TRIGGER_STOP)==0);
    assert(!(afe[0x10/4]&0x40));assert((afe[0x10/4]&5)==5);
    assert(!(afe[0x18/4]&1));assert(!(afe[0x500/4]&0x41));
    assert(mt_sync_stop(&component,&sub[1])==0);assert(!audio.stream[1].timer.active);
    assert(!audio.stream[1].prepared);assert(!pmic_calls);
    audio.stream[0].running=false;assert(mt_hw_free(&component,&sub[1])==0);
    assert(!audio.stream[1].area);assert(!runtime[1].dma_area);
    assert(mt_hw_free(&component,&sub[1])==0);
    puts("PASS FM prepare/start/stop/duplex/idempotent free: no codec/GPIO/DAC enable");
}
static void test_card_drvdata(void)
{
    reset();
    struct snd_kcontrol control={&component};struct snd_ctl_elem_value value={0};
    assert(snd_soc_card_get_drvdata(component.card)==&audio);
    assert(mt_volume_get(&control,&value)==0);assert(value.value.integer.value[0]==30);
    value.value.integer.value[0]=35;assert(mt_volume_put(&control,&value)==1);
    value.value.integer.value[0]=0;assert(mt_volume_get(&control,&value)==0);
    assert(value.value.integer.value[0]==35);
    assert(dev_get_drvdata(component.dev)==&audio.card);
    puts("PASS matching kernel drvdata APIs: card registration overwrites device data, all callbacks retain audio context");
}
static void test_codec_errors(void)
{
    int total;
    reset();assert(mt_codec_on(&audio)==0);total=pmic_calls;
    assert(total==79);assert(mt_codec_off(&audio)==0);
    for(int n=1;n<=total;n++) {
        reset();allocate(0);fail_call=n;
        assert(mt_prepare(&component,&sub[0])==-EIO);
        assert(!audio.codec_on);assert(!audio.stream[0].prepared);
        assert(pmic_calls==n+(int)ARRAY_SIZE(codec_down));
        assert(!(gpio[(0x100+0x10+4)/4]&(1U<<22))); /* no amp-on sequence */
        assert(mt_hw_free(&component,&sub[0])==0);
    }
    reset();allocate(0);assert(mt_prepare(&component,&sub[0])==0);
    fail_forever=pmic_calls+1;
    assert(mt_hw_free(&component,&sub[0])==-EIO);
    assert(audio.fault);assert(!audio.stream[0].area);
    assert(pmic_calls==total+(int)ARRAY_SIZE(codec_down));
    assert(mt_prepare(&component,&sub[0])==-EIO);
    puts("PASS all 79 codec startup failure points; all cleanup steps attempted, fault latched");
}
static void test_pcm_copy(void)
{
    char raw[16];s16 values[]={-32768,32767,-100,100,-1,1,300,-300};
    struct snd_kcontrol control={&component};struct snd_ctl_elem_value volume={0};
    struct iov_iter it={raw,sizeof(raw),0};
    reset();allocate(0);allocate(1);
    afe[0x24/4]=0xffff0000;afe[0x28/4]=0x0041fffb;
    assert(mt_prepare(&component,&sub[0])==0);
    assert((afe[0x24/4]&0xffff0000)==(1U<<23));
    assert(afe[0x28/4]==0x00410100); /* AWB preserved, no direct FM -> speaker */
    memcpy(raw,values,sizeof(raw));audio.volume=50;
    assert(mt_copy(&component,&sub[0],0,9592,&it,sizeof(raw))==0);
    for(int i=0;i<8;i++)assert(((s16*)audio.stream[0].area)[((9592+i*2)%9600)/2]==mt_scale(values[i],50));
    memset(audio.stream[0].area,0xff,9600);volume.value.integer.value[0]=0;
    assert(mt_volume_put(&control,&volume)==1);
    for(int i=0;i<9600;i++)assert(!((char*)audio.stream[0].area)[i]);
    assert(mt_volume_get(&control,&volume)==0);assert(volume.value.integer.value[0]==0);
    volume.value.integer.value[0]=101;assert(mt_volume_put(&control,&volume)==-EINVAL);
    it.at=0;assert(mt_copy(&component,&sub[0],0,0,&it,sizeof(raw))==0);
    for(int i=0;i<16;i++)assert(!((char*)audio.stream[0].area)[i]);
    it.at=0;it.size=4;audio.volume=100;
    assert(mt_copy(&component,&sub[0],0,0,&it,16)==-EFAULT);
    for(int i=0;i<16;i++)assert(!((char*)audio.stream[0].area)[i]);
    assert(mt_copy(&component,&sub[0],0,9600,&it,4)==-EINVAL);
    assert(mt_copy(&component,&sub[0],0,0,&it,3)==-EINVAL);
    memcpy(audio.stream[1].area+9592,raw,8);memcpy(audio.stream[1].area,raw+8,8);
    char received[16];it=(struct iov_iter){received,16,0};
    assert(mt_copy(&component,&sub[1],0,9592,&it,16)==0);assert(!memcmp(raw,received,16));
    assert(mt_hw_free(&component,&sub[0])==0);assert(mt_hw_free(&component,&sub[1])==0);
    puts("PASS actual PCM callbacks: signed gain, mute queued+new PCM, wrap, invalid/short copies");
}
static void test_poll_xrun(void)
{
    reset();allocate(1);assert(mt_prepare(&component,&sub[1])==0);
    assert(mt_trigger(&component,&sub[1],SNDRV_PCM_TRIGGER_START)==0);
    afe[0x7c/4]=audio.stream[1].dma;test_clock=2000000;
    assert(mt_poll(&audio.stream[1].timer)==HRTIMER_RESTART);assert(!period_callbacks);
    afe[0x7c/4]+=1920;test_clock=11000000;
    assert(mt_poll(&audio.stream[1].timer)==HRTIMER_RESTART);assert(period_callbacks==1);
    assert(mt_pointer(&component,&sub[1])==480);
    test_clock=70000000; /* 59 ms gap exceeds the negotiated 50 ms ring. */
    assert(mt_poll(&audio.stream[1].timer)==HRTIMER_NORESTART);assert(xrun_callbacks==1);
    assert(mt_pointer(&component,&sub[1])==SNDRV_PCM_POS_XRUN);
    assert(!audio.stream[1].running);assert(!(afe[0x10/4]&0x41));
    audio.users=1;assert(mt_close(&component,&sub[1])==0);
    assert(!audio.stream[1].substream);assert(!audio.stream[1].timer.active);assert(!pmic_calls);
    puts("PASS actual hrtimer/pointer callbacks: no artificial periods; XRUN stops DMA; close releases");
}
static void test_ring(void)
{
    for(int sample=-32768;sample<=32767;sample++)for(unsigned v=0;v<=100;v+=5)
        assert(mt_scale(sample,v)==sample*(int)v/100);
    struct mt_ring r={.bytes=8192,.period=1920};
    assert(mt_advance(&r,1920,10000000)==1);
    assert(mt_advance(&r,5760,30000000)==2);
    assert(mt_advance(&r,0,42000000)==1);assert(r.residue==512);
    assert(mt_advance(&r,4,85000000)==-1);assert(r.xrun);
    r=(struct mt_ring){.bytes=65536,.period=1920};
    for(unsigned n=1;n<100;n++)assert(mt_advance(&r,0,n*1000000ULL)==0);
    assert(mt_advance(&r,0,100000000)==-1);
    r=(struct mt_ring){.bytes=8192,.period=1920};assert(mt_advance(&r,8192,1)==-1);
    r=(struct mt_ring){.bytes=8192,.period=1920};assert(mt_advance(&r,3,1)==-1);
    assert(mt_span(8184,16,8192)==8);
    puts("PASS 1,376,256 signed PCM gain cases; period/wrap, missed-wrap and stalled-clock XRUN");
}
static void init_constraints(void)
{
    struct snd_pcm_hw_constraints *c=&runtime[1].hw_constraints;
    for(int i=0;i<3;i++)snd_interval_any(&c->intervals[i]);
    /* pcm_native.c sets BUFFER_BYTES integer before calling component open. */
    snd_interval_setinteger(&c->intervals[SNDRV_PCM_HW_PARAM_BUFFER_BYTES]);
    audio.stream[1].substream=NULL;
}
static int refine_choice(unsigned bytes, unsigned period)
{
    struct snd_pcm_hw_constraints *c=&runtime[1].hw_constraints;
    struct snd_interval b=c->intervals[SNDRV_PCM_HW_PARAM_BUFFER_BYTES];
    struct snd_interval p={runtime[1].hw.period_bytes_min,runtime[1].hw.period_bytes_max};
    struct snd_interval v={bytes,bytes}, quotient, periods={2,32,0,0,1,0};
    if(snd_interval_refine(&b,&v)<0)return -EINVAL;
    v=(struct snd_interval){period,period};
    if(snd_interval_refine(&p,&v)<0)return -EINVAL;
    if(snd_interval_step(&b,c->steps[SNDRV_PCM_HW_PARAM_BUFFER_BYTES])<0 ||
       snd_interval_step(&p,c->steps[SNDRV_PCM_HW_PARAM_PERIOD_BYTES])<0)return -EINVAL;
    snd_interval_div(&b,&p,&quotient);
    return snd_interval_refine(&periods,&quotient)<0 ? -EINVAL : 0;
}
static void test_open_constraints(void)
{
    reset();init_constraints();allow_playback=allow_capture=false;
    assert(mt_open(&component,&sub[0])==-EPERM);
    assert(mt_open(&component,&sub[1])==-EPERM);
    assert(!audio.users && !pmic_calls);
    for(unsigned i=0;i<ARRAY_SIZE(gpio);i++)assert(!gpio[i]);
    allow_playback=allow_capture=true;
    reset();init_constraints();
    assert(mt_open(&component,&sub[1])==0); /* minmax and integer return +1 */
    assert(runtime[1].hw_constraints.intervals[0].min==8192);
    assert(runtime[1].hw_constraints.intervals[0].max==MT_CAPTURE_BYTES);
    assert(runtime[1].hw_constraints.intervals[2].integer);
    assert(refine_choice(3840,1920)==-EINVAL);
    assert(refine_choice(8160,2040)==-EINVAL);
    assert(refine_choice(8192,2040)==-EINVAL); /* period alignment */
    assert(refine_choice(8192,1920)==-EINVAL); /* not integer periods */
    assert(refine_choice(9600,1920)==0);assert(refine_choice(8192,2048)==0);
    assert(refine_choice(65536,16384)==0);assert(refine_choice(65568,16384)==-EINVAL);
    assert(mt_close(&component,&sub[1])==0);
    reset();init_constraints();runtime[1].hw_constraints.intervals[0].max=4096;
    assert(mt_open(&component,&sub[1])==-EINVAL);assert(!audio.users);
    assert(!audio.stream[1].substream);assert(!pmic_calls);
    puts("PASS actual open + kernel ALSA interval APIs: minimum/range/step/integer periods, +1 and errors");
}
static void test_owner_status(void)
{
    char buf[4096];reset();
    assert(analogue_status_show(&component_device,NULL,buf)>0);
    assert(!strcmp(buf,"PWR_STATUS.audio=0 DAC_CON0=0x00000000 ADDA_DL=0x00000000 AUDDEC0=0x0000 UL_DL=0x0000\n"));
    spm[0x180/4]=1<<24;afe[0x010/4]=5;afe[0x108/4]=3;pmic[0x0cf2]=0xe080;pmic[0x2000]=1;
    assert(analogue_status_show(&component_device,NULL,buf)>0);
    assert(!strcmp(buf,"PWR_STATUS.audio=1 DAC_CON0=0x00000005 ADDA_DL=0x00000003 AUDDEC0=0xe080 UL_DL=0x0001\n"));
    audio.users=1;assert(analogue_status_show(&component_device,NULL,buf)==-EBUSY);audio.users=0;
    read_fail=0x0cf2;assert(analogue_status_show(&component_device,NULL,buf)==-EIO);
    read_fail=0x2000;assert(analogue_status_show(&component_device,NULL,buf)==-EIO);read_fail=0;
    assert(!pmic_calls);assert(afe[0x010/4]==5 && afe[0x108/4]==3 && spm[0x180/4]==1<<24);
    for(unsigned i=0;i<ARRAY_SIZE(gpio);i++)assert(!gpio[i]);
    puts("PASS owner status: actual registers, card drvdata, busy/error refusal, no register writes");
}
static void *stop_thread(void *arg)
{ mt_stop(&audio.stream[(int)(long)arg]);return NULL; }
static void *start_thread(void *arg)
{
    starter=1;assert(mt_trigger(&component,&sub[(int)(long)arg],SNDRV_PCM_TRIGGER_START)==0);
    assert(!pthread_mutex_lock(&schedule_lock));start_done=1;
    assert(!pthread_cond_broadcast(&schedule_cond));assert(!pthread_mutex_unlock(&schedule_lock));return NULL;
}
static void test_duplex_state_transaction(void)
{
    for(int stopping=0;stopping<2;stopping++) {
        pthread_t stop, start;int peer=1-stopping;
        reset();allocate(0);allocate(1);
        assert(mt_prepare(&component,&sub[0])==0);assert(mt_prepare(&component,&sub[1])==0);
        assert(mt_trigger(&component,&sub[stopping],SNDRV_PCM_TRIGGER_START)==0);
        pause_clear=1;at_clear=release_clear=start_attempt=start_done=0;
        assert(!pthread_create(&stop,NULL,stop_thread,(void *)(long)stopping));
        assert(!pthread_mutex_lock(&schedule_lock));
        while(!at_clear)assert(!pthread_cond_wait(&schedule_cond,&schedule_lock));
        assert(!pthread_mutex_unlock(&schedule_lock));
        assert(!pthread_create(&start,NULL,start_thread,(void *)(long)peer));
        assert(!pthread_mutex_lock(&schedule_lock));
        while(!start_attempt && !start_done)assert(!pthread_cond_wait(&schedule_cond,&schedule_lock));
        /* Real trylock proves contention; otherwise wait for completed START. */
        int crossed=start_done;
        release_clear=1;assert(!pthread_cond_broadcast(&schedule_cond));
        assert(!pthread_mutex_unlock(&schedule_lock));
        assert(!pthread_join(stop,NULL));assert(!pthread_join(start,NULL));pause_clear=0;
        assert(!audio.stream[stopping].running);assert(audio.stream[peer].running);
        assert(afe[0x010/4]&1);assert(afe[0x010/4]&(peer ? 0x40 : 4));assert(!crossed);
        assert(audio.stream[peer].timer.active);
        assert(mt_hw_free(&component,&sub[0])==0);assert(mt_hw_free(&component,&sub[1])==0);
    }
    puts("PASS pthread duplex STOP/START rendezvous both directions: shared AFE stays enabled; cancel outside lock");
}
int main(void)
{
    test_card_drvdata();test_owner_status();
    test_open_constraints();test_duplex_state_transaction();
    test_ring();test_fm_lifecycle();test_codec_errors();test_pcm_copy();test_poll_xrun();
    puts("SOURCE-EXTRACTED OFFLINE CALLBACK TESTS PASSED (no hardware)");return 0;
}
''')
path = output / 'driver-callbacks-host.c'
path.write_text('\n\n'.join(parts))
exe = output / 'driver-callbacks-host'
subprocess.run(['cc', '-std=gnu11', '-g', '-O1', '-Wall', '-Wextra', '-Werror',
                '-Wno-missing-field-initializers',
                '-Wno-unused-function', '-Wno-unused-variable', '-Wno-unused-parameter',
                '-pthread', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                '-I', str(source), str(path), '-o', str(exe)], check=True)
subprocess.run([str(exe)], check=True, timeout=30)
