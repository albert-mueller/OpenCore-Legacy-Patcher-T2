"""
gui_download.py: Generate UI for downloading files
"""

import wx
import wx.html2
import logging
import time
import webbrowser

import markdown2

from ..support import image_handler
from .. import constants
from ..datasets import css_data

from ..wx_gui import gui_support

from ..support import (
    network_handler,
    utilities
)


class DownloadFrame(wx.Frame):
    """
    Update provided frame with download stats
    """
    # Size of the release notes view shown while an app update downloads
    CHANGELOG_WIDTH:  int = 610
    CHANGELOG_HEIGHT: int = 320

    def __init__(self, parent: wx.Frame, title: str, global_constants: constants.Constants, download_obj: network_handler.DownloadObject, item_name: str, download_icon = None, cancel_message: str = None, changelog: str = None) -> None:
        logging.info("Initializing Download Frame")
        self.constants: constants.Constants = global_constants
        self.title: str = title
        self.parent: wx.Frame = parent
        self.download_obj: network_handler.DownloadObject = download_obj
        self.item_name: str = item_name
        if download_icon:
            self.download_icon: str = str(download_icon)
        else:
            self.download_icon: str = "/System/Library/CoreServices/Installer.app/Contents/Resources/package.icns"

        self.user_cancelled: bool = False
        # Callers can supply their own confirmation text for the Cancel button
        # (the updater uses this to warn about staying on an outdated build).
        # Everyone else keeps the generic prompt.
        self.cancel_message: str = cancel_message or "Are you sure you want to cancel the download?"
        self.cancel_icon: int = wx.ICON_WARNING if cancel_message else wx.ICON_QUESTION

        # Optional Markdown release notes (the updater passes the changelog of the
        # release being downloaded). Every other download keeps the compact layout.
        self.changelog: str = changelog.strip() if isinstance(changelog, str) and changelog.strip() else None

        width = self.CHANGELOG_WIDTH + 40 if self.changelog else 400
        self.frame_modal = wx.Dialog(parent, title=title, size=(width, 200))

        self._generate_elements(self.frame_modal)


    def _generate_elements(self, frame: wx.Dialog = None) -> None:
        """
        Generate elements for download frame
        """

        frame = self if not frame else frame
        icon = self.download_icon
        icon = wx.StaticBitmap(frame, bitmap=image_handler.get_bitmap(icon), pos=(-1, 20))
        icon.SetSize((100, 100))
        icon.Centre(wx.HORIZONTAL)

        title_label = wx.StaticText(frame, label=f"Downloading: {self.item_name}", pos=(-1,icon.GetPosition()[1] + icon.GetSize()[1] + 20))
        title_label.SetFont(gui_support.font_factory(19, wx.FONTWEIGHT_BOLD))
        title_label.Centre(wx.HORIZONTAL)

        progress_bar = wx.Gauge(frame, range=100, pos=(-1, title_label.GetPosition()[1] + title_label.GetSize()[1] + 5), size=(300, 20), style=wx.GA_SMOOTH|wx.GA_PROGRESS)
        progress_bar.Centre(wx.HORIZONTAL)

        label_amount = wx.StaticText(frame, label="Preparing download", pos=(-1, progress_bar.GetPosition()[1] + progress_bar.GetSize()[1]))
        label_amount.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        label_amount.Centre(wx.HORIZONTAL)

        button_y = label_amount.GetPosition()[1] + label_amount.GetSize()[1] + 10
        if self.changelog:
            changelog_view = self._generate_changelog_view(frame, button_y + 5)
            if changelog_view is not None:
                button_y = changelog_view.GetPosition()[1] + changelog_view.GetSize()[1] + 15

        return_button = wx.Button(frame, label="Cancel", pos=(-1, button_y))
        return_button.Bind(wx.EVT_BUTTON, lambda event: self.terminate_download())
        return_button.Centre(wx.HORIZONTAL)

        # Set size of frame
        frame.SetSize((-1, return_button.GetPosition()[1] + return_button.GetSize()[1] + 40))
        frame.SetPosition(self.parent.GetScreenPosition())
        frame.ShowWindowModal()

        self.download_obj.download()
        while self.download_obj.is_active():

            # wx.Yield() at the bottom of this loop pumps the event queue, so a Cmd+Q
            # or window close runs the full teardown *inside* this loop: quit_app()
            # destroys every top-level window, which deletes the C++ side of these
            # widgets while their Python wrappers live on. Touching one after that
            # raises "RuntimeError: wrapped C/C++ object of type Gauge has been
            # deleted", so stop the transfer and get out instead.
            if gui_support.is_app_exiting() or not frame or not progress_bar or not label_amount:
                logging.info("Download window went away, stopping download")
                self.user_cancelled = True
                self.download_obj.stop()
                return

            percentage: int = round(self.download_obj.get_percent())
            if percentage == 0:
                percentage = 1

            # The teardown can also land between the check above and these calls,
            # so the widget updates stay guarded on their own.
            try:
                if percentage == -1:
                    amount_str = f"{utilities.human_fmt(self.download_obj.downloaded_file_size)} downloaded ({utilities.human_fmt(self.download_obj.get_speed())}/s)"
                    progress_bar.Pulse()
                else:
                    amount_str = f"{utilities.seconds_to_readable_time(self.download_obj.get_time_remaining())}left - {utilities.human_fmt(self.download_obj.downloaded_file_size)} of {utilities.human_fmt(self.download_obj.total_file_size)} ({utilities.human_fmt(self.download_obj.get_speed())}/s)"
                    progress_bar.SetValue(int(percentage))

                label_amount.SetLabel(amount_str)
                label_amount.Centre(wx.HORIZONTAL)
            except RuntimeError:
                logging.info("Download window destroyed mid-update, stopping download")
                self.user_cancelled = True
                self.download_obj.stop()
                return

            wx.Yield()
            time.sleep(self.constants.thread_sleep_interval)

        if self.download_obj.download_complete is False and self.user_cancelled is False:
            logging.error(f"Download failed due to an error")
            logging.exception("Stack Trace:")
            if not gui_support.is_app_exiting():
                wx.MessageBox(f"Download failed: \n{self.download_obj.error_msg}", "Error", wx.OK | wx.ICON_ERROR)

        # Same story on the way out: either of these may already be gone.
        for widget in (progress_bar, frame):
            try:
                widget.Destroy()
            except RuntimeError:
                continue


    def _generate_changelog_view(self, frame: wx.Dialog, y: int):
        """
        Show the release notes of the version being downloaded below the progress
        bar. Returns the view, or None if it could not be created - the download
        itself must never fail because of the release notes.
        """
        try:
            # safe_mode="escape": the release notes come from GitHub (remote input),
            # so raw HTML in them is shown as text instead of being rendered.
            html_markdown = markdown2.markdown(self.changelog, extras=["tables"], safe_mode="escape")
            html_code = f'''
<html>
    <head>
        <style>
            {css_data.updater_css}
        </style>
    </head>
    <body class="markdown-body">
        {html_markdown.replace("<a href=", "<a target='_blank' href=")}
    </body>
</html>
'''
            web_view = wx.html2.WebView.New(frame, pos=(-1, y), size=(self.CHANGELOG_WIDTH, self.CHANGELOG_HEIGHT), style=wx.BORDER_SUNKEN)
            web_view.SetPage(html_code, "")
            web_view.Bind(wx.html2.EVT_WEBVIEW_NEWWINDOW, self._on_changelog_link)
            web_view.EnableContextMenu(False)
            web_view.Centre(wx.HORIZONTAL)
            return web_view
        except Exception as e:
            logging.error(f"Could not show the changelog in the download window: {e}")
            return None


    def _on_changelog_link(self, event) -> None:
        """
        Open links from the release notes in the browser instead of the view
        """
        webbrowser.open(event.GetURL())


    def terminate_download(self) -> None:
        """
        Terminate download
        """
        if wx.MessageBox(self.cancel_message, "Cancel Download", wx.YES_NO | self.cancel_icon | wx.NO_DEFAULT) == wx.YES:
            logging.info("User cancelled download")
            self.user_cancelled = True
            self.download_obj.stop()


