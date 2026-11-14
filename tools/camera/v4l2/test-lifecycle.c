// SPDX-License-Identifier: GPL-2.0
/* Extracted production STREAMON/STREAMOFF bodies, mocked kernel boundaries. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "mtk-capture.h"
#define GFP_KERNEL 0
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x) = (v))
#define lower_32_bits(x) ((u32)(x))
#define IS_ERR(x) ((intptr_t)(x) < 0 && (intptr_t)(x) > -4096)
#define PTR_ERR(x) ((int)(intptr_t)(x))
#define dev_err(...) ((void)0)
/* Explicit offline count shim; real core queue validation is tested separately. */
#define min_t(t,a,b) ((t)(a) < (t)(b) ? (t)(a) : (t)(b))
struct board_mode { u32 width, height, vts; };
struct device { int unused; };
struct platform { struct device dev; };
struct camera;
struct vb2_queue { struct camera *c; unsigned int allocated; };
static unsigned int vb2_get_num_buffers(struct vb2_queue *q) { return q->allocated; }
struct camera {
	struct board *board;
	struct mtk_fifo queued;
	unsigned int sequence;
	bool stopping;
	void *worker;
	int wait, video, pipeline;
};
struct board {
	struct camera *owner;
	bool poisoned, safe, pm_transition, snapshot;
	int owner_lock;
	struct platform *pdev;
	void *dma_cpu;
	u64 dma_address;
	size_t dma_size;
};
enum vb2_buffer_state { VB2_BUF_STATE_QUEUED, VB2_BUF_STATE_ERROR };
struct notifier_block { int unused; };
enum { PM_SUSPEND_PREPARE, PM_HIBERNATION_PREPARE, PM_RESTORE_PREPARE,
	PM_POST_SUSPEND, PM_POST_HIBERNATION, PM_POST_RESTORE };
#define NOTIFY_OK 1
static int notifier_from_errno(int error) { return error; }
static struct board *board;
static bool allow_streaming;
static int fail, frees, allocations, pipelines, powered, cleanups, joins, returns;
static unsigned int returned[8], states[8];
static u8 storage[64];
static const struct board_mode mode = { 1632, 1224, 1246 };
static const struct board_mode *current_mode(struct camera *c) { (void)c; return &mode; }
static struct camera *vb2_get_drv_priv(struct vb2_queue *q) { return q->c; }
static void mutex_lock(int *lock) { assert(!*lock); *lock = 1; }
static void mutex_unlock(int *lock) { assert(*lock); *lock = 0; }
static int video_device_pipeline_start(int *video, int *pipeline)
{
	(void)video; (void)pipeline;
	if (fail == 2) return -22;
	pipelines++; return 0;
}
static void video_device_pipeline_stop(int *video) { (void)video; assert(pipelines == 1); pipelines--; }
static void *dma_alloc_coherent(struct device *d, size_t size, u64 *addr, int flags)
{
	(void)d; (void)flags; assert(size == 1632U * 1224 * 5 / 4);
	allocations++; *addr = 0x50000000; return fail == 3 ? NULL : storage;
}
static void dma_free_coherent(struct device *d, size_t size, void *p, u64 addr)
{
	(void)d; (void)size; (void)addr; assert(p == storage); frees++;
}
static int power_up(struct camera *c) { c->board->snapshot = true; powered++; return fail == 4 || fail == 6 ? -44 : 0; }
static int power_down(struct camera *c) { assert(c->board->snapshot); c->board->snapshot = false; cleanups++; if (fail == 6 || fail >= 10) c->board->poisoned = true; if (fail == 6 || fail == 11) c->board->safe = false; return fail == 6 || fail >= 10 ? -66 : 0; }
static unsigned int task_refs;
static struct camera *worker_camera;
static void *mock_kthread_create(struct camera *c) { worker_camera = c; task_refs = fail == 5 ? 0 : 1; return fail == 5 ? (void *)(intptr_t)-55 : storage; }
#define kthread_create(fn, c, ...) mock_kthread_create(c)
#define get_task_struct(worker) do { assert((worker) == storage); task_refs++; } while (0)
static void wake_up_process(void *worker) { assert(worker == storage); if (fail >= 9) { power_down(worker_camera); task_refs--; } }
#define kthread_run(fn, c, ...) ({ void *task = mock_kthread_create(c); if (!IS_ERR(task)) wake_up_process(task); task; })
static void wake_up(int *wait) { (void)wait; }
static void kthread_stop(void *worker)
{
	assert(worker == storage && task_refs); joins++;
	if (fail < 9) task_refs--;
	/* A stopped-before-entry worker never executes its cleanup body. */
}
static void __attribute__((unused)) kthread_stop_put(void *worker) { kthread_stop(worker); assert(task_refs); task_refs--; }
static void return_buffers(struct camera *c, enum vb2_buffer_state state)
{
	unsigned int *p;
	while ((p = mtk_fifo_pop(&c->queued))) {
		assert(*p < 8); returned[*p]++; states[*p] = state; returns++;
	}
}
#include "extracted-lifecycle.h"
int main(void)
{
	/* Initial/repeated allocations and smaller partial-allocation rechecks. */
	for (unsigned int held = 0; held <= MTK_QUEUE_DEPTH; held++) {
		for (unsigned int request = 1; request <= 40; request++) {
			struct camera c = { 0 };
			struct vb2_queue q = { .c = &c, .allocated = held };
			unsigned int count = request, planes = 0, sizes[1] = { 0 };
			int ret = queue_setup(&q, &count, &planes, sizes, NULL);
			if (held == MTK_QUEUE_DEPTH) { assert(ret == -ENOBUFS); continue; }
			assert(!ret && count <= request && count + held <= MTK_QUEUE_DEPTH);
			assert(planes == 1 && sizes[0] == mode.width * mode.height * 2);
			for (unsigned int allocated = 1; allocated < count; allocated++) {
				q.allocated = held + allocated;
				unsigned int retry = allocated;
				assert(!queue_setup(&q, &retry, &planes, sizes, NULL));
				assert(retry <= allocated);
			}
		}
	}
	puts("PASS extracted queue setup: total eight-buffer cap, repeated CREATE_BUFS and partial-allocation checks");
	{
		struct board b = { 0 };
		struct camera c = { .board = &b };
		struct vb2_queue q = { .c = &c };
		unsigned int buffer = 0;
		assert(!mtk_fifo_push(&c.queued, &buffer));
		assert(start_streaming(&q, 2) == -EPERM);
		assert(!b.owner && !b.owner_lock && !allocations && !powered && !pipelines && !c.worker);
		assert(!c.queued.count && returns == 1 && states[0] == VB2_BUF_STATE_QUEUED);
	}
	allow_streaming = true; /* Remaining cases explicitly exercise admitted capture. */
	/* Production PM notifier rejects a live/starved/error-owned stream, and
	 * idle prepare blocks new STREAMON before any allocation/power operation.
	 */
	for (unsigned int event = PM_SUSPEND_PREPARE; event <= PM_RESTORE_PREPARE; event++) {
		struct board b = { 0 };
		struct camera c = { .board = &b };
		struct vb2_queue q = { .c = &c };
		board = &b;
		b.owner = &c;
		assert(camera_pm_event(NULL, event, NULL) == -EBUSY);
		assert(!b.pm_transition && b.owner == &c);
		b.owner = NULL;
		assert(camera_pm_event(NULL, event, NULL) == NOTIFY_OK && b.pm_transition);
		assert(start_streaming(&q, 2) == -EBUSY);
		assert(!allocations && !powered && !b.owner);
		assert(camera_pm_event(NULL, event + 3, NULL) == NOTIFY_OK && !b.pm_transition);
	}
	for (fail = 0; fail <= 11; fail++) {
		struct platform platform = { 0 };
		struct board b = { .pdev = &platform };
		struct camera c = { .board = &b }, other = { .board = &b };
		struct vb2_queue q = { .c = &c };
		unsigned int buffers[8];
		int ret;

		frees = allocations = pipelines = powered = cleanups = joins = returns = task_refs = 0;
		memset(returned, 0, sizeof(returned)); memset(states, 0, sizeof(states));
		for (unsigned int i = 0; i < 8; i++) { buffers[i] = i; assert(!mtk_fifo_push(&c.queued, &buffers[i])); }
		if (fail == 1) b.owner = &other;
		if (fail == 7) b.poisoned = true;
		ret = start_streaming(&q, 8);
		if (fail == 0 || fail >= 8) {
			assert(!ret && b.owner == &c && c.worker && !returns);
			assert(!mtk_fifo_push(&other.queued, &buffers[0]));
			struct vb2_queue other_q = { .c = &other };
			assert(start_streaming(&other_q, 1) == -EBUSY);
			assert(b.owner == &c); /* failed second sensor cannot steal shared reset/power */
			returned[0] = 0; returns = 0;
			if (fail == 8) b.safe = false;
			stop_streaming(&q);
			assert(!b.owner && c.stopping && !c.worker && joins == 1);
			assert(cleanups == 1 && !task_refs);
			assert(frees == (fail == 8 || fail == 11 ? 0 : 1));
			assert(b.poisoned == (fail == 8 || fail >= 10));
			for (unsigned int i = 0; i < 8; i++) assert(states[i] == VB2_BUF_STATE_ERROR);
		} else {
			assert(ret < 0 && !b.owner_lock && !c.worker);
			assert(b.owner == (fail == 1 ? &other : NULL));
			assert(frees == (fail == 4 || fail == 5 ? 1 : 0));
			assert(cleanups == (fail == 4 || fail == 5 || fail == 6 ? 1 : 0));
			for (unsigned int i = 0; i < 8; i++) assert(states[i] == VB2_BUF_STATE_QUEUED);
		}
		assert(!pipelines && !c.queued.count && returns == 8);
		for (unsigned int i = 0; i < 8; i++) assert(returned[i] == 1);
	}
	puts("PASS extracted production lifecycle: default capture denial before power/allocation; PM/STREAMON exclusion; normal stop, busy owner, pipeline/allocate/power/thread failure, poisoned admission, unsafe DMA quarantine; all eight buffers returned once");
	return 0;
}
