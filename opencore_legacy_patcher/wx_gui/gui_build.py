"""
gui_build.py: Generate UI for Building OpenCore
"""

import wx
import logging
import threading
import traceback
import time
import webbrowser

from .. import constants

from ..datasets import os_data

from ..efi_builder import build

from ..wx_gui import (
    gui_main_menu,
    gui_install_oc,
    gui_support
)

class BuildFrame(wx.Frame):
    """
    Create a frame for building OpenCore
    Uses a Modal Dialog for smoother transition from other frames
    """
    def __init__(self, parent: wx.Frame, title: str, global_constants: constants.Constants, screen_location: tuple = None, save: bool = False, install: bool = False) -> None:
        logging.info("Initializing Build Frame")
        super(BuildFrame, self).__init__(parent, title=title, size=(350, 200), style=wx.DEFAULT_FRAME_STYLE & ~(wx.RESIZE_BORDER | wx.MAXIMIZE_BOX),)
        gui_support.GenerateMenubar(self, global_constants).generate()

        self.build_successful: bool = False

        self.install_button: wx.Button = None
        self.text_box:     wx.TextCtrl = None
        self.frame_modal:    wx.Dialog = None

        self.constants: constants.Constants = global_constants
        self.title: str = title
        self.install = install
        self.save = save
        self.stock_output = logging.getLogger().handlers[0].stream

        self.frame_modal = wx.Dialog(self, title=title, size=(400, 200))

        self._generate_elements(self.frame_modal)

        if self.constants.update_stage != gui_support.AutoUpdateStages.INACTIVE:
            self.constants.update_stage = gui_support.AutoUpdateStages.BUILDING

        self.Centre()

        # Cmd+Q calls Close() on this frame (see gui_support.GenerateMenubar). Without
        # this handler the frame is torn down with its sheet session still running,
        # which leaves macOS treating it as sheet-blocked - the quit then hangs and a
        # second Cmd+Q re-enters Close() on a frame already queued for deletion.
        self.Bind(wx.EVT_CLOSE, self.on_close)

        self.frame_modal.ShowWindowModal()

        if not self.constants.Experimental_Features:
            self._invoke_build()


    def on_close(self, event: wx.Event = None) -> None:
        """
        Release the sheet before this frame is destroyed (Cmd+Q, window close)
        """
        if self.frame_modal:
            # Session only, no Destroy(): destroying this frame takes the sheet with it
            gui_support.end_window_modal(self.frame_modal)
            self.frame_modal = None

        if event:
            event.Skip()
        else:
            self.Destroy()


    def _dismiss_modal(self) -> None:
        """
        End this frame's sheet session and tear the sheet down

        Hide()/Destroy() alone don't end a ShowWindowModal() session (see
        gui_support.end_window_modal); leaving it running keeps macOS treating this
        frame as sheet-blocked, which is what made a later Cmd+Q hang instead of
        quitting. The Destroy() is deferred because callers are usually buttons
        living on the dialog itself.
        """
        if not self.frame_modal:
            return

        modal = self.frame_modal
        self.frame_modal = None

        gui_support.end_window_modal(modal)
        try:
            modal.Hide()
        except RuntimeError:
            return
        wx.CallAfter(self._destroy_modal, modal)


    @staticmethod
    def _destroy_modal(modal: wx.Dialog) -> None:
        try:
            modal.Destroy()
        except RuntimeError:
            pass


    def on_build_failure(self) -> None:
        """
        Standard error dialog for build failure.
        """
        dlg = wx.MessageDialog(
            self,
            "An error occurred while building OpenCore.\n\nPlease check the logs in the text box for more information.",
            "Build Error",
            style=wx.OK | wx.ICON_ERROR
        )
        dlg.ShowModal()
        dlg.Destroy()

    def _generate_elements(self, frame: wx.Frame = None) -> None:
        """
        Generate UI elements for build frame

        Format:
            - Title label:        Build and Install OpenCore
            - Text:               Model: {Build or Host Model}
            - Profile selection:  Radio buttons (MBP14,3 only)
            - Read-only text box: {empty}
            - Button:             Return to Main Menu
        """
        frame = self if not frame else frame

        title_label = wx.StaticText(frame, label="Build and Install OpenCore", pos=(-1,5))
        title_label.SetFont(gui_support.font_factory(19, wx.FONTWEIGHT_BOLD))
        title_label.Centre(wx.HORIZONTAL)

        model_label = wx.StaticText(frame, label=f"Model: {self.constants.custom_model or self.constants.computer.real_model}", pos=(-1,30))
        model_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        model_label.Centre(wx.HORIZONTAL)

        next_y = model_label.GetPosition()[1] + model_label.GetSize()[1] + 5

        # Profile selection for MacBookPro14,x (T1)
        target_model = self.constants.custom_model or self.constants.computer.real_model
        if target_model in ["MacBookPro14,1", "MacBookPro14,2", "MacBookPro14,3"] and self.constants.Experimental_Features:
            self.radio_standard = wx.RadioButton(frame, label="STANDARD / SAFE", pos=(-1, next_y), style=wx.RB_GROUP)
            self.radio_standard.Centre(wx.HORIZONTAL)
            next_y += 30

            self.radio_testa = wx.RadioButton(frame, label="TEST-A (GPU)", pos=(-1, next_y))
            self.radio_testa.Centre(wx.HORIZONTAL)
            next_y += 30

            self.radio_testb = wx.RadioButton(frame, label="TEST-B (GPU + No-Compat)", pos=(-1, next_y))
            self.radio_testb.Centre(wx.HORIZONTAL)
            next_y += 30

            self.radio_testc = wx.RadioButton(frame, label="TEST-C (GPU + No-Compat + VBootArgs)", pos=(-1, next_y))
            self.radio_testc.Centre(wx.HORIZONTAL)
            next_y += 30

            self.radio_testd = wx.RadioButton(frame, label="TEST-D (GPU + BootArgs + XPC)", pos=(-1, next_y))
            self.radio_testd.Centre(wx.HORIZONTAL)
            next_y += 40

            if self.constants.build_profile == "test_d":
                self.radio_testd.SetValue(True)
            elif self.constants.build_profile == "test_c":
                self.radio_testc.SetValue(True)
            elif self.constants.build_profile == "test_b":
                self.radio_testb.SetValue(True)
            elif self.constants.build_profile == "test_a":
                self.radio_testa.SetValue(True)
            else:
                self.radio_standard.SetValue(True)
        else:
            self.radio_standard = None
            self.radio_testa = None
            self.radio_testb = None
            self.radio_testc = None
            self.radio_testd = None

        if self.constants.Experimental_Features:
            # Button: Build OpenCore (Only in Developer Mode to allow selection)
            build_button = wx.Button(frame, label="🔨 Build OpenCore", pos=(-1, next_y), size=(150, 30))
            build_button.Bind(wx.EVT_BUTTON, self.on_build_click)
            build_button.Centre(wx.HORIZONTAL)
            self.build_button = build_button
            next_y += 35

        # Button: Install OpenCore
        install_button = wx.Button(frame, label="🔩 Install OpenCore", pos=(-1, next_y), size=(150, 30))
        install_button.Bind(wx.EVT_BUTTON, self.on_install)
        install_button.Centre(wx.HORIZONTAL)
        install_button.Disable()
        self.install_button = install_button

        # Read-only text box: {empty}
        text_box = wx.TextCtrl(frame, value="", pos=(-1, install_button.GetPosition()[1] + install_button.GetSize()[1] + 10), size=(380, 350), style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_RICH2)
        text_box.Centre(wx.HORIZONTAL)
        self.text_box = text_box

        # Button: Return to Main Menu
        return_button = wx.Button(frame, label="Return to Main Menu", pos=(-1, text_box.GetPosition()[1] + text_box.GetSize()[1] + 5), size=(150, 30))
        return_button.Bind(wx.EVT_BUTTON, self.on_return_to_main_menu)
        return_button.Centre(wx.HORIZONTAL)

        # Disable by default if standard mode (since it builds automatically)
        if not self.constants.Experimental_Features:
            return_button.Disable()

        self.return_button = return_button

        # Adjust window size to fit all elements
        frame.SetSize((-1, return_button.GetPosition()[1] + return_button.GetSize()[1] + 40))


    def _invoke_build(self) -> None:
        """
        Invokes build function and waits for it to finish
        """
        while gui_support.PayloadMount(self.constants, self).is_unpack_finished() is False:
            wx.Yield()
            time.sleep(self.constants.thread_sleep_interval)

        thread = threading.Thread(target=self._build)
        thread.start()

        gui_support.wait_for_thread(thread)

        if self.build_successful is False:
            # Mirrors the Report Issue / Ask Gemini / Close dialog used for OpenCore
            # installation errors (gui_install_oc.py) instead of a plain OK-only alert,
            # so build failures get the same reporting and AI-assist options.
            try:
                error_dialog = wx.Dialog(self, title="Build Error", size=(460, 200))

                main_sizer = wx.BoxSizer(wx.VERTICAL)
                button_sizer = wx.BoxSizer(wx.HORIZONTAL)

                error_msg = "An error occurred while building OpenCore.\n\nWould you like to report this issue or ask Gemini for help?"
                msg_text = wx.StaticText(error_dialog, label=error_msg)
                msg_text.SetFont(gui_support.font_factory(12, wx.FONTWEIGHT_NORMAL))

                btn_report = wx.Button(error_dialog, id=wx.ID_OK, label="Report Issue")
                btn_gemini = wx.Button(error_dialog, id=wx.ID_ANY, label="Ask Gemini")
                btn_close  = wx.Button(error_dialog, id=wx.ID_CANCEL, label="Close")

                # Define a custom return code identifier for Gemini tracking
                GEMINI_CLICKED_ID = 10001

                # Bind an event so clicking the button closes the dialog and returns our custom identifier
                error_dialog.Bind(wx.EVT_BUTTON, lambda event: error_dialog.EndModal(GEMINI_CLICKED_ID), btn_gemini)

                main_sizer.Add(msg_text, 1, wx.ALL | wx.EXPAND, 20)
                button_sizer.Add(btn_report, 0, wx.RIGHT, 10)
                button_sizer.Add(btn_gemini, 0, wx.RIGHT, 10)
                button_sizer.Add(btn_close, 0)

                main_sizer.Add(button_sizer, 0, wx.ALIGN_RIGHT | wx.BOTTOM | wx.RIGHT, 20)

                error_dialog.SetSizer(main_sizer)
                error_dialog.Layout()
                error_dialog.Centre()

                response = error_dialog.ShowModal()

                if response == wx.ID_OK:
                    webbrowser.open("https://github.com/albert-mueller/OpenCore-Legacy-Patcher-T2/issues")
                # Safari und WebKit unter macOS Catalina und älter können nicht richtig Gemini öffnen, deshalb falls diese Version läuft, wird Gemini ins Webbrowser geöffnet
                elif response == GEMINI_CLICKED_ID:
                    # Gemini can't see the build log on its own, so copy it to the clipboard
                    # and tell the user to paste it in, rather than making them go hunt for
                    # the text box and select/copy it manually.
                    try:
                        clipboard = wx.Clipboard.Get()
                        if not clipboard.IsOpened():
                            clipboard.Open()
                        clipboard.SetData(wx.TextDataObject(self.text_box.GetValue()))
                        clipboard.Close()
                        wx.MessageDialog(
                            self,
                            "The build log has been copied to your clipboard.\n\nPaste it into the Gemini chat so it can help diagnose the error.",
                            "Copied to Clipboard",
                            wx.OK | wx.ICON_INFORMATION
                        ).ShowModal()
                    except Exception as clipboard_error:
                        logging.error(f"Failed to copy build log to clipboard: {clipboard_error}")

                    if self.constants.detected_os >= os_data.os_data.big_sur:
                        logging.info("- Launching Gemini AI Assistant (wx.html2 WebView)")
                        gemini_window = gui_support.GeminiWebView(self, title="Gemini AI Assistant")
                        gemini_window.Show()
                    else:
                        logging.info("- Launching Gemini AI Assistant (default web browser, host predates Big Sur)")
                        logging.info("macOS Catalina, Mojave and High Sierra can't load Gemini in Safari and WebKit because they're too old.")
                        webbrowser.open("https://gemini.google.com")

                error_dialog.Destroy()
            except Exception as e:
                logging.error(f"Failed to display build error dialog: {e}")
                dialog = wx.MessageDialog(
                    parent=self,
                    message="An error occurred while building OpenCore. We tried to display another error dialog, but encountered an error and that's why it displays this instead.",
                    caption="Error building OpenCore",
                    style=wx.OK | wx.ICON_ERROR
                )
                dialog.ShowModal()
            self.return_button.Enable()
            return
        if self.save:
            index = 1
            number_lines = self.text_box.GetNumberOfLines()
            lines = []
            while index != number_lines:
                lines.append(f"{self.text_box.GetLineText(index)}\n")
                index += 1
            with open(self.constants.oc_build_path / "Build.log", "w") as f:
                f.writelines(lines)
            dialog = wx.MessageDialog(
                parent=self,
                message=f"OpenCore was built and placed at {self.constants.oc_build_path}",
                caption="Done Building",
                style=wx.OK | wx.ICON_INFORMATION
            )
            dialog.ShowModal()
            self.on_return_to_main_menu()
        elif self.install:
            self.on_install()


    def _build(self) -> None:
        """
        Calls build function and redirects stdout to the text box
        """
        logger = logging.getLogger()
        handler = gui_support.ThreadHandler(self.text_box) # Keep a reference
        logger.addHandler(handler)


        if self.constants.build_profile == "test_b":
            profile_name = "TEST-B GPU"
        elif self.constants.build_profile == "test_c":
            profile_name = "TEST-C TAHOE / ALBERT"
        elif self.constants.build_profile == "test_d":
            profile_name = "TEST-D (GPU + BootArgs + XPC)"
        elif self.constants.build_profile == "test_c_spoofed":
            profile_name = "TEST-C SPOOFED / ALBERT"
        else:
            profile_name = "STANDARD / SAFE"
        target_model = self.constants.custom_model or self.constants.computer.real_model

        logging.info("=========================================")
        logging.info("          BUILD CONFIGURATION            ")
        logging.info("=========================================")
        logging.info(f"Target Model: {target_model}")
        logging.info(f"Profile: {profile_name}")

        if target_model in ["MacBookPro14,1", "MacBookPro14,2", "MacBookPro14,3"]:
            t1_status = "DETECTED" if getattr(self.constants.computer, 't1_chip', False) else f"ENABLED ({target_model})"
            wifi_status = f"{self.constants.computer.wifi.vendor_id:04X}:{self.constants.computer.wifi.device_id:04X}" if getattr(self.constants.computer, 'wifi', None) else "14E4:43BA"
            logging.info(f"T1 Security:  {t1_status}")
            logging.info(f"Wi-Fi Module: {wifi_status}")

        logging.info("=========================================")
        logging.info("")


        try:
            build.BuildOpenCore(self.constants.custom_model or self.constants.computer.real_model, self.constants)
            self.build_successful = True
        except Exception as e:
            logging.error("An internal error occurred while building:\n")
            logging.error(traceback.format_exc())

            # Handle bug from 2.1.0 where None type was stored in config.plist from global settings
            if "TypeError: unsupported type: <class 'NoneType'>" in traceback.format_exc():
                logging.error("If you continue to see this error, delete the following file and restart the application:")
                logging.error("Path: /Users/Shared/.com.dortania.opencore-legacy-patcher.plist")

        finally:
            # Was logger.handlers[2], which is only the ThreadHandler if the root
            # logger happens to have exactly the handlers it had at startup - so it
            # could remove the wrong handler and leave this one attached to a text
            # box that the next frame handoff destroys. Match by type instead, and
            # do it in a finally so an early exception cannot leak the handler.
            for existing in logger.handlers[:]:
                if isinstance(existing, gui_support.ThreadHandler):
                    logger.removeHandler(existing)


    def on_return_to_main_menu(self, event: wx.Event = None) -> None:
        """
        Return to main menu
        """
        screen_location = self.GetScreenPosition()
        self._dismiss_modal()

        main_menu_frame = gui_main_menu.MainFrame(
            None,
            title=self.title,
            global_constants=self.constants,
            screen_location=screen_location,
        )
        main_menu_frame.Show()
        # Deferred: this handler is running inside a button that lives on the frame
        wx.CallAfter(self.Destroy)

    def on_install(self, event: wx.Event = None) -> None:
        """
        Launch install frame
        """
        # Stop any pending UI updates
        logger = logging.getLogger()
        for handler in logger.handlers[:]:
            if isinstance(handler, gui_support.ThreadHandler):
                logger.removeHandler(handler)

        screen_location = self.GetScreenPosition()
        self._dismiss_modal() # Hides first, so it feels responsive

        install_oc_frame = gui_install_oc.InstallOCFrame(
            None,
            title=self.title,
            global_constants=self.constants,
            screen_location=screen_location,
        )
        install_oc_frame.Show()
        # Deferred, so this frame outlives the button event handler running inside it
        wx.CallAfter(self.Destroy)

    def on_build_click(self, event: wx.Event) -> None:
        self.build_button.Disable()
        if getattr(self, "radio_standard", None):
            if self.radio_testd.GetValue():
                self.constants.build_profile = "test_d"
            elif self.radio_testc.GetValue():
                self.constants.build_profile = "test_c"
            elif self.radio_testb.GetValue():
                self.constants.build_profile = "test_b"
            elif self.radio_testa.GetValue():
                self.constants.build_profile = "test_a"
            else:
                self.constants.build_profile = "standard"

            self.radio_standard.Disable()
            self.radio_testa.Disable()
            self.radio_testb.Disable()
            self.radio_testc.Disable()
            self.radio_testd.Disable()
        if hasattr(self, "return_button"):
            self.return_button.Disable()
        self._invoke_build()
