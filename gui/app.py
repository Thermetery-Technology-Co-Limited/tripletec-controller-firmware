"""Threaded Tk desktop for the TripleTECDriver's acknowledged SWD protocol."""
from __future__ import annotations

import argparse
from collections import deque
import itertools
import math
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

from .model import (Snapshot, channel_status, current_trip_label, format_value,
                    margin_millic, mode_label)

COLORS = {"bg": "#0C121B", "panel": "#151E2B", "raised": "#1C2838",
          "line": "#2A384B", "text": "#EAF1FA", "muted": "#8C9EB7",
          "accent": "#51DAC4", "blue": "#79B8FF", "amber": "#F4C77A",
          "red": "#FF828C", "purple": "#BCABFF"}


def resource_root() -> Path:
    """Resolve optional bundled assets without depending on the launch folder."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


class LinkWorker(threading.Thread):
    """Serializes all hardware I/O; never accesses Tk or presentation state."""

    def __init__(self):
        super().__init__(name="TripleTEC device link", daemon=True)
        self.jobs = queue.PriorityQueue()
        self.events = queue.Queue()
        self.counter = itertools.count()
        self.generation = 0
        self.lock = threading.Lock()
        self.link = None

    def invalidate(self, generation):
        with self.lock:
            self.generation = generation

    def submit(self, generation, kind, payload=None, priority=10):
        self.jobs.put((priority, next(self.counter), generation, kind, payload))

    def run(self):
        while True:
            _, _, generation, kind, payload = self.jobs.get()
            with self.lock:
                current = self.generation
            if generation != current:
                continue
            if kind == "disconnect":
                self.link = None
                continue
            try:
                if kind == "connect":
                    # Late import allows offline/demo launches without the backend.
                    from scripts.device_link import DeviceLink
                    self.link = DeviceLink(root=None, serial=payload or None)
                    result = self.link.read_status()
                elif kind == "poll":
                    if self.link is None:
                        raise RuntimeError("Device link is disconnected. Select Connect to retry.")
                    result = self.link.read_status()
                elif kind == "command":
                    if self.link is None:
                        raise RuntimeError("Device link is disconnected. Select Connect to retry.")
                    result = None
                    for name, value in payload:
                        with self.lock:
                            if generation != self.generation:
                                break
                        result = self.link.command(name, value)
                    if result is None:
                        continue
                else:
                    continue
                self.events.put((generation, "status", (kind, result)))
            except Exception as error:
                self.events.put((generation, "error", (kind, str(error))))


class Dashboard:
    def __init__(self, root: tk.Tk, *, demo=False, connect=False, serial=None):
        self.root = root
        self.root.title("TripleTECControl · Thermal control")
        self.root.geometry("1100x760")
        self.root.minsize(900, 620)
        self.root.configure(bg=COLORS["bg"])
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.worker = LinkWorker()
        self.worker.start()
        self.generation = 0
        self.connected = False
        self.connecting = False
        self.poll_pending = False
        self.command_pending = False
        self.last_error = None
        self.snapshot = None
        self.closed = False
        self.dirty = False
        self.history = deque(maxlen=90)
        self.demo_started = time.monotonic()
        self.demo_mode = 0
        self.demo_margin = 3.0
        self.demo_armed = False
        self.source = tk.StringVar(value="Demo (simulated)" if demo else "Live hardware")
        self.serial = tk.StringVar(value=serial or "")
        self.mode = tk.IntVar(value=0)
        self.margin = tk.StringVar(value="3.0")
        self.connection_text = tk.StringVar(value="DISCONNECTED")
        self.detail_text = tk.StringVar(value="Connect through the Pico SWD probe to read the controller.")
        self.reported_mode = tk.StringVar(value="DEVICE MODE  —")
        self.message = tk.StringVar(value="Ready. Live connection requires OpenOCD on PATH and the Pico connected.")
        self.reason = tk.StringVar(value="Connect to a device before starting.")
        self.note = tk.StringVar()
        self.metric_values = []
        self.channel_values = []
        self.current_trip_values = []
        self._style()
        self._layout()
        self._render()
        self.root.after(100, self._drain)
        self.root.after(1000, self._tick)
        if demo:
            self._demo_update()
        elif connect:
            self.root.after(150, self.connect)

    def _style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=COLORS["raised"],
                        background=COLORS["raised"], foreground=COLORS["text"],
                        arrowcolor=COLORS["muted"], bordercolor=COLORS["line"], padding=7)
        style.map("TCombobox", fieldbackground=[("readonly", COLORS["raised"])],
                  foreground=[("readonly", COLORS["text"])])
        self.root.option_add("*TCombobox*Listbox.background", COLORS["raised"])
        self.root.option_add("*TCombobox*Listbox.foreground", COLORS["text"])
        self.root.option_add("*Font", "{Segoe UI} 10")

    def _label(self, parent, text="", *, variable=None, size=10, color="text", bold=False, **kw):
        return tk.Label(parent, text=text, textvariable=variable, bg=parent.cget("bg"),
                        fg=COLORS[color], font=("Segoe UI", size, "bold" if bold else "normal"),
                        anchor="w", **kw)

    def _button(self, parent, text, command, *, accent=False, danger=False):
        background = "#633341" if danger else ("#215A55" if accent else COLORS["raised"])
        button = tk.Button(parent, text=text, command=command, bg=background,
                           fg=COLORS["red"] if danger else COLORS["text"],
                           activebackground=COLORS["line"], activeforeground=COLORS["text"],
                           disabledforeground="#667589", bd=0, relief="flat", padx=16, pady=9,
                           cursor="hand2", font=("Segoe UI", 10, "bold"))
        return button

    def _panel(self, parent):
        return tk.Frame(parent, bg=COLORS["panel"], highlightbackground=COLORS["line"],
                        highlightthickness=1)

    def _layout(self):
        # Vertical scrolling keeps every control reachable at 900x620 and at
        # Windows text scaling levels above 100%; wide layouts still stretch.
        viewport = tk.Canvas(self.root, bg=COLORS["bg"], highlightthickness=0)
        scrollbar = tk.Scrollbar(self.root, orient="vertical", command=viewport.yview,
                                 bg=COLORS["panel"], troughcolor=COLORS["bg"], bd=0)
        viewport.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        viewport.pack(side="left", fill="both", expand=True)
        outer = tk.Frame(viewport, bg=COLORS["bg"])
        slot = viewport.create_window((0, 0), window=outer, anchor="nw")
        shell = tk.Frame(outer, bg=COLORS["bg"])
        shell.pack(fill="both", expand=True, padx=24, pady=18)
        def resize_view(_event=None):
            viewport.itemconfigure(slot, width=viewport.winfo_width())
            viewport.itemconfigure(slot, height=max(viewport.winfo_height(), outer.winfo_reqheight()))
            viewport.configure(scrollregion=viewport.bbox("all"))
        viewport.bind("<Configure>", resize_view)
        outer.bind("<Configure>", resize_view)
        self.root.bind("<MouseWheel>", lambda event: viewport.yview_scroll(-int(event.delta / 120), "units"))
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(3, weight=1)
        header = tk.Frame(shell, bg=COLORS["bg"])
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        self._label(header, "TripleTECControl", size=22, bold=True).grid(row=0, column=0, sticky="w")
        self._label(header, "THERMAL CONTROL / 3 CHANNELS", size=9, color="muted").grid(row=1, column=0, sticky="w")
        source = ttk.Combobox(header, state="readonly", textvariable=self.source,
                              values=("Live hardware", "Demo (simulated)"), width=18)
        source.grid(row=0, column=1, rowspan=2, padx=(10, 12))
        source.bind("<<ComboboxSelected>>", self._source_changed)
        self.connect_button = self._button(header, "Connect", self.connect, accent=True)
        self.connect_button.grid(row=0, column=2, rowspan=2)
        self.advanced_button = self._button(header, "Probe…", self._advanced)
        self.advanced_button.grid(row=0, column=3, rowspan=2, padx=(8, 0))

        state = tk.Frame(shell, bg=COLORS["bg"])
        state.grid(row=1, column=0, sticky="ew", pady=(14, 14))
        state.columnconfigure(1, weight=1)
        self.connection_label = self._label(state, variable=self.connection_text, size=10, color="accent", bold=True)
        self.connection_label.grid(row=0, column=0, sticky="w", padx=(0, 18))
        self._label(state, variable=self.detail_text, size=9, color="muted").grid(row=0, column=1, sticky="w")

        metrics = tk.Frame(shell, bg=COLORS["bg"])
        metrics.grid(row=2, column=0, sticky="ew")
        for index, title in enumerate(("AMBIENT", "RELATIVE HUMIDITY", "DEW POINT", "TARGET FLOOR")):
            metrics.columnconfigure(index, weight=1, uniform="metrics")
            card = self._panel(metrics)
            card.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 6, 0 if index == 3 else 6))
            self._label(card, title, size=9, color="muted").pack(anchor="w", padx=16, pady=(12, 1))
            value = self._label(card, "—", size=27, bold=True, color="accent" if index == 3 else "text")
            value.pack(anchor="w", padx=16, pady=(0, 10))
            self.metric_values.append(value)

        channels = self._panel(shell)
        channels.grid(row=3, column=0, sticky="nsew", pady=(15, 14))
        channels.columnconfigure(0, weight=2, minsize=210)
        for column in range(1, 5):
            channels.columnconfigure(column, weight=1, uniform="readings")
        self._label(channels, "CHANNEL TELEMETRY", size=10, bold=True).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 8))
        for col, label in enumerate(("Plate", "Current*", "Applied", "Requested"), 1):
            self._label(channels, label, size=9, color="muted").grid(row=0, column=col, sticky="w")
        for index in range(3):
            channels.rowconfigure(index + 1, weight=1)
            name = tk.Frame(channels, bg=COLORS["panel"])
            name.grid(row=index + 1, column=0, sticky="w", padx=16, pady=5)
            self._label(name, f"TEC 0{index + 1}", size=11, bold=True).pack(anchor="w")
            status = self._label(name, "Disconnected", size=9, color="muted", wraplength=220)
            status.pack(anchor="w")
            values = []
            for column in range(1, 5):
                cell = tk.Frame(channels, bg=COLORS["panel"])
                cell.grid(row=index + 1, column=column, sticky="w", pady=5)
                value = self._label(cell, "—", size=16, bold=True)
                value.pack(anchor="w")
                if column == 2:
                    trip = self._label(cell, "SW trip unavailable", size=9, color="muted", wraplength=145)
                    trip.pack(anchor="w")
                    self.current_trip_values.append(trip)
                values.append(value)
            self.channel_values.append((status, values))
        self._label(channels, "* Nominal current · uncalibrated; — means unavailable. Applied duty is the firmware output.",
                    size=9, color="muted").grid(row=4, column=0, columnspan=5, sticky="w", padx=16, pady=(6, 0))
        self._label(channels, "Software current trip; peak current requires hardware limiting.",
                    size=9, color="amber").grid(row=5, column=0, columnspan=5, sticky="w", padx=16, pady=(0, 10))

        bottom = tk.Frame(shell, bg=COLORS["bg"])
        bottom.grid(row=4, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=3)
        bottom.columnconfigure(1, weight=2)
        controls = self._panel(bottom)
        controls.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        controls.columnconfigure(0, weight=1)
        self._label(controls, variable=self.reported_mode, size=9, color="accent", bold=True).grid(row=0, column=0, sticky="w", padx=14, pady=(11, 6))
        options = tk.Frame(controls, bg=COLORS["panel"])
        options.grid(row=1, column=0, sticky="ew", padx=10)
        self.mode_buttons = []
        for label, value in (("Regulated", 0), ("Unregulated (100%)", 1)):
            radio = tk.Radiobutton(options, text=label, variable=self.mode, value=value,
                                   command=self._edited, bg=COLORS["panel"], fg=COLORS["text"],
                                   selectcolor=COLORS["raised"], activebackground=COLORS["panel"],
                                   activeforeground=COLORS["accent"], disabledforeground=COLORS["muted"],
                                   highlightthickness=0)
            radio.pack(side="left", padx=(0, 8))
            self.mode_buttons.append(radio)
        settings = tk.Frame(controls, bg=COLORS["panel"])
        settings.grid(row=2, column=0, sticky="ew", padx=14, pady=(7, 6))
        self._label(settings, "Dew-point margin", size=9, color="muted").pack(side="left")
        self.margin_entry = tk.Entry(settings, textvariable=self.margin, width=6,
                                     bg=COLORS["raised"], fg=COLORS["text"], insertbackground=COLORS["text"],
                                     disabledbackground=COLORS["raised"], disabledforeground=COLORS["muted"],
                                     relief="flat", justify="center", font=("Segoe UI", 11))
        self.margin_entry.pack(side="left", padx=(10, 5), ipady=5)
        self.margin_entry.bind("<KeyRelease>", self._edited)
        self._label(settings, "°C", size=10, color="muted").pack(side="left")
        self.apply_button = self._button(settings, "Apply settings", self.apply)
        self.apply_button.pack(side="right")
        actions = tk.Frame(controls, bg=COLORS["panel"])
        actions.grid(row=3, column=0, sticky="ew", padx=14)
        self.start_button = self._button(actions, "Start all channels", self.start, accent=True)
        self.start_button.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.stop_button = self._button(actions, "STOP ALL", self.stop, danger=True)
        self.stop_button.pack(side="left", fill="x", expand=True)
        self.reason_label = self._label(controls, variable=self.reason, size=9, color="amber", wraplength=510)
        self.reason_label.grid(row=4, column=0, sticky="w", padx=14, pady=(7, 5))
        self.note_label = self._label(controls, variable=self.note, size=9, color="muted", wraplength=510)
        self.note_label.grid(row=5, column=0, sticky="w", padx=14, pady=(0, 11))
        trend_panel = self._panel(bottom)
        trend_panel.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        self._label(trend_panel, "LOCAL TREND", size=10, bold=True).pack(anchor="w", padx=14, pady=(11, 0))
        self._label(trend_panel, "Ambient  /  TEC 1  /  TEC 2  /  TEC 3", size=8, color="muted").pack(anchor="w", padx=14)
        self.trend = tk.Canvas(trend_panel, bg=COLORS["panel"], height=100, highlightthickness=0)
        self.trend.pack(fill="both", expand=True, padx=10, pady=8)
        self.trend.bind("<Configure>", lambda _event: self._draw_trend())
        self.footer = self._label(shell, variable=self.message, size=9, color="muted", wraplength=1020)
        self.footer.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        shell.bind("<Configure>", self._resize)

    def _resize(self, event):
        width = max(300, int((event.width - 14) * 0.6) - 30)
        self.reason_label.configure(wraplength=width)
        self.note_label.configure(wraplength=width)
        self.footer.configure(wraplength=max(500, event.width - 10))

    def _is_demo(self):
        return self.source.get() == "Demo (simulated)"

    def _new_generation(self):
        self.generation += 1
        self.worker.invalidate(self.generation)
        self.worker.submit(self.generation, "disconnect", priority=0)
        self.poll_pending = False
        self.command_pending = False

    def _source_changed(self, _event=None):
        self._new_generation()
        self.connected = False
        self.connecting = False
        self.last_error = None
        self.snapshot = None
        self.history.clear()
        self.dirty = False
        self.demo_armed = False
        if self._is_demo():
            self.demo_started = time.monotonic()
            self._demo_update()
            self.message.set("Demo is simulated. No hardware reads or commands are sent.")
        else:
            self.message.set("Live hardware selected. Select Connect to read the Pico-linked controller.")
        self._render()

    def connect(self):
        if self._is_demo():
            return
        if self.connected or self.connecting:
            self._new_generation()
            self.connected = False
            self.connecting = False
            self.last_error = None
            self.message.set("Disconnected. The last readings are retained and marked inactive.")
        else:
            self._new_generation()
            self.connecting = True
            self.last_error = None
            self.worker.submit(self.generation, "connect", self.serial.get().strip())
            self.message.set("Opening OpenOCD and reading the device. The window remains responsive.")
        self._render()

    def _advanced(self):
        window = tk.Toplevel(self.root)
        window.title("Probe connection")
        window.configure(bg=COLORS["panel"])
        window.resizable(False, False)
        window.transient(self.root)
        self._label(window, "Pico / CMSIS-DAP serial (optional)", size=11, bold=True).pack(anchor="w", padx=20, pady=(18, 8))
        serial = tk.StringVar(value=self.serial.get())
        entry = tk.Entry(window, textvariable=serial, width=36, bg=COLORS["raised"], fg=COLORS["text"],
                         insertbackground=COLORS["text"], relief="flat")
        entry.pack(padx=20, ipady=7)
        self._label(window, "Leave blank to auto-select a probe. Changes apply on the next connection.",
                    size=9, color="muted", wraplength=350).pack(padx=20, pady=10)
        def save():
            self.serial.set(serial.get().strip())
            window.destroy()
        self._button(window, "Save", save, accent=True).pack(anchor="e", padx=20, pady=(0, 18))
        entry.focus_set()

    def _edited(self, _event=None):
        self.dirty = True
        self._render_controls()

    def _command(self, commands):
        if self._is_demo():
            for name, value in commands:
                if name == "mode":
                    self.demo_mode, self.demo_armed = value, False
                elif name == "margin":
                    self.demo_margin = value / 1000
                elif name == "arm":
                    self.demo_armed = bool(value)
                elif name == "stop":
                    self.demo_armed = False
            self.dirty = False
            self._demo_update()
            self.message.set("Demo action applied locally. No hardware was contacted.")
            return
        if not self.connected:
            return
        self.command_pending = True
        priority = 0 if any(name == "stop" for name, _ in commands) else 2
        self.worker.submit(self.generation, "command", commands, priority)
        self.message.set("Waiting for firmware acknowledgement…")
        self._render_controls()

    def apply(self):
        try:
            margin = margin_millic(self.margin.get()) if self.mode.get() == 0 else None
        except ValueError as error:
            self.message.set(str(error))
            return
        commands = []
        if self.snapshot is None or self.mode.get() != self.snapshot.mode:
            commands.append(("mode", self.mode.get()))
        if margin is not None and (self.snapshot is None or self.snapshot.margin_c is None or
                                  margin != round(self.snapshot.margin_c * 1000)):
            commands.append(("margin", margin))
        if commands:
            self._command(commands)
        else:
            self.dirty = False
            self.message.set("Settings already match the firmware-reported values.")

    def start(self):
        if self.snapshot is None:
            return
        reason = self.snapshot.start_block_reason(self.connected or self._is_demo(), time.monotonic())
        if reason or self.last_error or self.command_pending:
            return
        if self.dirty:
            self.message.set("Apply the pending settings before starting. Applying a mode never starts outputs.")
            return
        self._command([("arm", 7)])

    def stop(self):
        if not self._is_demo():
            # Discard queued arm/settings requests so none can execute after STOP.
            # Keep the existing link: the in-flight transaction finishes, then STOP.
            self.generation += 1
            self.worker.invalidate(self.generation)
            self.poll_pending = False
            self.command_pending = False
        self._command([("stop", 0)])

    def _accept_status(self, data):
        now = time.monotonic()
        self.snapshot = Snapshot.from_status(data, now)
        self.last_error = None
        self.connected = not self._is_demo()
        if not self.dirty and not self.command_pending:
            if self.snapshot.mode is not None:
                self.mode.set(self.snapshot.mode)
            if self.snapshot.margin_c is not None and self.root.focus_get() != self.margin_entry:
                self.margin.set(f"{self.snapshot.margin_c:g}")
        self.history.append((now, self.snapshot.ambient_c, *self.snapshot.plate_c))
        self._render()

    def _drain(self):
        if self.closed:
            return
        while True:
            try:
                generation, kind, payload = self.worker.events.get_nowait()
            except queue.Empty:
                break
            if generation != self.generation or self._is_demo():
                continue
            origin, result = payload
            self.poll_pending = False
            self.connecting = False
            if origin == "command":
                self.command_pending = False
            if kind == "status":
                try:
                    if origin == "command":
                        self.dirty = False
                    self._accept_status(result)
                    if origin == "command":
                        self.message.set("Firmware acknowledged the request. Reported mode and applied duty are shown above.")
                    elif origin == "connect":
                        self.message.set("Connected. Live readings come from the controller; Start is subject to firmware interlocks.")
                except ValueError as error:
                    self.last_error = str(error)
                    self.message.set(str(error))
                    if origin == "connect":
                        self.connected = False
            else:
                self.last_error = result
                if origin == "connect":
                    self.connected = False
                self.message.set(f"{result}  Check Pico USB, target power, SWD wiring and OpenOCD; then reconnect if needed.")
            self._render()
        self.root.after(100, self._drain)

    def _tick(self):
        if self.closed:
            return
        if self._is_demo():
            self._demo_update()
        elif self.connected and not self.poll_pending and not self.command_pending:
            self.poll_pending = True
            self.worker.submit(self.generation, "poll")
        self._render()
        self.root.after(1000, self._tick)

    def _demo_update(self):
        elapsed = time.monotonic() - self.demo_started
        ambient = 25.6 + 0.25 * math.sin(elapsed / 11)
        dewpoint = 17.8 + 0.15 * math.sin(elapsed / 15)
        duty = (1 if self.demo_mode else 0.34) if self.demo_armed else 0
        data = {"schema": 2, "mode": self.demo_mode, "margin_c": self.demo_margin,
                "armed_mask": 7 if self.demo_armed else 0, "outputs_locked_off": False,
                "uptime_ms": int(elapsed * 1000), "ambient_c": ambient, "rh_percent": 62.0,
                "dewpoint_c": dewpoint, "target_c": None if self.demo_mode else dewpoint + self.demo_margin,
                "plate_c": [24.1 + math.sin(elapsed / 13 + index) * 0.35 for index in range(3)],
                "current_a_nominal": [round(duty * current, 2) for current in (9, 3, 2.8)],
                "current_limits_a": [40, 9, 9], "current_limits_commissioned_mask": 7,
                "applied_duty": [duty] * 3, "desired_duty": [duty] * 3,
                "active_faults": [0] * 3, "sht31_errors": 0, "ads1220_errors": 0, "valid_flags": 63}
        self._accept_status(data)

    def _render(self):
        now = time.monotonic()
        sample = self.snapshot
        demo = self._is_demo()
        fresh = sample is not None and not sample.stale(now)
        active = (self.connected or demo) and fresh and not self.last_error
        if demo:
            self.connection_text.set("●  DEMO / SIMULATED")
            self.connection_label.configure(fg=COLORS["purple"])
            self.detail_text.set("Local demonstration · no device reads or commands")
        elif self.connecting:
            self.connection_text.set("●  CONNECTING")
            self.connection_label.configure(fg=COLORS["amber"])
            self.detail_text.set("Waiting for the first verified device response…")
        elif active:
            self.connection_text.set("●  LIVE CONNECTED")
            self.connection_label.configure(fg=COLORS["accent"])
            self.detail_text.set("Outputs locked OFF · bench image" if sample.outputs_locked_off else "Firmware interlocks active")
        else:
            self.connection_text.set("●  READ ERROR" if self.connected and self.last_error else ("●  STALE" if self.connected else "●  DISCONNECTED"))
            self.connection_label.configure(fg=COLORS["amber"] if self.connected else COLORS["muted"])
            self.detail_text.set(f"Last reading {now - sample.received_at:.0f}s ago · values inactive" if sample else "Connect through the Pico SWD probe to read the controller.")
        self.connect_button.configure(text="Disconnect" if self.connected or self.connecting else "Connect",
                                      state="disabled" if demo else "normal")
        self.advanced_button.configure(state="disabled" if self.connected or self.connecting or demo else "normal")
        values = [sample.ambient_c, sample.rh_percent, sample.dewpoint_c, sample.target_c] if sample else [None] * 4
        for index, (widget, value) in enumerate(zip(self.metric_values, values)):
            text = "Bypassed" if index == 3 and sample and sample.mode == 1 else format_value(value, " %" if index == 1 else " °C")
            widget.configure(text=text, font=("Segoe UI", 22 if text == "Bypassed" else 27, "bold"),
                             fg=COLORS["muted"] if not active else COLORS["accent" if index == 3 else "text"])
        for index, (status_label, widgets) in enumerate(self.channel_values):
            self.current_trip_values[index].configure(text=current_trip_label(sample, index),
                                                      fg=COLORS["muted"] if not active else COLORS["amber"])
            status_label.configure(text=channel_status(sample, index, self.connected or demo, now) if sample else "No device reading",
                                   fg=COLORS["muted"] if not active else COLORS["amber"] if sample.outputs_locked_off or sample.faults[index] else COLORS["accent"])
            numbers = (sample.plate_c[index], sample.current_a[index], sample.applied_duty[index], sample.desired_duty[index]) if sample else (None,) * 4
            for column, (widget, number) in enumerate(zip(widgets, numbers)):
                text = format_value(number * 100 if number is not None and column >= 2 else number,
                                    " °C" if column == 0 else " A" if column == 1 else " %", 2 if column == 1 else 1)
                widget.configure(text=text, fg=COLORS["text"] if active else COLORS["muted"])
        self._render_controls()
        self._draw_trend()

    def _render_controls(self):
        sample = self.snapshot
        now = time.monotonic()
        demo = self._is_demo()
        available = bool(sample and (self.connected or demo) and not sample.stale(now) and not self.last_error and sample.schema >= 2)
        editable = available and not self.command_pending
        self.apply_button.configure(state="normal" if editable else "disabled")
        for button in self.mode_buttons:
            button.configure(state="normal" if editable else "disabled")
        self.margin_entry.configure(state="normal" if editable and self.mode.get() == 0 else "disabled")
        if sample:
            prefix = "DEMO MODE" if demo else "DEVICE MODE"
            self.reported_mode.set(f"{prefix}  {mode_label(sample.mode)}" + ("   · pending edit" if self.dirty else ""))
        else:
            self.reported_mode.set("DEVICE MODE  —")
        reason = sample.start_block_reason(self.connected or demo, now) if sample else "Connect to a device before starting."
        if self.last_error:
            reason = "Communication failed. Restore fresh telemetry before starting."
        elif self.command_pending:
            reason = "Waiting for firmware acknowledgement."
        elif self.dirty and reason is None:
            reason = "Apply pending settings before starting. Mode changes leave all channels stopped."
        self.reason.set(reason or ("Demo controls affect simulated values only." if demo else "Ready. Start requests all three channels; firmware checks every interlock."))
        self.start_button.configure(state="normal" if available and not reason else "disabled")
        self.stop_button.configure(state="normal" if (self.connected or demo) and sample and sample.schema >= 2 else "disabled")
        unregulated = self.mode.get() == 1 or (sample is not None and sample.mode == 1)
        self.note.set("Bypasses dew-point protection; condensation may occur. Sensor and current trips remain active. Margin is ignored." if unregulated else "Regulated target = max(0 °C, dew point + margin). Applying settings never starts outputs.")
        self.note_label.configure(fg=COLORS["amber"] if unregulated else COLORS["muted"])

    def _draw_trend(self):
        canvas = self.trend
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 30 or height < 30:
            return
        series = list(self.history)
        values = [value for row in series for value in row[1:] if value is not None]
        if len(series) < 2 or not values:
            canvas.create_text(width / 2, height / 2, text="Connect to record local temperature trends" if not self._is_demo() else "Simulated trend starting…",
                               fill=COLORS["muted"], font=("Segoe UI", 9), width=width - 20)
            return
        minimum, maximum = min(values) - 0.5, max(values) + 0.5
        colors = (COLORS["muted"], COLORS["accent"], COLORS["blue"], COLORS["purple"])
        left, top, right, bottom = 35, 10, width - 10, height - 22
        for fraction in (0, 0.5, 1):
            y = bottom - (bottom - top) * fraction
            canvas.create_line(left, y, right, y, fill=COLORS["line"])
            canvas.create_text(left - 6, y, anchor="e", text=f"{minimum + (maximum - minimum) * fraction:.0f}°", fill=COLORS["muted"], font=("Segoe UI", 8))
        duration = max(1, series[-1][0] - series[0][0])
        dim = not self._is_demo() and (not self.connected or self.last_error or self.snapshot.stale(time.monotonic()))
        for column, color in enumerate(colors, 1):
            points = []
            for row in series:
                if row[column] is None:
                    if len(points) >= 4:
                        canvas.create_line(*points, fill=COLORS["line"] if dim else color, width=2)
                    points = []
                    continue
                points.extend((left + (right - left) * (row[0] - series[0][0]) / duration,
                               bottom - (bottom - top) * (row[column] - minimum) / (maximum - minimum)))
            if len(points) >= 4:
                canvas.create_line(*points, fill=COLORS["line"] if dim else color, width=2)
        canvas.create_text(left, height - 5, anchor="w", text=f"Last {duration:.0f}s · {'simulated' if self._is_demo() else 'this session'}", fill=COLORS["muted"], font=("Segoe UI", 8))

    def close(self):
        self.closed = True
        self._new_generation()
        self.root.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description="TripleTECDriver desktop dashboard")
    parser.add_argument("--demo", action="store_true", help="Show simulated data; never contact hardware")
    parser.add_argument("--connect", action="store_true", help="Connect to live hardware on launch")
    parser.add_argument("--serial", help="Optional CMSIS-DAP probe serial")
    parser.add_argument("--smoke-test", action="store_true", help="Render an offline demo and close automatically")
    args = parser.parse_args(argv)
    root = tk.Tk()
    app = Dashboard(root, demo=args.demo or args.smoke_test, connect=args.connect and not args.smoke_test, serial=args.serial)
    if args.smoke_test:
        root.after(1500, app.close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
