"""Cardputer framebuffer event loop and asynchronous LCD transport."""
import queue
import threading
import time
import sys
class FrameWriter:
    """Latest-frame-only LCD output; never block evdev polling on fb0 writes."""
    def __init__(self, fb, profile=False):
        self.fb=fb
        self.profile=profile
        self.frames=queue.Queue(maxsize=1)
        self.stop_event=threading.Event()
        self.error=None
        self.stats_since=time.monotonic()
        self.stats_count=0
        self.stats_sum_ms=0.0
        self.stats_max_ms=0.0
        self.dropped=0
        self.thread=threading.Thread(target=self.run,daemon=True,name='lcd-writer')
        self.thread.start()

    def put(self, frame):
        if self.error is not None:
            raise RuntimeError('LCD output failed: '+str(self.error))
        try:
            self.frames.put_nowait(frame)
        except queue.Full:
            try:
                self.frames.get_nowait()
                self.dropped += 1
            except queue.Empty:pass
            try:self.frames.put_nowait(frame)
            except queue.Full:pass

    def run(self):
        while not self.stop_event.is_set():
            try: frame=self.frames.get(timeout=0.05)
            except queue.Empty:continue
            try:
                start=time.monotonic()
                self.fb.render(frame)
                duration=(time.monotonic()-start)*1000
                if self.profile:
                    self.stats_count += 1
                    self.stats_sum_ms += duration
                    self.stats_max_ms = max(self.stats_max_ms, duration)
                    elapsed=time.monotonic()-self.stats_since
                    if elapsed >= 1.0:
                        # LCD writes/second, NOT necessarily physical LCD scanout FPS.
                        print('LCD writes/s:',round(self.stats_count/elapsed,1),
                              'avg_ms:',round(self.stats_sum_ms/max(1,self.stats_count),2),
                              'worst_ms:',round(self.stats_max_ms,2),
                              'queued_frames_dropped:',self.dropped,
                              'native:',bool(getattr(self.fb,'native_blit',None)),
                              file=sys.stderr,flush=True)
                        self.stats_since=time.monotonic()
                        self.stats_count=0
                        self.stats_sum_ms=0.0
                        self.stats_max_ms=0.0
                        self.dropped=0
            except Exception as exc:
                self.error=exc
                return

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=1.5)
        if not self.thread.is_alive():
            self.fb.close()



def dispatch_input(ui, keyboard, dimmer, events):
    """Process a batch of evdev input; a blank-screen wake is inert.

    The initial ESC_DOWN is suppressed along with its later BACK/EXIT release
    and any pending directional key repeat. Background scan workers and
    ui.tick() continue normally while the LCD is blanked or waking.
    """
    if not events:
        return False
    was_off = dimmer.off
    dimmer.input()
    if was_off:
        keyboard.discard_wake_input()
        ui.exit_progress = 0.0
        return True
    for event in events:
        ui.handle(event)
        if ui.stage == 'exit':
            break
    return True


def run_framebuffer(ui, profile=False):
    """Run the Linux LCD/input adapter and close its owned resources."""
    from src.views.cardputer.keyboard import Keyboard
    from src.views.cardputer.idle_dim import IdleDimmer
    from PIL import ImageEnhance
    from src.views.cardputer.video import Framebuffer
    screen=None; writer=None;keyboard=None;dimmer=None
    try:
        screen=Framebuffer()
        writer=FrameWriter(screen,profile=profile)
        if profile:
            print('Framebuffer:',screen.width,'x',screen.height,
                  'bpp:',screen.bpp,'stride:',screen.line,
                  'RGB16 native:',bool(screen.native_blit),file=sys.stderr,flush=True)
        keyboard=Keyboard()
        dimmer=IdleDimmer()
        last_stage=None
        last_draw=0.0
        last_hold=-1.0
        last_info_tick=-1
        dirty=True
        while ui.stage!='exit':
            events=keyboard.poll(0.003)
            if dispatch_input(ui, keyboard, dimmer, events):
                dirty=True
            ui.tick()
            hold = keyboard.esc_hold_fraction()
            ui.exit_progress=hold
            now = time.monotonic()
            if dimmer.update(now): dirty=True
            busy = ui.stage in ('scanning','partition_scanning','recovering','unmounting')
            if events or ui.stage != last_stage or abs(hold-last_hold)>0.025:
                dirty=True
            # Time-based device refresh, scan counter and elapsed clock.
            info_tick=int(now*4) if busy else int(now*1) if ui.stage in ('devices','device_info') else -1
            if info_tick!=last_info_tick:
                dirty=True
                last_info_tick=info_tick
            if profile and events:
                print('keys:',events,'stage:',ui.stage,'t:',round(now,3),file=sys.stderr,flush=True)
            if ui.stage!='exit' and dirty and now-last_draw>=0.015:
                start=time.monotonic()
                if dimmer.off and dimmer.physical_off:
                    # Keep the display shut down even while scans update.
                    frame=None
                elif dimmer.software_off:
                    from PIL import Image
                    frame=Image.new('RGB', (screen.width,screen.height), 'black')
                else:
                    frame=ui.render()
                    if dimmer.software_dim:
                        frame=ImageEnhance.Brightness(frame).enhance(0.10)
                if frame is not None:
                    writer.put(frame)
                if profile and (events or time.monotonic()-start>0.02):
                    print('UI draw:',round((time.monotonic()-start)*1000,2),'ms',file=sys.stderr,flush=True)
                last_draw=now
                last_stage=ui.stage
                last_hold=hold
                dirty=False
    except (OSError,RuntimeError) as exc:
        print('SectorRec:',exc,file=sys.stderr)
        return 1
    finally:
        if dimmer:dimmer.close()
        ui.close()
        if keyboard:keyboard.close()
        if writer:writer.close()
        elif screen:screen.close()
    return 0
