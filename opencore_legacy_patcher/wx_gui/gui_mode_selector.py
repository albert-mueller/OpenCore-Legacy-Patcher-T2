import wx
import logging

from .. import constants
from . import gui_support, gui_main_menu

class ModeSelectorFrame(wx.Frame):
    def __init__(self, parent: wx.Frame, title: str, global_constants: constants.Constants, screen_location: tuple = None):
        logging.info("Initializing Mode Selector Frame")
        super(ModeSelectorFrame, self).__init__(parent, title=title, size=(450, 300), style=wx.DEFAULT_FRAME_STYLE & ~(wx.RESIZE_BORDER | wx.MAXIMIZE_BOX))

        self.constants: constants.Constants = global_constants
        self.title: str = title
        self.screen_location: tuple = screen_location

        # Center the window
        if self.screen_location:
            self.SetPosition(self.screen_location)
        else:
            self.Centre()

        self._build_ui()
        self.Show()

    def _build_ui(self):
        panel = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)

        # Title
        title_font = wx.Font(16, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        title_lbl = wx.StaticText(panel, label="Choose Application Mode")
        title_lbl.SetFont(title_font)
        vbox.Add(title_lbl, flag=wx.ALIGN_CENTER | wx.TOP, border=20)

        # Subtitle
        sub_lbl = wx.StaticText(panel, label="Which repository version would you like to run?")
        vbox.Add(sub_lbl, flag=wx.ALIGN_CENTER | wx.TOP | wx.BOTTOM, border=10)

        # Buttons
        btn_matteo = wx.Button(panel, label="Matteo - Tahoe Experimental\n(Custom UI & Experimental Patches)", size=(350, 60))
        btn_albert = wx.Button(panel, label="Albert T2 - Upstream\n(Standard UI & Upstream Defaults)", size=(350, 60))

        btn_matteo.Bind(wx.EVT_BUTTON, self.on_matteo_click)
        btn_albert.Bind(wx.EVT_BUTTON, self.on_albert_click)

        vbox.Add(btn_matteo, flag=wx.ALIGN_CENTER | wx.ALL, border=10)
        vbox.Add(btn_albert, flag=wx.ALIGN_CENTER | wx.ALL, border=10)

        panel.SetSizer(vbox)

    def on_matteo_click(self, event):
        logging.info("Selected Mode: Matteo")
        self.constants.app_mode = "matteo"
        self.constants.build_profile = "test_c"
        self._launch_main_menu()

    def on_albert_click(self, event):
        logging.info("Selected Mode: Albert T2")
        self.constants.app_mode = "albert"
        self.constants.build_profile = "standard"
        self._launch_main_menu()

    def _launch_main_menu(self):
        # Update title based on mode
        title = f"{self.constants.patcher_name} {self.constants.patcher_version}"
        if self.constants.app_mode == "matteo":
            title = f"{self.constants.patcher_name} {self.constants.experimental_version} (Matteo's Build)"

        frame = gui_main_menu.MainFrame(
            None,
            title=title,
            global_constants=self.constants,
            screen_location=self.GetPosition()
        )
        # We must bind the close event to the app, but since we are not the app, we just close ourselves.
        # Wait, if we close ourselves, does the app exit? No, because frame is open.
        # But wait, gui_entry.py binds EVT_CLOSE to self.frame. If we replace it, it might fail.
        # It's better to just swap the panel or handle the frame properly.
        # Since gui_entry.py creates the entry frame and assigns self.frame, we should update app.frame.
        app = wx.GetApp()
        if hasattr(app, 'frame'):
            app.frame = frame
            app.SetTopWindow(frame)
            frame.Bind(wx.EVT_CLOSE, app.OnCloseFrame)

        self.Hide()
        self.Destroy()
