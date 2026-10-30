"""PyInstaller entry point for the SectorRec desktop application."""
import sys


def askpass(smoke_test=False):
    """SectorRec-themed sudo -A prompt (separate process, never privileged)."""
    import tkinter as tk
    from importlib.resources import files

    # Keep this palette in sync with src.views.desktop_native. Importing the
    # desktop view here would unnecessarily initialize the main GUI in askpass.
    bg = '#0C1321'
    surface = '#141F30'
    border = '#304157'
    text = '#F0F6FC'
    muted = '#9EADC1'
    teal = '#56DFC0'

    root = tk.Tk()
    root.withdraw()
    root.title('SectorRec — Administrator authorization')
    root.configure(bg=bg)
    root.resizable(False, False)
    root.option_add('*Font', ('DejaVu Sans', 10))
    root.attributes('-topmost', True)
    result = [None]
    password = tk.StringVar()
    reveal = tk.BooleanVar(value=False)

    body = tk.Frame(root, bg=bg, padx=28, pady=24)
    body.pack(fill='both', expand=True)

    header = tk.Frame(body, bg=bg)
    header.pack(fill='x', anchor='w')
    try:
        image = tk.PhotoImage(file=str(files('src').joinpath('assets', 'sectorrec_desktop_icon.png')))
        icon = image.subsample(7, 7)
        root.iconphoto(True, image)
        root._sectorrec_icon = (image, icon)  # retain Tk references
        tk.Label(header, image=icon, bg=bg, bd=0).pack(side='left', padx=(0, 12))
    except (OSError, tk.TclError, ImportError):
        pass  # Authorization must still work if the optional icon is missing.

    heading = tk.Frame(header, bg=bg)
    heading.pack(side='left', anchor='w')
    tk.Label(heading, text='ADMINISTRATOR ACCESS', bg=bg, fg=teal,
             font=('DejaVu Sans', 9, 'bold'), anchor='w').pack(anchor='w')
    tk.Label(heading, text='Authorize disk access', bg=bg, fg=text,
             font=('DejaVu Sans', 16, 'bold'), anchor='w').pack(anchor='w', pady=(3, 0))

    tk.Frame(body, height=1, bg=border).pack(fill='x', pady=(18, 16))
    tk.Label(body, text='SectorRec needs permission to access physical drives.',
             bg=bg, fg=text, anchor='w', justify='left').pack(anchor='w')
    tk.Label(body, text='Your password is sent directly to sudo and is never stored.',
             bg=bg, fg=muted, anchor='w', justify='left',
             font=('DejaVu Sans', 9)).pack(anchor='w', pady=(5, 17))

    tk.Label(body, text='Linux administrator password', bg=bg, fg=muted,
             font=('DejaVu Sans', 9, 'bold'), anchor='w').pack(anchor='w', pady=(0, 6))
    entry_frame = tk.Frame(body, bg=surface, highlightthickness=1,
                           highlightbackground=border, highlightcolor=teal, bd=0)
    entry_frame.pack(fill='x')
    field = tk.Entry(entry_frame, textvariable=password, show='●',
                     bg=surface, fg=text, insertbackground=teal, bd=0,
                     highlightthickness=0, relief='flat',
                     font=('DejaVu Sans', 12))
    field.pack(fill='x', ipady=10, padx=11)
    field.bind('<FocusIn>', lambda e: entry_frame.configure(highlightbackground=teal))
    field.bind('<FocusOut>', lambda e: entry_frame.configure(highlightbackground=border))

    def toggle_password():
        field.configure(show='' if reveal.get() else '●')

    options = tk.Frame(body, bg=bg)
    options.pack(fill='x', pady=(9, 0))
    tk.Checkbutton(options, text='Show password', variable=reveal,
                   command=toggle_password, bg=bg, fg=muted,
                   selectcolor=surface, activebackground=bg,
                   activeforeground=text, highlightthickness=0,
                   bd=0).pack(side='left')
    tk.Label(options, text='One authorization per session',
             bg=bg, fg=teal, font=('DejaVu Sans', 9)).pack(side='right')

    def accept(event=None):
        result[0] = password.get()
        root.destroy()

    def cancel(event=None):
        root.destroy()

    footer = tk.Frame(body, bg=bg)
    footer.pack(fill='x', pady=(18, 0))
    tk.Button(footer, text='Authorize', command=accept,
              bg=teal, fg=bg, activebackground='#80EBD3', activeforeground=bg,
              font=('DejaVu Sans', 10, 'bold'), relief='flat', bd=0,
              cursor='hand2', padx=18, pady=10).pack(side='right')
    tk.Button(footer, text='Cancel', command=cancel,
              bg=surface, fg=text, activebackground=border,
              activeforeground=text, relief='flat', bd=0,
              cursor='hand2', padx=18, pady=10).pack(side='right', padx=(0, 10))

    root.bind('<Return>', accept)
    root.bind('<Escape>', cancel)
    root.protocol('WM_DELETE_WINDOW', cancel)
    root.update_idletasks()
    width, height = 484, root.winfo_reqheight()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f'{width}x{height}+{max(0, (sw-width)//2)}+{max(0, (sh-height)//2)}')
    root.deiconify()
    root.lift()
    field.focus_force()
    if smoke_test:
        # Real UI boot, then automatic dismissal: safe on a headless CI runner,
        # does not request or print any password.
        root.after(250, root.destroy)
    root.mainloop()
    if smoke_test:
        return 0
    if result[0] is None:
        return 1
    # SUDO_ASKPASS receives the secret on stdout; it is never logged or sent to
    # the main SectorRec process. The Tk widget dies with this short-lived app.
    sys.stdout.write(result[0] + '\n')
    sys.stdout.flush()
    return 0


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--check-tk-runtime':
        # Packaging smoke test: prove the frozen program can load its own BLT
        # copies (not the build host's), then initialize a real Tcl/Tk window.
        if getattr(sys, 'frozen', False) and __import__('os').environ.get('SECTORREC_APPIMAGE') == '1':
            import ctypes
            from pathlib import Path
            internal = Path(sys.executable).parent / '_internal'
            for prefix in ('libBLT.2.5.so.', 'libBLTlite.2.5.so.'):
                matches = sorted(internal.glob(prefix + '*'))
                if not matches:
                    raise RuntimeError(f'AppImage is missing {prefix}* in {internal}')
                ctypes.CDLL(str(matches[0]))
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.destroy()
        raise SystemExit(0)
    if len(sys.argv) > 1 and sys.argv[1] == '--check-askpass-subprocess':
        # Launch from the *already frozen parent*, using the same inherited
        # environment that sudo -A receives during real disk authorization.
        import os
        import subprocess
        from src.infrastructure.appimage_environment import host_environment
        dialog = os.environ.get('SECTORREC_ASKPASS')
        if not dialog:
            raise RuntimeError('AppImage askpass wrapper was not configured')
        process = subprocess.run([dialog, '--smoke-test'],
                                 env=host_environment(),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 timeout=20, check=False)
        if process.returncode or process.stdout.strip():
            sys.stderr.write(process.stderr.decode('utf-8', 'replace')[-800:])
            raise SystemExit(process.returncode or 1)
        raise SystemExit(0)
    if len(sys.argv) > 1 and sys.argv[1] == '--sectorrec-askpass':
        raise SystemExit(askpass(smoke_test='--smoke-test' in sys.argv[2:]))
    from src.app import main
    sys.argv = [sys.argv[0], '--ui', 'desktop', *sys.argv[1:]]
    raise SystemExit(main())
