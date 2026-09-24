"""
gui_help.py: GUI Help Menu with added Repository Support Resources
"""

import wx
import logging
import webbrowser

from .. import constants
from ..datasets import os_data
from ..wx_gui import gui_support

logger = logging.getLogger(__name__)


class HelpFrame(wx.Frame):
    """
    Append to main menu through a modal dialog
    """
    def __init__(self, parent: wx.Frame, title: str, global_constants: constants.Constants, screen_location: tuple = None) -> None:
        logger.info("Initializing Help Frame")

        # INCREASED BASE SIZE: Changed vertical boundary constraint from 200 to 300 to accommodate more buttons cleanly
        self.dialog = wx.Dialog(parent, title=title, size=(300, 320))

        # Kept separately from self.dialog: self.dialog is shown as a modal
        # macOS sheet (ShowWindowModal below), so it isn't a suitable parent
        # for any *further* top-level window we spawn from a button inside
        # it (see on_gemini_help) - Cocoa renders a window parented to a
        # sheet as another attached, chromeless sheet-like panel (no title
        # bar/traffic lights, pinned under the menu bar) rather than a normal
        # movable/closable window. self.parent_frame is the actual top-level
        # app window and parents correctly.
        self.parent_frame: wx.Frame = parent

        self.constants: constants.Constants = global_constants
        self.title: str = title

        self._generate_elements(self.dialog)
        self.dialog.ShowWindowModal()

    def _generate_elements(self, frame: wx.Frame = None) -> None:
        """
        Format:
            - Title: Patcher Resources
            - Text:  Following resources are available:
            - Button: Official Guide
            - Button: Community Discord Server
            - Button: Bug Reports / GitHub Issues  <-- Added
            - Button: Community Discussions        <-- Added
            - Button Gemini
            - Button: Return to Main Menu
        """
        frame = self if not frame else frame

        # 1. Main Title Header
        title_label = wx.StaticText(frame, label="Patcher Resources", pos=(-1, 10))
        title_label.SetFont(gui_support.font_factory(19, wx.FONTWEIGHT_BOLD))
        title_label.Centre(wx.HORIZONTAL)

        # 2. Informational Context Label
        text_label = wx.StaticText(frame, label="Following resources are available:", pos=(-1, 40))
        text_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        text_label.Centre(wx.HORIZONTAL)

        # Track the starting Y position dynamically below the description text block
        current_y = text_label.GetPosition()[1] + text_label.GetSize()[1] + 15
        button_spacing = 35  # Clean pixel tracking padding gaps between buttons

        # Define external target items using structured tuples instead of a dict mapping loop
        # Pulls from constants.py configuration bindings cleanly
        resource_links = [
            ("View official GitHub repository", getattr(self.constants, "github_official_link", "https://share.google/wPHFvNb9hJ86pGJg0")),
            ("View official GitHub Issues", getattr(self.constants, "github_issues_link", "https://github.com/albert-mueller/OpenCore-Legacy-Patcher-T2/issues")),
            ("Join official GitHub Discussions", getattr(self.constants, "github_discussions_link", "https://github.com/albert-mueller/OpenCore-Legacy-Patcher-T2/discussions")),
        ]

        # 3. Dynamic External Link Button Generation
        for label, url in resource_links:
            help_button = wx.Button(frame, label=label, pos=(-1, current_y), size=(220, 30))

            # Bound the lambda environment execution target safely using fixed parameter signatures
            help_button.Bind(wx.EVT_BUTTON, lambda event, target_url=url: webbrowser.open(target_url))
            help_button.Centre(wx.HORIZONTAL)

            # Step the coordinate down for the next item element sequence
            current_y += button_spacing
        gemini_button = wx.Button(frame, label="✨ Ask Gemini", pos=(-1, current_y), size=(220, 30))
        gemini_button.Bind(wx.EVT_BUTTON, self.on_gemini_help)
        gemini_button.Centre(wx.HORIZONTAL)

        current_y += button_spacing
        # 4. Return to Main Menu Action Button
        current_y += 10  # Add a slight visual separation gap before the close action button
        return_button = wx.Button(frame, label="Return to Main Menu", pos=(-1, current_y), size=(160, 30))
        return_button.Bind(wx.EVT_BUTTON, lambda event: frame.Close())
        return_button.Centre(wx.HORIZONTAL)

        # Automatically wrap structural layout scaling to avoid text truncation on varied OS platforms
        frame.SetSize((-1, return_button.GetPosition()[1] + return_button.GetSize()[1] + 45))

    # es gab zu viel Platz frei (16 Symbole frei statt 8)
    def on_gemini_help(self, event: wx.Event):
        # Ask Gemini in an embedded WebView on macOS Big Sur (11.0) or
        # newer; on anything older, open Gemini in the user's default web
        # browser instead. GeminiWebView (wx.html2.WebView, itself a thin
        # wrapper around the host's own system WebKit) already sidesteps
        # the pywebview crash documented in its docstring, which only
        # ever affected macOS < 11.3 - but the WebKit actually shipped
        # with genuinely older releases (High Sierra/Mojave/Catalina) is
        # too old to render Gemini's web app at all regardless of which
        # embedding library is used, so those hosts are better served by
        # their real, up-to-date default browser than a broken embedded
        # view.
        #
        # self.constants.detected_os is this codebase's own Darwin-major
        # OS enum (see datasets/os_data.py) rather than platform.mac_ver(),
        # which is what the rest of the app already uses for OS gating
        # (see e.g. gui_support.py's host_is_non_metal()/host_is_solarium())
        # - and it sidesteps mac_ver()'s well-known Big Sur "10.16"
        # misreport quirk entirely.
        if self.constants.detected_os >= os_data.os_data.big_sur:
            logging.info("- Launching Gemini AI Assistant (wx.html2 WebView)")

            # Uses gui_support.GeminiWebView (wx.html2.WebView) instead of
            # the third-party 'pywebview' package: pywebview's Cocoa
            # backend crashes the navigation delegate on macOS hosts
            # older than 11.3 (e.g. 10.13 High Sierra), see GeminiWebView
            # docstring for details.
            #
            # Parented to self.parent_frame (the real top-level app window),
            # NOT self.dialog (the modal sheet this button lives in) - see
            # the comment on self.parent_frame in __init__ for why.
            window = gui_support.GeminiWebView(self.parent_frame, size=(500, 850))
            window.Show()
        else:
            logging.info("- Launching Gemini AI Assistant (default web browser, host predates Big Sur)")
            logging.info("macOS Catalina, Mojave and High Sierra can't load Gemini in Safari and WebKit because they're too old.")
            webbrowser.open("https://gemini.google.com")
