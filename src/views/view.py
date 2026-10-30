"""Pillow rendering for Cardputer Zero and CLI screenshots. No disk or input I/O."""
import time
import textwrap
from pathlib import Path
from importlib.resources import files
from src import __version__ as APP_VERSION
from PIL import Image, ImageDraw, ImageFont
W,H=320,170
from src.models.files import human_size
from src.services.device_diagnostics import report_lines
from src.views.elapsed import format_elapsed

BG='#0C1220'
PANEL='#151F30'
PANEL_ALT='#1B2940'
EDGE='#2A3B54'
TEXT='#F2F5FB'
MUTED='#8FA2B9'
ACCENT='#4EE1C1'
BLUE='#75B3FF'
GOLD='#FFC76B'

# Compact, one-line scanning tips. Never imply ESC completes the scan.
SCAN_TIPS = (
    'ESC stops early; review and save partial results.',
    'Scan reads deleted metadata; no files are copied yet.',
    'Stopping early keeps matches but may miss other files.',
    'Deleted entries may contain overwritten or missing data.',
    'Some recoverable files have no known extension.',
    'Recycle bins and deleted folders are also checked.',
    'Recoverable files may still appear in OTHER.',
    'Filters are available after scanning completes.',
    'Elapsed time only: no reliable scan ETA is available.',
    'PC SectorRec supports PhotoRec and more tools.',
    'Damaged media can take a long time to read.',
    'Large drives need time. Do not unplug yet.',
    'If you need an image copy, use SectorRec PC.',
)
STOPPING_TIP = 'Stopping early... keeping matches already found.'
SCAN_CANCEL_LABEL = 'Stop early / view partial results'

FONT='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
BOLD='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'


def fonts():
    """Use host fonts without bundling proprietary or system font files."""
    candidates = [
        (FONT,BOLD),
        ('/System/Library/Fonts/Supplemental/Arial.ttf',
         '/System/Library/Fonts/Supplemental/Arial Bold.ttf'),
        ('C:/Windows/Fonts/arial.ttf','C:/Windows/Fonts/arialbd.ttf'),
        ('DejaVuSans.ttf','DejaVuSans-Bold.ttf'),
    ]
    result={}
    for name,size in {'logo':14,'title':15,'bold':11,'text':10,'small':9,'micro':8,'microbold':8}.items():
        font=None
        for plain,heavy in candidates:
            try:
                font=ImageFont.truetype(heavy if name in ('title','bold','microbold') else plain,size)
                break
            except OSError:
                pass
        result[name]=font or ImageFont.load_default(size=size)
    return result


def shorten(s, limit):
    if len(s)<=limit:return s
    if limit<=4:return s[:limit]
    return s[:limit-3]+'...'



class PillowView:
    """Renderer observes the controller but has no capability to modify disks."""
    def __init__(self, state):
        self.state = state
        self.configure_view()

    def __getattr__(self, name):
        # Read controller state without inheriting from the controller.
        return getattr(self.state, name)

    def configure_view(self):
        self.f=fonts()
        icon_path=files('src').joinpath('assets')/'sectorrec_welcome.png'
        try:
            self.welcome_icon=Image.open(icon_path).convert('RGBA').resize((84,91),Image.Resampling.LANCZOS)
        except (OSError,ValueError):
            self.welcome_icon=None

    def label(self,d,xy,s,font='text',fill=TEXT,anchor=None):
        d.text(xy,str(s),font=self.f[font],fill=fill,anchor=anchor)

    def rounded(self,d,box,fill,outline=None,radius=9,width=1):
        d.rounded_rectangle(box,radius,fill=fill,outline=outline,width=width)

    def icon(self,d,x,y,kind,fill=ACCENT):
        # 18x18 vector icons, no emoji/font dependency.
        x=int(x);y=int(y)
        if kind=='drive':
            d.rounded_rectangle((x,y+4,x+19,y+15),radius=3,outline=fill,width=2)
            d.line((x+3,y+10,x+16,y+10),fill=fill,width=1)
            d.ellipse((x+14,y+12,x+16,y+14),fill=fill)
        elif kind=='file':
            d.polygon([(x+3,y+1),(x+12,y+1),(x+17,y+6),(x+17,y+19),(x+3,y+19)],outline=fill)
            d.line((x+12,y+1,x+12,y+6,x+17,y+6),fill=fill,width=1)
            d.line((x+6,y+11,x+14,y+11),fill=fill,width=1)
            d.line((x+6,y+14,x+13,y+14),fill=fill,width=1)
        elif kind=='shield':
            d.polygon([(x+10,y),(x+18,y+4),(x+17,y+13),(x+10,y+20),(x+3,y+13),(x+2,y+4)],outline=fill)
            d.line((x+6,y+10,x+9,y+13,x+14,y+7),fill=fill,width=2)
        elif kind=='search':
            d.ellipse((x+1,y+1,x+13,y+13),outline=fill,width=2)
            d.line((x+12,y+12,x+19,y+19),fill=fill,width=2)
        elif kind=='save':
            d.rectangle((x+2,y+2,x+18,y+18),outline=fill,width=2)
            d.line((x+6,y+2,x+6,y+8,x+14,y+8,x+14,y+2),fill=fill,width=1)
            d.rectangle((x+6,y+12,x+15,y+18),outline=fill,width=1)
        elif kind=='check':
            d.ellipse((x+1,y+1,x+19,y+19),outline=fill,width=2)
            d.line((x+5,y+10,x+9,y+14,x+15,y+6),fill=fill,width=2)
        elif kind=='warning':
            d.polygon([(x+10,y+1),(x+20,y+19),(x,y+19)],outline=fill)
            d.line((x+10,y+7,x+10,y+13),fill=fill,width=2)
            d.ellipse((x+9,y+16,x+11,y+18),fill=fill)
        else:
            d.ellipse((x+3,y+3,x+17,y+17),outline=fill,width=2)

    def welcome_art(self,im,d):
        """Original forensic-shield icon (v0.1.4), also used by AppLauncher."""
        if self.welcome_icon is not None:
            im.paste(self.welcome_icon,(4,45),self.welcome_icon)
        else:
            self.icon(d,36,80,'shield',ACCENT)

    def base(self,tag):
        im=Image.new('RGB',(W,self.viewport_height),BG)
        d=ImageDraw.Draw(im)
        d.rectangle((0,0,W,3),fill=ACCENT)
        self.rounded(d,(9,9,30,28),PANEL_ALT,radius=6)
        self.icon(d,10,10,'shield',ACCENT)
        self.label(d,(36,12),'SECTOR',font='bold',fill=TEXT)
        self.label(d,(88,12),'REC',font='bold',fill=ACCENT)
        # Keep the version in the unused header space, never over the stage tag.
        badge_text = 'v' + APP_VERSION
        badge_w = int(d.textlength(badge_text, font=self.f['microbold'])) + 12
        badge_left = 129
        self.rounded(d,(badge_left,10,badge_left+badge_w,26),PANEL_ALT,EDGE,radius=5)
        self.label(d,(badge_left+badge_w//2,18),badge_text,'microbold',ACCENT,anchor='mm')
        tag_width = max(47, int(d.textlength(tag,font=self.f['microbold'])) + 19)
        self.rounded(d,(310-tag_width,9,310,27),PANEL_ALT,radius=7)
        self.label(d,(310-tag_width//2,18),tag,'microbold',MUTED,anchor='mm')
        d.line((10,33,310,33),fill=EDGE,width=1)
        return im,d

    def footer(self,d,labels):
        fy = self.viewport_height - 24
        d.rectangle((0,fy,320,self.viewport_height),fill='#101B2D')
        d.line((0,fy,320,fy),fill=EDGE)
        x=10
        for key,desc in labels:
            keywidth=max(19,len(key)*5+9)
            self.rounded(d,(x,fy+5,x+keywidth,fy+18),PANEL_ALT,EDGE,radius=4)
            self.label(d,(x+keywidth//2,fy+11),key,'microbold',TEXT,anchor='mm')
            x+=keywidth+3
            self.label(d,(x,fy+7),desc,'micro',MUTED)
            x+=int(d.textlength(desc,font=self.f['micro']))+8

    def render(self, height=170):
        """Draw native LCD art or a taller desktop viewport in the same visual style."""
        self.viewport_height = max(170, min(1200, int(height)))
        stage=self.stage
        im,d=self.base({'welcome':'FORENSIC','devices':'01 / 04',
             'scan_ready':'02 / 04','scanning':'03 / 04','results':'04 / 04','results_exit_confirm':'CONFIRM','confirm':'SAVE','recovering':'SAVE',
             'complete':'DONE','notice':'INFO','unmounting':'PREP',
             'partition_ready':'PARTITION','partition_scanning':'SEARCH','partition_results':'FOUND','device_info':'INFO',
             'signature_setup':'SIGNATURE','signature_running':'EXTRACT','signature_summary':'REPORT'}.get(stage,'INFO'))
        if stage=='welcome':
            self.welcome_art(im,d)
            self.label(d,(93,45),'FIND DELETED FILES',font='title')
            self.label(d,(93,62),'SAVE WHAT MATTERS.',font='title',fill=ACCENT)
            self.label(d,(94,81),('Explore filesystem images' if not self.platform.raw_drives else 'Explore USB drives, SD cards'),font='small',fill=MUTED)
            self.label(d,(94,93),('for deleted file entries.' if not self.platform.raw_drives else 'and HDDs for deleted files.'),font='small',fill=MUTED)
            self.label(d,(94,105),'Choose exactly what to save.',font='small',fill=MUTED)
            self.rounded(d,(94,121,305,133),'#183D40',radius=5)
            self.label(d,(199,127),'READ-ONLY  /  SELECT & SAVE',font='microbold',fill=ACCENT,anchor='mm')
            self.footer(d,[('OK','Start'),('ESC','Hold 3s to exit')])
        elif stage=='devices':
            self.label(d,(12,41),'SELECT A DEVICE','bold')
            self.label(d,(308,42),'AUTO REFRESH','micro',MUTED,anchor='ra')
            if not self.devices:
                self.icon(d,26,72,'drive',GOLD)
                initial = not self.device_refresh_completed
                self.label(d,(57,66),'Scanning connected devices...' if initial else
                           ('Device scan unavailable' if self.device_refresh_error else 'No external volumes found'), 'text')
                self.label(d,(57,82),('File > Open filesystem image...' if not self.platform.raw_drives else 'Connect USB / SD. Auto detection on.'),'small',MUTED)
                self.label(d,(57,96),('Images are opened read-only.' if not self.platform.raw_drives else 'Root system drive is excluded.'),'small',MUTED)
            else:
                visible=max(3,(self.viewport_height-57-24)//29)
                start=max(0,self.selection-visible//2)
                start=min(start,max(0,len(self.devices)-visible))
                for idx,item in enumerate(self.devices[start:start+visible],start):
                    y=57+(idx-start)*29
                    chosen=idx==self.selection
                    self.rounded(d,(10,y,310,y+26),PANEL_ALT if chosen else PANEL,ACCENT if chosen else EDGE,radius=7)
                    # Indent a volume ONLY when its whole-disk parent is
                    # visible, so child partitions read as one device group.
                    parent=item.get('disk_path')
                    child=bool(parent and parent!=item.get('path') and
                               any(dv.get('path')==parent and dv.get('partition_disk')
                                   for dv in self.devices))
                    offset=10 if child else 0
                    if child:
                        d.line((20,y+5,20,y+17,26,y+17),fill=EDGE,width=1)
                    self.icon(d,15+offset,y+4,'drive',ACCENT if chosen else BLUE)
                    name=item['label'].replace('[WHOLE DISK]','').strip() if item.get('partition_disk') else item['label']
                    self.label(d,(42+offset,y+3),shorten(name,17 if child else 19),'bold' if chosen else 'text')
                    kind='WHOLE DISK' if item.get('partition_disk') else item['fs']
                    self.label(d,(42+offset,y+15),f"{kind} • {item['path']}",'micro',MUTED)
                    tag='MOUNT' if item['mounted'] else ('NO FS' if not item.get('supported',True) else human_size(item['size']))
                    self.label(d,(301,y+9),tag,'microbold',GOLD if item['mounted'] or not item.get('supported',True) else ACCENT,anchor='ra')
            self.footer(d,[('F/X','Move'),('OK','Select'),('C','Info')])
        elif stage=='device_info':
            self.label(d,(12,39),'DEVICE INFORMATION','bold')
            rep=self.diagnostic_report
            if self.diagnostic_loading:
                self.label(d,(12,65),'Reading available device data...','small',BLUE)
                self.label(d,(12,88),'No tests or writes are performed.','micro',MUTED)
            elif rep:
                lines=report_lines(rep)
                offset=min(self.diagnostic_scroll,max(0,len(lines)-4))
                for i,(key,value) in enumerate(lines[offset:offset+4]):
                    y=58+22*i
                    self.label(d,(12,y),shorten(key,17),'micro',MUTED)
                    self.label(d,(307,y),shorten(value,28),'small',ACCENT if key=='Health' and rep.health=='passed' else TEXT,anchor='ra')
                if len(lines)>4:self.label(d,(307,146),f'{offset+1}-{min(len(lines),offset+4)}/{len(lines)}','micro',BLUE,anchor='ra')
            else:
                self.label(d,(12,68),'SMART information unavailable.','small',GOLD)
                self.label(d,(12,87),'This is not a health verdict.','micro',MUTED)
            self.footer(d,[('F/X','Scroll'),('ESC','Back')])
        elif stage=='partition_ready':
            source=self.device or {}
            self.label(d,(12,42),'FIND LOST PARTITIONS','bold')
            self.icon(d,14,62,'drive',ACCENT)
            self.label(d,(43,59),shorten(source.get('label','Disk'),32),'bold')
            self.label(d,(43,76),human_size(source.get('size',0))+'  /  WHOLE DISK','small',BLUE)
            self.label(d,(12,101),'Scan for missing partitions. Read only.','small',TEXT)
            self.label(d,(12,117),'No disk repair or partition writes.','micro',MUTED)
            self.footer(d,[('OK','Find partitions'),('ESC','Back')])
        elif stage=='partition_scanning':
            self.label(d,(12,42),'SEARCHING PARTITIONS','bold')
            self.icon(d,15,67,'search',ACCENT)
            self.label(d,(44,68),'Read-only partition search','small',TEXT)
            elapsed=int(time.monotonic()-self.partition_status.get('started',time.monotonic()))
            self.label(d,(309,70),format_elapsed(elapsed, compact=True),'small',BLUE,anchor='ra')
            for i in range(24):
                x=12+i*12.5
                d.rounded_rectangle((x,116,x+8,124),radius=3,
                    fill=ACCENT if ((i-self.frame//2)%24)<7 else PANEL_ALT)
            # Partition results are displayed after the search, not as a
            # potentially misleading running '(N NEW)' count.
            count=self.partition_status.get('testdisk_io_bytes')
            self.rounded(d,(12,86,309,105),PANEL_ALT,EDGE,radius=5)
            self.label(d,(20,90),'READ I/O','microbold',MUTED)
            value=human_size(count) if count is not None else 'Checking…'
            self.label(d,(301,89),shorten(value,19),'small',BLUE,anchor='ra')
            self.label(d,(12,133),'Cumulative · not scan % · ESC stops early','micro',MUTED)
            self.footer(d,[('ESC','Stop search early')])
        elif stage=='partition_results':
            matches=self.partition_status.get('candidates',[])
            self.label(d,(12,41),f'{len(matches)} PARTITIONS FOUND','bold')
            if not matches:
                self.label(d,(13,78),'No validated locations found.','text',GOLD)
                self.label(d,(13,96),'Try a different source image.','small',MUTED)
            else:
                visible=max(2,(self.viewport_height-60-24)//30)
                start=max(0,min(self.partition_index-visible//2,len(matches)-visible))
                for idx,item in enumerate(matches[start:start+visible],start):
                    y=59+(idx-start)*30
                    active=idx==self.partition_index
                    self.rounded(d,(10,y,309,y+27),PANEL_ALT if active else PANEL,
                                 ACCENT if active else EDGE,radius=6)
                    self.icon(d,15,y+4,'drive',ACCENT if active else BLUE)
                    self.label(d,(43,y+4),shorten(item.label,23),'bold' if active else 'text')
                    self.label(d,(43,y+17),f'Sector {item.start}','micro',MUTED)
                    self.label(d,(303,y+11),human_size(item.size),'microbold',ACCENT,anchor='ra')
            self.footer(d,[('F/X','Move'),('OK','Browse files'),('ESC','Back')])
        elif stage=='scan_ready':
            source=self.device or {}
            self.label(d,(12,42),'READY TO SCAN?', 'bold')
            self.icon(d,13,62,'drive',ACCENT)
            self.label(d,(42,58),shorten(source.get('label','Selected drive'),30),'bold')
            details = f"{human_size(source.get('size',0))}  /  {source.get('fs','?')}"
            self.label(d,(42,73),shorten(details,41),'small',BLUE)
            self.label(d,(12,95),'Scan deleted-file metadata on this source.', 'small',TEXT)
            self.label(d,(12,110),'Nothing is recovered until you choose files.', 'micro',MUTED)
            self.label(d,(12,122),'Mounted drives will be unmounted first.', 'micro',GOLD)
            self.footer(d,[('OK','Start scan'),('ESC','Back')])
        elif stage=='signature_setup':
            source=self.device or {}
            self.label(d,(12,42),'SIGNATURE RECOVERY','bold')
            self.icon(d,14,66,'search',GOLD)
            self.label(d,(43,60),shorten(source.get('label','Source'),35),'bold')
            self.label(d,(43,76),f"{human_size(source.get('size',0))}  /  {source.get('fs','?')}",'small',MUTED)
            self.label(d,(12,100),'PhotoRec extracts files while scanning.','small',TEXT)
            self.label(d,(12,114),'Choose an output folder first.','small',GOLD)
            self.label(d,(12,128),'Signatures, not metadata or filenames.','micro',MUTED)
            self.footer(d,[('PANEL','Choose folder / start')])
        elif stage=='signature_running':
            st=self.signature_status
            self.label(d,(12,42),'EXTRACTING SIGNATURES','bold')
            self.label(d,(12,64),f"{st.get('files',0):,} files extracted",'bold',ACCENT)
            self.label(d,(12,84),f"Written: {human_size(st.get('written',0))}",'small',TEXT)
            self.label(d,(173,84),f"Free: {human_size(st.get('free',0))}",'small',BLUE)
            for i in range(24):
                x=12+i*12.5
                d.rounded_rectangle((x,107,x+8,115),radius=3,
                    fill=ACCENT if ((i-self.frame//2)%24)<7 else PANEL_ALT)
            self.label(d,(12,126),'Writing files now. Progress % is unknown.','micro',MUTED)
            self.footer(d,[('PANEL','Stop extraction early')])
        elif stage=='signature_summary':
            st=self.signature_status
            state=st.get('state')
            self.icon(d,18,49,'check' if state=='done' else 'warning',
                      ACCENT if state=='done' else GOLD)
            self.label(d,(46,50),'SIGNATURE REPORT','bold')
            self.label(d,(14,79),f"{st.get('files',0):,} files extracted",'bold',TEXT)
            self.label(d,(14,98),f"Written: {human_size(st.get('written',0))}",'small',MUTED)
            self.label(d,(14,114),shorten(st.get('error') or st.get('stopped_reason') or 'Extraction finished.',43),
                       'small',GOLD if state!='done' else ACCENT)
            self.footer(d,[('PANEL','Choose another source')])
        elif stage=='scanning':
            self.label(d,(13,41),'STOPPING SCAN...' if self.scan_stop_requested else 'SCANNING METADATA','bold')
            self.label(d,(308,43),shorten(self.device['path'] if self.device else 'Drive',21),'micro',MUTED,anchor='ra')
            count=self.status.get('count',0)
            self.icon(d,15,66,'search',ACCENT)
            self.label(d,(43,68),f'{count:,} file entries found','bold')
            now = time.monotonic()
            elapsed = max(0,now-self.status.get('started',now))
            self.label(d,(308,68),format_elapsed(elapsed, compact=True), 'bold',BLUE,anchor='ra')
            self.label(d,(13,94),f"ALL FILES  /  {self.status.get('phase','METADATA SEARCH').upper()}", 'micro',MUTED)
            # Indeterminate activity animation, never presented as a percentage.
            for i in range(24):
                x=12+i*12.5
                active=((i-self.frame//2)%24)<7
                d.rounded_rectangle((x,107,x+8,115),radius=3,fill=ACCENT if active else PANEL_ALT)
            # Tips fit the available 294px width; never spill into the footer.
            tip = (STOPPING_TIP if self.scan_stop_requested else
                   SCAN_TIPS[int(elapsed)//7 % len(SCAN_TIPS)])
            self.label(d,(12,126),tip,'micro',GOLD if self.scan_stop_requested else MUTED)
            self.footer(d,[('ESC',SCAN_CANCEL_LABEL)])
        elif stage=='results':
            count,total,unknown=self.found_details()
            label,category,ext=self.active_filter()
            self.label(d,(12,39),f'{count:,} FILES FOUND','bold')
            if self.scan_partial:
                self.label(d,(307,40),self.scan_result_label,'microbold',GOLD,anchor='ra')
            # One shared filter row; category + exact extension are independent.
            size_text=human_size(total)+(' +' if unknown else '')
            self.rounded(d,(10,54,122,71),'#193633',radius=5)
            self.label(d,(15,59),'SIZE','microbold',ACCENT)
            self.label(d,(118,59),shorten(size_text,10),'microbold',TEXT,anchor='ra')
            self.rounded(d,(125,54,217,71),PANEL_ALT,EDGE,radius=5)
            self.label(d,(130,59),'TAB','microbold',BLUE)
            self.label(d,(212,59),shorten(category,9),'microbold',ACCENT,anchor='ra')
            self.rounded(d,(220,54,310,71),PANEL_ALT,EDGE,radius=5)
            self.label(d,(225,59),'E','microbold',BLUE)
            self.label(d,(305,59),shorten(self.extension_label(),12),'microbold',ACCENT,anchor='ra')
            if count==0:
                self.icon(d,22,89,'search',GOLD)
                self.label(d,(51,85),'No matching files found.','text')
                self.label(d,(51,101),'Scan may be partial.' if self.scan_partial else 'Try a different result filter.','micro',MUTED)
            else:
                visible=max(3,(self.viewport_height-76-24)//23)
                start=max(0,self.result_index-visible//2)
                start=min(start,max(0,count-visible))
                for idx,row in enumerate(self.result_rows(start,visible),start):
                    _,_inode,path,size,_file_category,*_origin=row
                    y=75+(idx-start)*23
                    selected=idx==self.result_index
                    self.rounded(d,(9,y,300,y+21),PANEL_ALT if selected else PANEL,ACCENT if selected else EDGE,radius=5)
                    self.icon(d,10,y+1,'file',ACCENT if selected else BLUE)
                    self.label(d,(36,y+2),shorten(Path(path).name,30),'microbold' if selected else 'micro')
                    self.label(d,(36,y+12),shorten(path,38),'micro',MUTED)
                    self.label(d,(292,y+11),human_size(size),'microbold',ACCENT,anchor='rm')
                # Vertical position in the *filtered* result set.
                rail_top, rail_bottom = 76, self.viewport_height-28
                self.rounded(d,(305,rail_top,310,rail_bottom),PANEL_ALT,radius=2)
                height = rail_bottom-rail_top
                thumb = max(9, round(height*min(visible,count)/count))
                top = rail_top + round((height-thumb) * (self.result_index/(count-1) if count>1 else 0))
                self.rounded(d,(305,top,310,top+thumb),ACCENT,radius=2)
                if self.status.get('limited'):
                    self.label(d,(296,40),'LIMIT','microbold',GOLD,anchor='ra')
            self.footer(d,[('FX','Move'),('OK','Save'),('A','All'),('TAB','Group'),('E','Ext'),('ESC','Back')])
        elif stage=='results_exit_confirm':
            self.icon(d,19,49,'warning',GOLD)
            self.label(d,(47,51),'LEAVE FILE LIST?','bold',TEXT)
            self.rounded(d,(11,75,309,128),PANEL,EDGE,radius=7)
            self.label(d,(19,84),'Leaving closes the scanned file list.','small',TEXT)
            self.label(d,(19,100),'To view it again, scan this device again.','small',MUTED)
            self.footer(d,[('ESC','Keep list'),('OK','Leave')])
        elif stage in ('confirm','notice','complete'):
            failed=(stage=='complete' and self.status.get('state')=='error')
            kind='save' if stage=='confirm' else 'warning' if stage=='notice' or failed else 'check'
            col=GOLD if stage=='notice' or failed else ACCENT
            self.icon(d,19,48,kind,col)
            title={'confirm':'RECOVER SELECTED?','notice':'INFORMATION','complete':'RECOVERY REPORT'}[stage]
            if failed:title='RECOVERY ERROR'
            self.label(d,(47,51),title,'bold')
            lines=[sub for line in (self.message or '').split('\n') for sub in (textwrap.wrap(line, 45) or [''])]
            if stage=='complete' and not failed:
                lines=[*self.message.split('\n'),shorten(self.status.get('outdir',''),39),
                       f"{human_size(self.status.get('written',0))} written"]
            for i,line in enumerate(lines[:4]):
                self.label(d,(14,80+15*i),shorten(line,48),'small',MUTED)
            self.footer(d,[('OK','Confirm' if stage=='confirm' else 'Continue'),('ESC','Cancel / Back')])
        elif stage=='unmounting':
            self.icon(d,20,70,'drive',GOLD)
            self.label(d,(52,70),'PREPARING SOURCE','bold') if self.unmount_next=='partition_search' else self.label(d,(52,70),'UNMOUNTING VOLUME','bold')
            self.label(d,(52,89),shorten(self.unmount_path or '',32),'small',MUTED)
            self.label(d,(20,119),'Using UDisks. No formatting or writes.','micro',MUTED)
            self.footer(d,[('...', 'Please keep drive connected')])
        elif stage=='recovering':
            self.label(d,(12,42),'RECOVERING FILES','bold')
            done=self.status.get('done',0);total=self.status.get('total',0)
            self.label(d,(12,62),f'{done} / {total} processed','bold',ACCENT)
            self.label(d,(12,80),shorten(self.status.get('filename',''),47),'small',MUTED)
            self.rounded(d,(12,106,308,117),PANEL_ALT,radius=5)
            if total:
                progress=max(0,min(1,done/total))
                if progress>0:d.rounded_rectangle((12,106,12+max(5,int(296*progress)),117),radius=5,fill=ACCENT)
            self.label(d,(12,126),human_size(self.status.get('written',0))+' saved','small',MUTED)
            self.footer(d,[('ESC','Stop recovery / Results')])
        if self.exit_progress >= 0.15 and stage != 'exit':
            # Drawn above any page, including active scans. Holding ESC never
            # cancels a scan unless the hold succeeds (EXIT) or ESC is released (BACK).
            d.rounded_rectangle((34,94,286,139),radius=8,fill='#0A1B2B',outline=ACCENT,width=1)
            self.label(d,(48,101),'HOLD ESC TO EXIT','microbold',TEXT)
            self.label(d,(272,101),f'{int(self.exit_progress*100)}%','micro',ACCENT,anchor='ra')
            self.rounded(d,(48,120,272,127),PANEL_ALT,radius=3)
            filled=max(1,int(224*self.exit_progress))
            d.rounded_rectangle((48,120,48+filled,127),radius=3,fill=ACCENT)
        # In a taller desktop window the short-form pages remain a compact,
        # centered card. Device/result pages instead use the extra rows.
        if self.viewport_height > H and stage not in ('devices','results'):
            offset=(self.viewport_height-H)//2
            body=im.crop((0,34,320,146))
            d.rectangle((0,34,319,self.viewport_height-25),fill=BG)
            im.paste(body,(0,34+offset))
        return im

