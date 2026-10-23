import json
import os
import queue
import re
import threading
from tkinter import filedialog, messagebox

import customtkinter as ctk

from bot import MODULES, BotRunner
from builder import blueprint_from_screenshots, generate_blueprint

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

BG = "#0f0c1a"
SIDEBAR = "#15112a"
CARD = "#1c1736"
CARD_ALT = "#241d45"
ACCENT = "#7c3aed"
ACCENT_H = "#6d28d9"
MUTED = "#9a93b8"
GREEN = "#22c55e"
RED = "#ef4444"
AMBER = "#f59e0b"
TEXT = "#f1eeff"

PAGES = [
    ("connect", "🔌", "Connection"),
    ("blueprint", "🧩", "Blueprint"),
    ("bots", "🤖", "Bots & Modules"),
    ("activity", "📜", "Activity log"),
]


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Discord AI Server Builder")
        self.geometry("1140x780")
        self.minsize(980, 680)
        self.configure(fg_color=BG)
        self.q = queue.Queue()
        self.runner = BotRunner(lambda m: self.q.put(("log", m)))
        self.own_vars = {}
        self.nav = {}
        self.pages = {}

        self.grid_columnconfigure(1, weight=1)
        # Main page occupies row 1; the status bar belongs in row 2.
        # Keeping row 0 collapsed prevents the large blank area and overlap.
        self.grid_rowconfigure(0, weight=0, minsize=0)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)
        self._build_sidebar()
        self.content = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self.content.grid(row=1, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        self._build_statusbar()

        self._page_connect()
        self._page_blueprint()
        self._page_bots()
        self._page_activity()
        self.show("connect")
        self.after(200, self._drain)

    # ------------------------------------------------------------ layout
    def _build_sidebar(self):
        side = ctk.CTkFrame(self, width=220, fg_color=SIDEBAR, corner_radius=0)
        side.grid(row=1, column=0, rowspan=2, sticky="nsw")
        side.grid_propagate(False)
        ctk.CTkLabel(side, text="⚡ AI Builder", font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=TEXT).pack(anchor="w", padx=22, pady=(26, 0))
        ctk.CTkLabel(side, text="Discord server studio", text_color=MUTED,
                     font=ctk.CTkFont(size=12)).pack(anchor="w", padx=22, pady=(0, 24))
        for key, icon, label in PAGES:
            b = ctk.CTkButton(side, text=f"  {icon}   {label}", anchor="w", height=42,
                              corner_radius=10, fg_color="transparent", hover_color=CARD_ALT,
                              text_color=MUTED, font=ctk.CTkFont(size=14, weight="bold"),
                              command=lambda k=key: self.show(k))
            b.pack(fill="x", padx=14, pady=3)
            self.nav[key] = b
        self.pill = ctk.CTkLabel(side, text="●  Bot offline", text_color=RED,
                                 font=ctk.CTkFont(size=12, weight="bold"))
        self.pill.pack(side="bottom", anchor="w", padx=22, pady=22)

    def _build_statusbar(self):
        bar = ctk.CTkFrame(self, height=34, fg_color=SIDEBAR, corner_radius=0)
        bar.grid(row=2, column=1, sticky="ew")
        self.status = ctk.CTkLabel(bar, text="Ready", text_color=MUTED, font=ctk.CTkFont(size=12))
        self.status.pack(side="left", padx=16)
        self.progress = ctk.CTkProgressBar(bar, width=180, height=8, progress_color=ACCENT,
                                           mode="indeterminate")
        self.progress.set(0)  # shown only while busy (see start_progress)

    def show(self, key):
        for k, page in self.pages.items():
            page.grid_remove()
            self.nav[k].configure(fg_color="transparent", text_color=MUTED)
        self.pages[key].grid(row=0, column=0, sticky="nsew")
        self.nav[key].configure(fg_color=ACCENT, text_color="white")

    def _new_page(self, key, title, subtitle):
        page = ctk.CTkFrame(self.content, fg_color=BG, corner_radius=0)
        ctk.CTkLabel(page, text=title, font=ctk.CTkFont(size=26, weight="bold"),
                     text_color=TEXT).pack(anchor="w", padx=30, pady=(26, 0))
        ctk.CTkLabel(page, text=subtitle, text_color=MUTED, font=ctk.CTkFont(size=13),
                     wraplength=760, justify="left").pack(anchor="w", padx=30, pady=(2, 14))
        self.pages[key] = page
        return page

    def _card(self, parent, **pack):
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=14)
        card.pack(fill=pack.pop("fill", "x"), padx=30, pady=pack.pop("pady", 8), **pack)
        return card

    def _btn(self, parent, text, cmd, color=ACCENT, hover=ACCENT_H, width=170):
        return ctk.CTkButton(parent, text=text, command=cmd, fg_color=color, hover_color=hover,
                             height=38, corner_radius=10, width=width,
                             font=ctk.CTkFont(size=13, weight="bold"))

    def _style_switch(self, switch, active_color=GREEN):
        """Keep switch colors in sync with its ON/OFF state."""
        original_command = getattr(switch, "_command", None)
        def refresh():
            active = bool(switch.get())
            switch.configure(
                progress_color=active_color if active else "#4b5563",
                button_color=active_color if active else "#7b8494",
                button_hover_color=("#16a34a" if active_color == GREEN else active_color) if active else "#9ca3af",
                text_color=TEXT if active else MUTED,
            )
        def on_toggle():
            refresh()
            if original_command:
                original_command()
        switch.configure(command=on_toggle)
        refresh()
        return refresh

    def _entry_row(self, parent, row, label, secret=False, default="", hint=""):
        ctk.CTkLabel(parent, text=label, text_color=TEXT, font=ctk.CTkFont(size=13)
                     ).grid(row=row, column=0, sticky="w", padx=20, pady=(12, 0))
        entry = ctk.CTkEntry(parent, show="•" if secret else "", height=38, corner_radius=10,
                             fg_color=BG, border_color=CARD_ALT, placeholder_text=hint)
        entry.grid(row=row, column=1, sticky="ew", padx=20, pady=(12, 0))
        if default:
            entry.insert(0, default)
        return entry

    # ------------------------------------------------------------ pages
    def _page_connect(self):
        page = self._new_page("connect", "Connection",
                              "Enter your keys once, connect the bot, and the app scans the server "
                              "for other bots (ProBot, Dyno, ...). Keys are never saved to disk.")
        card = self._card(page, pady=10)
        card.configure(border_width=1, border_color=CARD_ALT)
        card.grid_columnconfigure(1, weight=1)
        connection_head = ctk.CTkFrame(card, fg_color="transparent")
        connection_head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=20, pady=(14, 0))
        ctk.CTkLabel(connection_head, text="🔐  Bot connection", text_color=TEXT,
                     font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")
        self.connection_state = ctk.CTkLabel(connection_head, text="● Not connected",
                                             text_color=RED, font=ctk.CTkFont(size=11, weight="bold"))
        self.connection_state.pack(side="right")
        ctk.CTkLabel(card, text="AI provider", text_color=TEXT, font=ctk.CTkFont(size=13)
                     ).grid(row=1, column=0, sticky="w", padx=20, pady=(16, 0))
        self.provider = ctk.CTkOptionMenu(card, values=["Gemini (free)", "Claude (paid)"],
                                          fg_color=BG, button_color=ACCENT,
                                          button_hover_color=ACCENT_H, height=38)
        self.provider.grid(row=1, column=1, sticky="w", padx=20, pady=(16, 0))
        self.api_key = self._entry_row(card, 2, "AI API key", True,
                                       os.environ.get("GEMINI_API_KEY", ""), "Gemini or Claude key")
        self.token = self._entry_row(card, 3, "Discord bot token", True, "", "From the Developer Portal")
        ctk.CTkLabel(card, text="Server", text_color=TEXT, font=ctk.CTkFont(size=13)
                     ).grid(row=4, column=0, sticky="w", padx=20, pady=(12, 0))
        self.guild_menu = ctk.CTkOptionMenu(
            card, values=["Press 'Connect' to load your servers"], command=self.pick_guild,
            fg_color=BG, button_color=ACCENT, button_hover_color=ACCENT_H, height=38, dynamic_resizing=False)
        self.guild_menu.grid(row=4, column=1, sticky="ew", padx=20, pady=(12, 0))
        self.guild_id = self._entry_row(card, 5, "Server ID (filled for you)", False, "",
                                        "Or paste an ID by hand")
        self.img_key = self._entry_row(card, 6, "OpenAI key (optional, images)", True)
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=7, column=0, columnspan=2, sticky="w", padx=20, pady=18)
        self.btn_connect = self._btn(row, "🔌  Connect bot", self.on_connect, GREEN, "#16a34a", 180)
        self.btn_connect.pack(side="left", padx=(0, 10))
        self.btn_backup = self._btn(row, "💾  Backup now", self.on_backup, CARD_ALT, "#3b3560", 150)
        self.btn_backup.pack(side="left")

        tips = self._card(page)
        ctk.CTkLabel(tips, text="Before you start", text_color=TEXT,
                     font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=20, pady=(14, 2))
        ctk.CTkLabel(
            tips, justify="left", text_color=MUTED, wraplength=720,
            text=("1. In the Developer Portal enable Server Members Intent and Message Content Intent.\n"
                  "2. Invite the bot with the Administrator permission.\n"
                  "3. In Server Settings → Roles drag the bot's role ABOVE the roles it must manage.\n"
                  "4. Never share the token. If it leaks, press Reset Token in the portal.")
        ).pack(anchor="w", padx=20, pady=(0, 14))

    def _page_blueprint(self):
        page = self._new_page("blueprint", "Blueprint",
                              "Describe the server or load a JSON file, review it, then build it on Discord.")
        card = self._card(page)
        self.desc = ctk.CTkTextbox(card, height=84, fg_color=BG, corner_radius=10,
                                   border_width=1, border_color=CARD_ALT)
        self.desc.pack(fill="x", padx=18, pady=(16, 8))
        opts = ctk.CTkFrame(card, fg_color="transparent")
        opts.pack(fill="x", padx=18)
        self.chk_visuals = ctk.CTkSwitch(opts, text="Generate icon, banner and emojis",
                                         progress_color=ACCENT, text_color=MUTED)
        self.chk_visuals.pack(side="left", padx=(0, 22))
        self._style_switch(self.chk_visuals, ACCENT)
        opts2 = ctk.CTkFrame(card, fg_color="transparent")
        opts2.pack(fill="x", padx=18, pady=(8, 0))
        self.chk_wipe = ctk.CTkSwitch(opts2, text="🗑️ Delete everything (channels + roles)",
                                      progress_color=RED, text_color=MUTED, command=self.update_mode)
        self.chk_wipe.pack(side="left", padx=(0, 22))
        self._style_switch(self.chk_wipe, RED)
        self.chk_replace = ctk.CTkSwitch(opts2, text="🔁 Replace: then build this blueprint",
                                         progress_color=AMBER, text_color=MUTED, state="disabled",
                                         command=self.update_mode)
        self.chk_replace.pack(side="left", padx=(0, 22))
        self._style_switch(self.chk_replace, AMBER)
        self.chk_keep = ctk.CTkSwitch(opts2, text="🧍 Keep existing roles unchanged",
                                      progress_color="#0ea5e9", text_color=MUTED)
        self.chk_keep.select()
        self.chk_keep.pack(side="left")
        self._style_switch(self.chk_keep, "#0ea5e9")
        self.chk_keepcat = ctk.CTkSwitch(opts2, text="🗂️ Keep existing categories/channels",
                                         progress_color="#0ea5e9", text_color=MUTED)
        self.chk_keepcat.select()
        self.chk_keepcat.pack(side="left", padx=(22, 0))
        self._style_switch(self.chk_keepcat, "#0ea5e9")
        opts3 = ctk.CTkFrame(card, fg_color="transparent")
        opts3.pack(fill="x", padx=18, pady=(8, 0))
        self.chk_keepperms = ctk.CTkSwitch(opts3, text="🔐 Keep channel/category permissions unchanged",
                                           progress_color="#0ea5e9", text_color=MUTED)
        self.chk_keepperms.select()
        self.chk_keepperms.pack(side="left", padx=(0, 22))
        self._style_switch(self.chk_keepperms, "#0ea5e9")
        opts4 = ctk.CTkFrame(card, fg_color="transparent")
        opts4.pack(fill="x", padx=18, pady=(8, 0))
        self.chk_addroles = ctk.CTkSwitch(opts4, text="➕ Add missing roles from JSON",
                                          progress_color=GREEN, text_color=MUTED)
        self.chk_addroles.pack(side="left", padx=(0, 22))
        self._style_switch(self.chk_addroles, GREEN)
        self.chk_addchannels = ctk.CTkSwitch(opts4, text="➕ Add missing categories/channels from JSON",
                                             progress_color=GREEN, text_color=MUTED)
        self.chk_addchannels.pack(side="left")
        self._style_switch(self.chk_addchannels, GREEN)
        bar = ctk.CTkFrame(card, fg_color="transparent")
        bar.pack(fill="x", padx=18, pady=(14, 6))
        self.btn_gen = self._btn(bar, "✨ Generate with AI", self.on_generate, width=170)
        self.btn_gen.pack(side="left", padx=(0, 8))
        self.btn_shots = self._btn(bar, "🖼️ From screenshots", self.on_screenshots, "#0ea5e9", "#0284c7", 180)
        self.btn_shots.pack(side="left", padx=(0, 8))
        self.btn_read = self._btn(bar, "📡 Read my server", self.on_read_server, "#0ea5e9", "#0284c7", 170)
        self.btn_read.pack(side="left")
        bar2 = ctk.CTkFrame(card, fg_color="transparent")
        bar2.pack(fill="x", padx=18, pady=(0, 14))
        self._btn(bar2, "📂 Import JSON", self.on_import, "#3b3560", "#4b4378", 140).pack(side="left", padx=(0, 8))
        self._btn(bar2, "💾 Export JSON", self.on_export, "#3b3560", "#4b4378", 140).pack(side="left")
        self.btn_create = self._btn(bar2, "🚀 Create on Discord", self.on_create, GREEN, "#16a34a", 190)
        self.btn_create.pack(side="right")
        box = self._card(page, fill="both", expand=True, pady=(4, 18))
        ctk.CTkLabel(box, text="Preview (editable JSON)", text_color=MUTED,
                     font=ctk.CTkFont(size=12)).pack(anchor="w", padx=18, pady=(12, 0))
        self.preview = ctk.CTkTextbox(box, font=("Consolas", 12), fg_color=BG, corner_radius=10)
        self.preview.pack(fill="both", expand=True, padx=14, pady=(4, 14))

    def _page_bots(self):
        page = self._new_page(
            "bots", "Bots & Modules",
            "A clear dashboard for the modules this app currently supports. Green means ON; gray means OFF.")

        # Search field, like a module dashboard, so users can find options quickly.
        search = ctk.CTkFrame(page, fg_color="transparent")
        search.pack(fill="x", padx=30, pady=(0, 10))
        self.module_search = ctk.CTkEntry(
            search, placeholder_text="🔎  Search modules...", height=40, corner_radius=10,
            fg_color=CARD, border_color=CARD_ALT, text_color=TEXT)
        self.module_search.pack(fill="x")
        self.module_search.bind("<KeyRelease>", lambda _event: self._render_module_cards())

        # Detected bots stay visible above the module grid.
        top = self._card(page, pady=6)
        head = ctk.CTkFrame(top, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(10, 2))
        ctk.CTkLabel(head, text="🤖  Detected bots", text_color=TEXT,
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        self._btn(head, "Scan again", self.on_scan, "#3b3560", "#4b4378", 120).pack(side="right")
        self.bots_box = ctk.CTkScrollableFrame(top, height=95, fg_color="transparent")
        self.bots_box.pack(fill="x", padx=8, pady=(0, 8))
        self.render_bots(None)

        mods = self._card(page, fill="both", expand=True, pady=6)
        header = ctk.CTkFrame(mods, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(12, 2))
        ctk.CTkLabel(header, text="Modules", text_color=TEXT,
                     font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")
        ctk.CTkLabel(header, text="● ON  /  ○ OFF", text_color=MUTED,
                     font=ctk.CTkFont(size=11, weight="bold")).pack(side="right")
        ctk.CTkLabel(mods, text="Turn ON only the modules you want this bot to handle.",
                     text_color=MUTED, font=ctk.CTkFont(size=12)).pack(anchor="w", padx=18, pady=(0, 8))

        self.module_grid = ctk.CTkScrollableFrame(mods, fg_color="transparent")
        self.module_grid.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.module_meta = {
            "logs": ("📋", "Server activity logs", "Joins, leaves, kicks, bans, edits and voice events."),
            "antispam": ("🛡️", "Anti-spam", "Spam and invite protection."),
            "levels": ("🏆", "Levels", "XP and member leveling."),
            "welcome": ("👋", "Welcome", "Welcome messages for new members."),
        }
        for key in MODULES:
            self.own_vars[key] = ctk.BooleanVar(value=False)
        self._render_module_cards()

        note = ctk.CTkLabel(
            mods, text_color=AMBER, wraplength=760, justify="left", font=ctk.CTkFont(size=11),
            text=('Tip: another bot can handle a module through the JSON "bots" setting. '
                  'If that bot is not configured, turn this app’s switch ON instead.')
        )
        note.pack(anchor="w", padx=18, pady=(0, 12))

    def _render_module_cards(self):
        if not hasattr(self, "module_grid"):
            return
        for child in self.module_grid.winfo_children():
            child.destroy()
        query = self.module_search.get().strip().lower() if hasattr(self, "module_search") else ""
        cards = []
        for key, label in MODULES.items():
            icon, title, description = self.module_meta.get(key, ("⚙️", label, label))
            if query and query not in (key + " " + label + " " + title + " " + description).lower():
                continue
            cards.append((key, label, icon, title, description))
        columns = 3
        for col in range(columns):
            self.module_grid.grid_columnconfigure(col, weight=1, uniform="module")
        if not cards:
            ctk.CTkLabel(self.module_grid, text="No modules match your search.",
                         text_color=MUTED).grid(row=0, column=0, sticky="w", padx=12, pady=12)
            return
        for index, (key, label, icon, title, description) in enumerate(cards):
            row, col = divmod(index, columns)
            card = ctk.CTkFrame(self.module_grid, fg_color=CARD_ALT, corner_radius=12,
                                border_width=1, border_color="#30284f")
            card.grid(row=row, column=col, sticky="nsew", padx=6, pady=6)
            card.grid_columnconfigure(0, weight=1)
            top = ctk.CTkFrame(card, fg_color="transparent")
            top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 2))
            ctk.CTkLabel(top, text=f"{icon}  {title}", text_color=TEXT,
                         font=ctk.CTkFont(size=14, weight="bold")).pack(side="left", anchor="w")
            var = self.own_vars[key]
            state = ctk.CTkLabel(top, text="● ON" if var.get() else "○ OFF",
                                 text_color=GREEN if var.get() else MUTED,
                                 font=ctk.CTkFont(size=10, weight="bold"))
            state.pack(side="right")
            ctk.CTkLabel(card, text=description, text_color=MUTED, justify="left",
                         wraplength=310, height=42, anchor="nw",
                         font=ctk.CTkFont(size=11)).grid(row=1, column=0, sticky="ew", padx=12, pady=(4, 4))
            def update_state(k=key, label_widget=state):
                active = bool(self.own_vars[k].get())
                label_widget.configure(text="● ON" if active else "○ OFF",
                                       text_color=GREEN if active else MUTED)
                if "switch_ref" in holder:
                    holder["switch_ref"].configure(
                        progress_color=GREEN if active else "#4b5563",
                        button_color=GREEN if active else "#7b8494",
                        button_hover_color="#16a34a" if active else "#9ca3af",
                        text_color=TEXT if active else MUTED,
                    )
            holder = {}
            switch = ctk.CTkSwitch(card, text="Enable module", variable=var,
                                   progress_color=GREEN, button_color=GREEN,
                                   button_hover_color="#16a34a", text_color=TEXT,
                                   command=update_state)
            holder["switch_ref"] = switch
            switch.grid(row=2, column=0, sticky="w", padx=12, pady=(4, 12))
            update_state()

    def _page_activity(self):
        page = self._new_page("activity", "Activity log", "Everything the app and the bot did.")
        box = self._card(page, fill="both", expand=True, pady=(4, 18))
        self.logbox = ctk.CTkTextbox(box, state="disabled", fg_color=BG, corner_radius=10,
                                     font=("Consolas", 12))
        self.logbox.pack(fill="both", expand=True, padx=14, pady=(14, 8))
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(anchor="e", padx=14, pady=(0, 12))
        self._btn(row, "Clear", self.clear_log, "#3b3560", "#4b4378", 100).pack(side="left", padx=6)
        self._btn(row, "Save log", self.save_log, ACCENT, ACCENT_H, 110).pack(side="left")

    # ------------------------------------------------------------ helpers
    def log(self, msg):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", msg + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")
        self.status.configure(text=msg[:110])

    def clear_log(self):
        self.logbox.configure(state="normal")
        self.logbox.delete("1.0", "end")
        self.logbox.configure(state="disabled")

    def save_log(self):
        path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text", "*.txt")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.logbox.get("1.0", "end"))

    def start_progress(self):
        self.progress.pack(side="right", padx=16)
        self.progress.start()

    def stop_progress(self):
        self.progress.stop()
        self.progress.pack_forget()

    def set_busy(self, busy):
        state = "disabled" if busy else "normal"
        for b in (self.btn_create, self.btn_backup, self.btn_connect, self.btn_read):
            b.configure(state=state)
        if busy:
            self.start_progress()
        else:
            self.stop_progress()

    def render_bots(self, found):
        for w in self.bots_box.winfo_children():
            w.destroy()
        if found is None:
            text = "Press 'Connect' and choose a server on the Connection page."
        elif not found:
            text = "No other bots found in this server."
        else:
            text = None
        if text:
            ctk.CTkLabel(self.bots_box, text=text, text_color=MUTED).pack(anchor="w", padx=10, pady=8)
            return
        for info in found:
            row = ctk.CTkFrame(self.bots_box, fg_color=CARD_ALT, corner_radius=10)
            row.pack(fill="x", padx=6, pady=4)
            ctk.CTkLabel(row, text=f"🤖  {info['name']}", text_color=TEXT,
                         font=ctk.CTkFont(size=14, weight="bold")).pack(side="left", padx=14, pady=10)
            covers = ", ".join(info["covers"]) if info["covers"] else "unknown bot, I will not hand it anything"
            ctk.CTkLabel(row, text=f"usually covers: {covers}", text_color=MUTED,
                         font=ctk.CTkFont(size=12)).pack(side="right", padx=14)

    def _drain(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.log(val)
                elif kind == "preview":
                    self.preview.delete("1.0", "end")
                    self.preview.insert("1.0", val)
                elif kind == "gen_done":
                    self.btn_gen.configure(state="normal")
                    self.btn_shots.configure(state="normal")
                    self.stop_progress()
                elif kind == "busy":
                    self.set_busy(val)
                elif kind == "guilds":
                    self.set_guilds(val)
                elif kind == "bots":
                    self.render_bots(val)
                elif kind == "online":
                    self.pill.configure(text="●  Bot online" if val else "●  Bot offline",
                                        text_color=GREEN if val else RED)
                    if hasattr(self, "connection_state"):
                        self.connection_state.configure(
                            text="● Connected" if val else "● Not connected",
                            text_color=GREEN if val else RED)
        except queue.Empty:
            pass
        self.after(200, self._drain)

    # ------------------------------------------------------------ actions
    def _connect(self):
        token = self.token.get().strip()
        gid = self.guild_id.get().strip()
        if not token or not gid.isdigit():
            self.log("Enter the bot token and choose a server on the Connection page (press Connect first).")
            self.show("connect")
            return None
        self.runner.start(token, self.api_key.get().strip() or None)
        return gid

    def _watch(self, fut, ok_msg, on_result=None):
        def done(f):
            if f.cancelled():
                self.q.put(("log", "Bot failed to connect (see messages above)."))
                self.q.put(("online", False))
            elif f.exception():
                self.q.put(("log", f"Error: {f.exception()}"))
            else:
                self.q.put(("log", ok_msg))
                self.q.put(("online", True))
                if on_result:
                    on_result(f.result())
            self.q.put(("busy", False))

        fut.add_done_callback(done)

    def on_connect(self):
        token = self.token.get().strip()
        if not token:
            self.log("Enter the bot token first.")
            return
        self.runner.start(token, self.api_key.get().strip() or None)
        gid = self.guild_id.get().strip()
        self.q.put(("busy", True))
        self.log("Connecting the bot...")
        if hasattr(self, "connection_state"):
            self.connection_state.configure(text="● Connecting...", text_color=AMBER)
        fut = self.runner.submit(self.runner.bot.connect_info(gid if gid.isdigit() else None))

        def got(info):
            self.q.put(("guilds", info["guilds"]))
            if info["bots"] is not None:
                self.q.put(("bots", info["bots"]))

        self._watch(fut, "Connected.", got)

    def pick_guild(self, choice):
        m_ = re.search(r"\((\d+)\)$", choice or "")
        if not m_:
            return
        self.guild_id.delete(0, "end")
        self.guild_id.insert(0, m_.group(1))
        bot = self.runner.bot
        if bot is not None and self.runner.thread and self.runner.thread.is_alive() and bot.is_ready():
            self.on_connect()  # rescan the other bots for the newly chosen server

    def set_guilds(self, guilds):
        if not guilds:
            self.guild_menu.configure(values=["The bot is not in any server yet"])
            self.guild_menu.set("The bot is not in any server yet")
            return
        labels = [f"{g['name']}  ({g['id']})" for g in guilds]
        self.guild_menu.configure(values=labels)
        current = self.guild_id.get().strip()
        chosen = next((l for l, g in zip(labels, guilds) if str(g["id"]) == current), None)
        if chosen is None and len(labels) == 1:
            chosen = labels[0]
            self.guild_id.delete(0, "end")
            self.guild_id.insert(0, str(guilds[0]["id"]))
        self.guild_menu.set(chosen or "Choose a server...")

    def on_scan(self):
        self.on_connect()

    def on_backup(self):
        gid = self._connect()
        if gid is None:
            return
        self.q.put(("busy", True))
        self._watch(self.runner.submit(self.runner.bot.backup_only(gid)), "Backup finished.")

    def on_generate(self):
        desc = self.desc.get("1.0", "end").strip()
        key = self.api_key.get().strip()
        if not desc or not key:
            self.log("Enter your AI API key (Connection page) and a description.")
            return
        provider = "gemini" if self.provider.get().startswith("Gemini") else "anthropic"
        self.btn_gen.configure(state="disabled")
        self.start_progress()
        self.log("Generating...")

        def work():
            try:
                bp = generate_blueprint(key, desc, provider)
                self.q.put(("preview", json.dumps(bp, indent=2, ensure_ascii=False)))
                self.q.put(("log", "Preview ready. Edit it if you like, then press Create."))
            except Exception as e:
                self.q.put(("log", f"Error: {e}"))
            finally:
                self.q.put(("gen_done", None))

        threading.Thread(target=work, daemon=True).start()

    def on_read_server(self):
        gid = self._connect()
        if gid is None:
            return
        self.q.put(("busy", True))
        self.log("Reading the server structure...")
        fut = self.runner.submit(self.runner.bot.export_server(gid))
        self._watch(
            fut, "Server read. Edit the JSON if you like, then Export it or Create it on another server.",
            lambda bp: self.q.put(("preview", json.dumps(bp, indent=2, ensure_ascii=False))))

    def on_screenshots(self):
        key = self.api_key.get().strip()
        if not key:
            self.log("Enter your AI API key on the Connection page first.")
            self.show("connect")
            return
        paths = filedialog.askopenfilenames(
            title="Choose screenshots of the server (max 6)",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp")])
        if not paths:
            return
        note = self.desc.get("1.0", "end").strip()
        provider = "gemini" if self.provider.get().startswith("Gemini") else "anthropic"
        self.btn_gen.configure(state="disabled")
        self.btn_shots.configure(state="disabled")
        self.start_progress()
        self.log(f"Reading {min(len(paths), 6)} screenshot(s) with the AI...")

        def work():
            try:
                bp = blueprint_from_screenshots(key, list(paths), provider, note)
                self.q.put(("preview", json.dumps(bp, indent=2, ensure_ascii=False)))
                self.q.put(("log", "Done. Check names and permissions in the preview (permissions are guessed)."))
            except Exception as e:
                self.q.put(("log", f"Error: {e}"))
            finally:
                self.q.put(("gen_done", None))

        threading.Thread(target=work, daemon=True).start()

    def on_import(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self.log(f"Could not load the file: {e}")
            return
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", json.dumps(data, indent=2, ensure_ascii=False))
        self.log(f"Loaded {os.path.basename(path)}.")

    def on_export(self):
        try:
            data = json.loads(self.preview.get("1.0", "end"))
        except json.JSONDecodeError as e:
            self.log(f"Preview is not valid JSON: {e}")
            return
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self.log(f"Saved {os.path.basename(path)}.")

    def update_mode(self):
        """Delete = wipe only. Delete + Replace = wipe, then build. Neither = just add.
        Replace + Keep roles = wipe CHANNELS only, existing roles are reused."""
        wipe = bool(self.chk_wipe.get())
        if wipe:
            self.chk_replace.configure(state="normal")
        else:
            self.chk_replace.deselect()
            self.chk_replace.configure(state="disabled")
        replace = bool(self.chk_replace.get())
        # Keep/Add controls are always available and independent of wipe/replace.
        keep = bool(self.chk_keep.get())
        keepcat = bool(self.chk_keepcat.get())
        if wipe and not replace:
            self.btn_create.configure(text="🗑️ Delete everything", fg_color=RED, hover_color="#dc2626")
        elif wipe and replace and keep and keepcat:
            self.btn_create.configure(text="🛠️ Update only (keep roles & channels)",
                                      fg_color="#0ea5e9", hover_color="#0284c7")
        elif wipe and replace and keepcat:
            self.btn_create.configure(text="🔁 Replace roles (keep channels)",
                                      fg_color="#0ea5e9", hover_color="#0284c7")
        elif wipe and replace and keep:
            self.btn_create.configure(text="🔁 Replace channels (keep roles)",
                                      fg_color="#0ea5e9", hover_color="#0284c7")
        elif wipe and replace:
            self.btn_create.configure(text="🔁 Delete & Replace", fg_color=AMBER, hover_color="#d97706")
        else:
            self.btn_create.configure(text="🚀 Create on Discord", fg_color=GREEN, hover_color="#16a34a")

    def on_create(self):
        wipe = bool(self.chk_wipe.get())
        build = (not wipe) or bool(self.chk_replace.get())
        bp = {}
        if build:
            try:
                bp = json.loads(self.preview.get("1.0", "end"))
            except json.JSONDecodeError as e:
                self.log(f"Preview is not valid JSON: {e}")
                return
        keep_roles = bool(self.chk_keep.get())
        keepcat = bool(self.chk_keepcat.get())
        if wipe:
            if not build:
                what = "This deletes ALL channels AND roles. NOTHING will be built afterwards"
            elif keep_roles and keepcat:
                what = ("NOTHING is deleted: existing channels, categories and roles stay "
                        "untouched; only missing ones are created and panels are re-sent")
            elif keepcat:
                what = ("Channels and categories are KEPT untouched (permissions included), "
                        "but ALL ROLES are deleted and rebuilt from the blueprint")
            elif keep_roles:
                what = ("This deletes ALL CHANNELS but KEEPS the existing roles "
                        "(reused by name, nothing deleted, not even their permissions)")
            else:
                what = "This deletes ALL channels AND roles and then builds the blueprint"
            if not messagebox.askyesno(
                    "Delete everything",
                    what + " (a backup is saved first). Roles above the bot's role, bot roles and "
                    "protected channels (such as Community rules/updates) cannot be deleted. Continue?"):
                return
        gid = self._connect()
        if gid is None:
            return
        own = {k for k, v in self.own_vars.items() if v.get()}
        self.q.put(("busy", True))
        self.log("Connecting the bot and " + ("building..." if build else "deleting..."))
        fut = self.runner.submit(self.runner.bot.apply_blueprint(
            gid, bp, wipe=wipe, make_visuals=bool(self.chk_visuals.get()) and build,
            openai_key=self.img_key.get().strip() or None, own_modules=own, build=build,
            keep_roles=keep_roles, keep_channels=keepcat,
            keep_permissions=bool(self.chk_keepperms.get()),
            add_missing_roles=bool(self.chk_addroles.get()),
            add_missing_channels=bool(self.chk_addchannels.get())))
        self._watch(fut, "Done! Check your Discord server." if build else "Everything was deleted.")


if __name__ == "__main__":
    App().mainloop()