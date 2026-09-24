"""
gui_oc_settings.py: Settings Frame for the GUI
"""



from pathlib import Path

import wx
import wx.adv
import logging
import subprocess
import py_sip_xnu

from .. import constants

from ..wx_gui import (
    gui_support,
    gui_build,
)
from ..support import (
    global_settings,
    generate_smbios,
)
from ..datasets import (
    sip_data,
    smbios_data,
    os_data,
    cpu_data,
    model_array
)

class OCSettingsFrame(wx.Frame):
    """
    OC Settings Frame
    """

    def __init__(self, parent: wx.Frame, title: str, global_constants: constants.Constants, screen_location: tuple = None):
        logging.info("Initializing Settings Frame")
        super().__init__(parent, title=title)
        self.constants: constants.Constants = global_constants
        self.title: str = title
        self.parent: wx.Frame = parent

        self.hyperlink_colour = (25, 179, 231)

        # The two controls that actually write an OpenCore build to disk. Held as attributes
        # so their enabled state can be re-evaluated live (see _refresh_build_gated_buttons()),
        # instead of being frozen at whatever host_can_build() returned when the frame opened.
        self.save_oc_button: wx.Button = None
        self.build_oc_button: wx.Button = None

        self.settings = self._settings()

        self.frame_modal = wx.Dialog(parent, title=title, size=(600, 685))

        self._generate_elements(self.frame_modal)
        self.frame_modal.ShowWindowModal()


    def _generate_elements(self, frame: wx.Frame = None) -> None:
        """
        Generates elements for the OC Settings Frame
        Uses wx.Notebook to implement a tabbed interface
        and relies on 'self._settings()' for populating
        """

        notebook = wx.Notebook(frame)
        notebook.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.AddSpacer(10)

        tabs = list(self.settings.keys())
        for tab in tabs:
            if tab == "Security":
                # The Security tab's SIP checkbox matrix (_populate_sip_settings)
                # is sized dynamically and can exceed the fixed dialog height,
                # clipping the bottom rows off screen. Make this page scrollable
                # so the extra content stays reachable instead of being cut off.
                panel = wx.ScrolledWindow(notebook)
                panel.SetScrollRate(0, 20)
            else:
                panel = wx.Panel(notebook)
            notebook.AddPage(panel, tab)

        sizer.Add(notebook, 1, wx.EXPAND | wx.ALL, 10)

        bot_sizer = wx.BoxSizer(wx.HORIZONTAL)

        # Add Save OpenCore Button
        save_oc_button = wx.Button(frame, label="Save OpenCore", pos=(-1, -1), size=(120, 30))
        save_oc_button.Bind(wx.EVT_BUTTON, self.on_save)
        save_oc_button.SetToolTip("Builds and Saves OpenCore to the filesystem")
        save_oc_button.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        self.save_oc_button = save_oc_button
        bot_sizer.Add(save_oc_button, 0, wx.ALIGN_CENTER | wx.ALL, 0)

        bot_sizer.AddSpacer(20)

        # Add Build OpenCore Button
        build_oc_button = wx.Button(frame, label="Install OpenCore", pos=(-1, -1), size=(120, 30))
        if self.constants.Experimental_Features or self.constants.True_Developer_Mode:
            if self.constants.build_profile == None or self.constants.build_profile == "":
                build_oc_button.Bind(wx.EVT_BUTTON, self.on_build_opencore_menu)
            else:
                 build_oc_button.Bind(wx.EVT_BUTTON, self.on_build_and_install)
        else:
            build_oc_button.Bind(wx.EVT_BUTTON, self.on_build_and_install_standard)
        # Deliberately NOT SetDefault(): wx fires the default button on Return from anywhere
        # in the dialog, including from any wx.TextCtrl without TE_PROCESS_ENTER (the custom
        # serial number fields on this very frame). "Install OpenCore" writes OpenCore to disk
        # and BuildFrame starts building the moment it is constructed (gui_build.py), so a
        # single stray Return while editing a text field was enough to kick off a full,
        # unconfirmed build and install.
        build_oc_button.SetToolTip("Installs OpenCore to your disk")
        build_oc_button.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        self.build_oc_button = build_oc_button
        # Sole gate for both disk-writing buttons now that the main menu lets every host in
        # here. Re-run on every change to a setting that feeds host_can_build().
        self._refresh_build_gated_buttons()
        bot_sizer.Add(build_oc_button, 0, wx.ALIGN_CENTER | wx.ALL, 0)

        bot_sizer.AddSpacer(20)

        # Add return button
        return_button = wx.Button(frame, label="Return", pos=(-1, -1), size=(110, 30))
        return_button.Bind(wx.EVT_BUTTON, self.on_return)
        return_button.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        sizer.Add(return_button, 0, wx.ALIGN_CENTER | wx.ALL, 0)




        sizer.Add(bot_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 10)

        frame.SetSizer(sizer)
        frame.Layout()

        # wx.Notebook only lays out its currently active page, so a hidden
        # page's scrolled window can end up with stale scrollbar metrics
        # from before it had its final size. Re-adjust on every tab switch
        # to keep the Security tab's scrollbar correct.
        notebook.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self.on_oc_settings_tab_changed)
        self._oc_settings_notebook = notebook

        horizontal_center = frame.GetSize()[0] / 2
        for tab in tabs:
            if tab not in self.settings:
                continue

            stock_height = 0
            stock_width = 20

            height = stock_height
            width = stock_width

            lowest_height_reached = height
            highest_height_reached = height

            panel = notebook.GetPage(tabs.index(tab))

            for setting, setting_info in self.settings[tab].items():
                if setting_info["type"] == "populate":
                    # execute populate function
                    if setting_info["args"] == wx.Frame:
                        setting_info["function"](panel)
                    else:
                        raise Exception("Invalid populate function")
                    # Populate functions (e.g. _populate_sip_settings) add their
                    # own widgets directly and don't report a height back, so
                    # 'lowest_height_reached' never learns about them. Scan the
                    # panel's children so the scrollable Security tab below gets
                    # an accurate virtual size instead of clipping this content.
                    for child in panel.GetChildren():
                        child_bottom = child.GetPosition()[1] + child.GetSize()[1]
                        if child_bottom > lowest_height_reached:
                            lowest_height_reached = child_bottom
                    continue

                if setting_info["type"] == "title":
                    stock_height = lowest_height_reached
                    height = stock_height
                    width = stock_width

                    height += 10

                    # Add title
                    title = wx.StaticText(panel, label=setting, pos=(-1, -1))
                    title.SetFont(gui_support.font_factory(19, wx.FONTWEIGHT_BOLD))

                    title.SetPosition((int(horizontal_center) - int(title.GetSize()[0] / 2) - 15, height))
                    highest_height_reached = height + title.GetSize()[1] + 10
                    height += title.GetSize()[1] + 10
                    if height > lowest_height_reached:
                        lowest_height_reached = height
                    continue

                if setting_info["type"] == "sub_title":
                    # Add sub-title
                    sub_title = wx.StaticText(panel, label=setting, pos=(-1, -1))
                    sub_title.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))

                    sub_title.SetPosition((int(horizontal_center) - int(sub_title.GetSize()[0] / 2) - 15, height))
                    highest_height_reached = height + sub_title.GetSize()[1] + 10
                    height += sub_title.GetSize()[1] + 10
                    if height > lowest_height_reached:
                        lowest_height_reached = height
                    continue

                if setting_info["type"] == "wrap_around":
                    height = highest_height_reached
                    # On a scrollable page (currently only Security), a vertical
                    # scrollbar eats into the panel's own width from the right
                    # edge. The right column is otherwise positioned as if the
                    # full frame width were available, which ran right-column
                    # text (e.g. "Secure Boot Model") underneath/behind the
                    # scrollbar. Same fix as the wrap width in
                    # _populate_sip_settings: size off the actual scrollbar
                    # metric instead of a guessed constant.
                    right_column_offset = 300
                    if isinstance(panel, wx.ScrolledWindow):
                        right_column_offset -= wx.SystemSettings.GetMetric(wx.SYS_VSCROLL_X)
                    width = right_column_offset if width is stock_width else stock_width
                    continue

                if setting_info["type"] == "checkbox":
                    # Add checkbox, and description underneath
                    checkbox = wx.CheckBox(panel, label=setting, pos=(10 + width, 10 + height), size = (300,-1))

                    value = False
                    if "value" in setting_info:
                        try:
                            value = bool(setting_info["value"])
                        except ValueError:
                            logging.error(f"Invalid value for {setting}, got {setting_info['value']} (type: {type(setting_info['value'])})")
                            value = False

                    checkbox.SetValue(value)
                    checkbox.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_BOLD))
                    event = lambda event, warning=setting_info["warning"] if "warning" in setting_info else "", override=bool(setting_info["override_function"]) if "override_function" in setting_info else False: self.on_checkbox(event, warning, override)
                    checkbox.Bind(wx.EVT_CHECKBOX, event)
                    if "condition" in setting_info:
                        checkbox.Enable(setting_info["condition"])
                        if setting_info["condition"] is False:
                            checkbox.SetValue(False)
                            # Best-effort: native macOS controls mostly ignore an
                            # explicit foreground colour once disabled and fall
                            # back to the OS's own (quite light) "disabled" look,
                            # but this costs nothing and helps on backends that
                            # do respect it.
                            checkbox.SetForegroundColour((90, 90, 90))

                elif setting_info["type"] == "spinctrl":
                    # Add spinctrl, and description underneath
                    spinctrl = wx.SpinCtrl(panel, value=str(setting_info["value"]), pos=(width - 20, 10 + height), min=setting_info["min"], max=setting_info["max"], size = (45,-1))
                    spinctrl.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_BOLD))
                    spinctrl.Bind(wx.EVT_TEXT, lambda event, variable=setting: self.on_spinctrl(event, variable))
                    # Add label next to spinctrl
                    label = wx.StaticText(panel, label=setting, pos=(spinctrl.GetSize()[0] + width - 16, spinctrl.GetPosition()[1]))
                    label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_BOLD))
                elif setting_info["type"] == "choice":
                    # Title
                    title = wx.StaticText(panel, label=setting, pos=(width + 30, 10 + height))
                    title.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_BOLD))
                    height += title.GetSize()[1] + 10

                    # Add combobox, and description underneath
                    choice = wx.Choice(panel, pos=(width + 25, 10 + height), choices=setting_info["choices"], size = (150,-1))
                    choice.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
                    choice.SetSelection(choice.FindString(setting_info["value"]))
                    if "override_function" in setting_info:
                        choice.Bind(wx.EVT_CHOICE, lambda event, variable=setting: self.settings[tab][variable]["override_function"](event))
                    else:
                        choice.Bind(wx.EVT_CHOICE, lambda event, variable=setting: self.on_choice(event, variable))
                    height += 10
                elif setting_info["type"] == "button":
                    button = wx.Button(panel, label=setting, pos=(width + 25, 10 + height), size = (200,-1))
                    button.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
                    button.Bind(wx.EVT_BUTTON, lambda event, variable=setting: self.settings[tab][variable]["function"](event))
                    height += 10
                    if "condition" in setting_info:
                        button.Enable(setting_info["condition"])


                else:
                    raise Exception("Invalid setting type")

                lines = '\n'.join(setting_info["description"])
                description = wx.StaticText(panel, label=lines, pos=(30 + width, 10 + height + 20))
                description.SetFont(gui_support.font_factory(11, wx.FONTWEIGHT_NORMAL))
                height += 40
                if "condition" in setting_info:
                    if setting_info["condition"] is False:
                        # Was (128, 128, 128): too low-contrast against the panel
                        # background to read comfortably, especially at 11pt
                        # (e.g. the Security tab's "Not used on T2 Macs" notes).
                        description.SetForegroundColour((90, 90, 90))

                # Check number of lines in description, and adjust spacer accordingly
                for i, line in enumerate(lines.split('\n')):
                    if line == "":
                        continue
                    if i == 0:
                        height += 11
                    else:
                        height += 13

                # Keep 'lowest_height_reached' current as items are placed (not
                # just once after the whole tab has been processed, see below).
                # A 'title' entry positions itself using 'lowest_height_reached'
                # as its starting y so it renders below everything placed so
                # far in either column; without updating it here, any second
                # title in a tab whose preceding items were plain
                # checkboxes/descriptions (no 'populate' in between) would
                # still read the stale initial value and land on the exact
                # same position as the first title.
                if height > lowest_height_reached:
                    lowest_height_reached = height

            if height > lowest_height_reached:
                lowest_height_reached = height

            if isinstance(panel, wx.ScrolledWindow):
                # Finalise the scrollable Security tab now that its full content
                # height (including populate-function widgets) is known, so the
                # SIP checkbox matrix and everything above it stay reachable
                # instead of being clipped by the fixed dialog height.
                panel.SetVirtualSize((int(horizontal_center * 2), lowest_height_reached + 50))
                panel.AdjustScrollbars()


    def _refresh_build_gated_buttons(self) -> None:
        """
        Enable/disable the two controls that write an OpenCore build to disk
        ("Save OpenCore" and "Install OpenCore") based on host_can_build().

        This frame is reachable from the main menu on every host now, so this is the only
        thing standing between an unsupported host and a build - and it has to stay live:
        "Allow native models" (allow_oc_everywhere) and the target model are changed from
        inside this very frame, so evaluating host_can_build() only once at construction
        would leave a user who just ticked the box staring at two dead buttons until they
        relaunched the app.
        """
        can_build = gui_support.CheckProperties(self.constants).host_can_build()
        for button in (self.save_oc_button, self.build_oc_button):
            if not button:
                continue
            button.Enable(can_build)

    # MARK: Settings dict
    def _settings(self) -> dict:
        """
        Generates a dictionary of settings to be used in the GUI
        General format:
        {
            "Tab Name": {
                "type": "title" | "checkbox" | "spinctrl" | "populate" | "wrap_around",
                "value": bool | int | str,
                "variable": str,  (Variable name)
                "constants_variable": str, (Constants variable name, if different from "variable")
                "description": [str, str, str], (List of strings)
                "warning": str, (Optional) (Warning message to be displayed when checkbox is checked)
                "override_function": function, (Optional) (Function to be executed when checkbox is checked)
            }
        }
        """

        models = [model for model in smbios_data.smbios_dictionary if "_" not in model and " " not in model and smbios_data.smbios_dictionary[model]["Board ID"] is not None]
        socketed_imac_models = ["iMac9,1", "iMac10,1", "iMac11,1", "iMac11,2", "iMac11,3", "iMac12,1", "iMac12,2"]
        socketed_gpu_models = socketed_imac_models + ["MacPro3,1", "MacPro4,1", "MacPro5,1", "Xserve2,1", "Xserve3,1"]

        # Whether the machine actually being *built for* has a T2 chip. This
        # intentionally prefers custom_model (the selected build target) over
        # the host's own real_model, matching the check already used in
        # _populate_sip_settings: a T2 Mac used as a host to flash a build for
        # a different, non-T2 target should see full Security customization,
        # not the host's own T2 restrictions.
        target_is_t2_mac = (self.constants.custom_model or self.constants.computer.real_model) in model_array.T2Macs

        settings = {
            "General": {
                "General": {
                    "type": "title",
                },
                "Allow native models": {
                    "type": "checkbox",
                    "value": self.constants.allow_oc_everywhere,
                    "variable": "allow_oc_everywhere",
                    "description": [
                        "Allow OpenCore to be installed",
                        "on natively supported Macs.",
                        "Note this will not allow unsupported",
                        "macOS versions to be installed on",
                        "your system.",
                    ],
                    "warning": "This option should only be used if your Mac natively supports the OSes you wish to run.\n\nIf you are currently running an unsupported OS, this option will break booting. Only toggle for enabling OS features on a native Mac.\n\nAre you certain you want to continue?",
                },
                "FireWire Booting": {
                    "type": "checkbox",
                    "value": self.constants.firewire_boot,
                    "variable": "firewire_boot",
                    "description": [
                        "Enable booting macOS from",
                        "FireWire drives.",
                    ],
                    "condition": not (generate_smbios.check_firewire(self.constants.custom_model or self.constants.computer.real_model) is False)
                },
                "XHCI Booting": {
                    "type": "checkbox",
                    "value": self.constants.xhci_boot,
                    "variable": "xhci_boot",
                    "description": [
                        "Enable booting macOS from add-in",
                        "USB 3.0 expansion cards on systems",
                        "without native support.",
                    ],
                    "condition": not gui_support.CheckProperties(self.constants).host_has_cpu_gen(cpu_data.CPUGen.ivy_bridge) # Sandy Bridge and older do not natively support XHCI booting
                },
                "NVMe Booting": {
                    "type": "checkbox",
                    "value": self.constants.nvme_boot,
                    "variable": "nvme_boot",
                    "description": [
                        "Enable booting macOS from NVMe",
                        "drives on systems without native",
                        "support.",
                        "Note: Requires Firmware support",
                        "for OpenCore to load from NVMe.",
                    ],
                    "condition": not gui_support.CheckProperties(self.constants).host_has_cpu_gen(cpu_data.CPUGen.ivy_bridge) # Sandy Bridge and older do not natively support NVMe booting
                },
                "wrap_around 2": {
                    "type": "wrap_around",
                },
                "OpenCore Vaulting": {
                    "type": "checkbox",
                    "value": self.constants.vault,
                    "variable": "vault",
                    "description": [
                        "Digitally sign OpenCore to prevent",
                        "tampering or corruption."
                    ],
                },

                "Show OpenCore Boot Picker": {
                    "type": "checkbox",
                    "value": self.constants.showpicker,
                    "variable": "showpicker",
                    "description": [
                        "When disabled, users can hold ESC to",
                        "show picker in the firmware.",
                        "Disable this to not show the",
                        "boot picker every time you",
                        "boot into OpenCore"

                    ],
                },
                "Boot Picker Timeout": {
                    "type": "spinctrl",
                    "value": self.constants.oc_timeout,
                    "variable": "oc_timeout",
                    "description": [
                        "Timeout before boot picker selects default",
                        "entry in seconds.",
                        "Set to 0 for no timeout.",
                    ],

                    "min": 0,
                    "max": 60,
                },
                "MacPro3,1/Xserve2,1 Workaround": {
                    "type": "checkbox",
                    "value": self.constants.force_quad_thread,
                    "variable": "force_quad_thread",
                    "description": [
                        "Limits to 4 threads max on these units.",
                        "Required for macOS Sequoia and later.",
                    ],
                    "condition": (self.constants.custom_model and self.constants.custom_model in ["MacPro3,1", "Xserve2,1"]) or self.constants.computer.real_model in ["MacPro3,1", "Xserve2,1"]
                },
            },
            "Debugging": {
                "Debugging features ": {
                    "type": "title",
                },
                "Verbose": {
                    "type": "checkbox",
                    "value": self.constants.verbose_debug,
                    "variable": "verbose_debug",
                    "description": [
                        "Verbose output during boot.",
                    ],

                },
                "Kext Debugging": {
                    "type": "checkbox",
                    "value": self.constants.kext_debug,
                    "variable": "kext_debug",
                    "description": [
                        "Use DEBUG variants of kexts and",
                        "enables additional kernel logging.",
                    ],
                },
                "wrap_around 1": {
                    "type": "wrap_around",
                },
                "OpenCore Debugging": {
                    "type": "checkbox",
                    "value": self.constants.opencore_debug,
                    "variable": "opencore_debug",
                    "description": [
                        "Use DEBUG variant of OpenCore",
                        "and enables additional logging.",
                    ],
                },
            },

            "Extras": {
                "Extra features - recommended for troubleshooting": {
                    "type": "title",
                },
                "Wake on WLAN": {
                    "type": "checkbox",
                    "value": self.constants.enable_wake_on_wlan,
                    "variable": "enable_wake_on_wlan",
                    "description": [
                        "Disabled by default due to",
                        "performance degradation",
                        "on some systems from wake.",
                        "Only applies to BCM943224, 331,",
                        "360 and 3602 chipsets.",
                    ],
                },
                "Disable Thunderbolt": {
                    "type": "checkbox",
                    "value": self.constants.disable_tb,
                    "variable": "disable_tb",
                    "description": [
                        "For MacBookPro11,x with faulty",
                        "PCHs that may crash sporadically.",
                    ],
                    "condition": (self.constants.custom_model and self.constants.custom_model in ["MacBookPro11,1", "MacBookPro11,2", "MacBookPro11,3"]) or self.constants.computer.real_model in ["MacBookPro11,1", "MacBookPro11,2", "MacBookPro11,3"]
                },
                "Windows GMUX": {
                    "type": "checkbox",
                    "value": self.constants.dGPU_switch,
                    "variable": "dGPU_switch",
                    "description": [
                        "Allow iGPU to be exposed in Windows",
                        "for dGPU-based MacBooks.",
                    ],
                },
                "Disable CPUFriend": {
                    "type": "checkbox",
                    "value": self.constants.disallow_cpufriend,
                    "variable": "disallow_cpufriend",
                    "description": [
                        "Disables power management helper",
                        "for unsupported models.",
                    ],
                },
                "Disable mediaanalysisd service": {
                    "type": "checkbox",
                    "value": self.constants.disable_mediaanalysisd,
                    "variable": "disable_mediaanalysisd",
                    "description": [
                        "For systems that are the primary iCloud",
                        "Photo Library host with a 3802-based GPU,",
                        "this may aid in prolonged idle stability.",
                    ],
                    "condition": gui_support.CheckProperties(self.constants).host_has_3802_gpu()
                },
                "wrap_around 1": {
                    "type": "wrap_around",
                },
                "Allow AppleALC Audio": {
                    "type": "checkbox",
                    "value": self.constants.set_alc_usage,
                    "variable": "set_alc_usage",
                    "description": [
                        "Allow AppleALC to manage audio",
                        "if applicable.",
                        "Only disable if your host lacks",
                        "a GOP ROM.",
                    ],
                },
                "NVRAM WriteFlash": {
                    "type": "checkbox",
                    "value": self.constants.nvram_write,
                    "variable": "nvram_write",
                    "description": [
                        "Allow OpenCore to write to NVRAM.",
                        "Disable on systems with faulty or",
                        "degraded NVRAM.",
                        "Not recommended for T2 Macs",
                    ],
                },

                "3rd Party NVMe PM": {
                    "type": "checkbox",
                    "value": self.constants.allow_nvme_fixing,
                    "variable": "allow_nvme_fixing",
                    "description": [
                        "Enable non-stock NVMe power",
                        "management in macOS.",
                    ],
                },
                "3rd Party SATA PM": {
                    "type": "checkbox",
                    "value": self.constants.allow_3rd_party_drives,
                    "variable": "allow_3rd_party_drives",
                    "description": [
                        "Enable non-stock SATA power",
                        "management in macOS.",
                    ],
                    "condition": not bool(self.constants.computer.third_party_sata_ssd is False and not self.constants.custom_model)
                },
                "APFS Trim": {
                    "type": "checkbox",
                    "value": self.constants.apfs_trim_timeout,
                    "variable": "apfs_trim_timeout",
                    "description": [
                        "Recommended for all users, however faulty",
                        "SSDs may benefit from disabling this.",
                    ],
                },
            },
            "Advanced": {
                "Miscellaneous": {
                    "type": "title",
                },
                "Disable Firmware Throttling": {
                    "type": "checkbox",
                    "value": self.constants.disable_fw_throttle,
                    "variable": "disable_fw_throttle",
                    "description": [
                        "Disables firmware-based throttling",
                        "caused by missing hardware.",
                        "Ex. Missing Display, Battery, etc.",
                    ],
                },
                "Software DeMUX": {
                    "type": "checkbox",
                    "value": self.constants.software_demux,
                    "variable": "software_demux",
                    "description": [
                        "Enable software based DeMUX",
                        "for MacBookPro8,2 and MacBookPro8,3.",
                        "Prevents faulty dGPU from turning on.",
                        "Note: Requires associated NVRAM arg:",
                        "'gpu-power-prefs'.",
                    ],
                    "warning": "This settings requires 'gpu-power-prefs' NVRAM argument to be set to '1'.\n\nIf missing and this option is toggled, the system will not boot\n\nFull command:\nnvram FA4CE28D-B62F-4C99-9CC3-6815686E30F9:gpu-power-prefs=%01%00%00%00",
                    "condition": not bool((not self.constants.custom_model and self.constants.computer.real_model not in ["MacBookPro8,2", "MacBookPro8,3"]) or (self.constants.custom_model and self.constants.custom_model not in ["MacBookPro8,2", "MacBookPro8,3"]))
                },
                "wrap_around 1": {
                    "type": "wrap_around",
                },
                "FeatureUnlock": {
                    "type": "choice",
                    "choices": [
                        "Enabled",
                        "Partial",
                        "Disabled",
                    ],
                    "value": "Enabled",
                    "variable": "",
                    "description": [
                        "Configure FeatureUnlock level.",
                        "Recommend lowering if your system",
                        "experiences memory instability.",
                        "Do not enable this feature on T2",
                        "Macs, it may cause kernel panics.",
                    ],
                },
                "Populate FeatureUnlock Override": {
                    "type": "populate",
                    "function": self._populate_fu_override,
                    "args": wx.Frame,
                },
                "Hibernation Work-around": {
                    "type": "checkbox",
                    "value": self.constants.disable_connectdrivers,
                    "variable": "disable_connectdrivers",
                    "description": [
                        "Only load minimum EFI drivers",
                        "to prevent hibernation issues.",
                        "Note: This may break booting from",
                        "external drives.",
                    ],
                },
                "Graphics": {
                    "type": "title",
                },
                "AMD GOP Injection": {
                    "type": "checkbox",
                    "value": self.constants.amd_gop_injection,
                    "variable": "amd_gop_injection",
                    "description": [
                        "Inject AMD GOP for boot screen",
                        "support on PC GPUs.",
                    ],
                    "condition": not bool((not self.constants.custom_model and self.constants.computer.real_model not in socketed_gpu_models) or (self.constants.custom_model and self.constants.custom_model not in socketed_gpu_models))
                },
                "Nvidia GOP Injection": {
                    "type": "checkbox",
                    "value": self.constants.nvidia_kepler_gop_injection,
                    "variable": "nvidia_kepler_gop_injection",
                    "description": [
                        "Inject Nvidia Kepler GOP for boot",
                        "screen support on PC GPUs.",
                    ],
                    "condition": not bool((not self.constants.custom_model and self.constants.computer.real_model not in socketed_gpu_models) or (self.constants.custom_model and self.constants.custom_model not in socketed_gpu_models))
                },
                "wrap_around 2": {
                    "type": "wrap_around",
                },
                "Graphics Override": {
                    "type": "choice",
                    "choices": [
                        "None",
                        "Nvidia Kepler",
                        "AMD GCN",
                        "AMD Polaris",
                        "AMD Lexa",
                        "AMD Navi",
                    ],
                    "value": "None",
                    "variable": "",
                    "description": [
                        "Override detected/assumed GPU on",
                        "socketed MXM-based iMacs.",
                    ],
                    "condition": bool((not self.constants.custom_model and self.constants.computer.real_model in socketed_imac_models) or (self.constants.custom_model and self.constants.custom_model in socketed_imac_models))
                },
                "Populate Graphics Override": {
                    "type": "populate",
                    "function": self._populate_graphics_override,
                    "args": wx.Frame,
                },

            },
            "Security": {
                "Kernel Security": {
                    "type": "title",
                },
                "Disable Library Validation": {
                    "type": "checkbox",
                    "value": self.constants.disable_cs_lv,
                    "variable": "disable_cs_lv",
                    "description": [
                        "Required for loading modified",
                        "system files from root patching.",
                        "Not used on T2 Macs, which handle",
                        "this differently.",
                    ],
                    "condition": not target_is_t2_mac,
                },
                "Disable AMFI": {
                    "type": "checkbox",
                    "value": self.constants.disable_amfi,
                    "variable": "disable_amfi",
                    "description": [
                        "Extended version of 'Disable",
                        "Library Validation', required",
                        "for systems with deeper",
                        "root patches. Not used on T2 Macs.",
                    ],
                    "condition": not target_is_t2_mac,
                },
                "wrap_around 1": {
                    "type": "wrap_around",
                },
                "Secure Boot Model": {
                    "type": "checkbox",
                    "value": self.constants.secure_status,
                    "variable": "secure_status",
                    "description": [
                        "Set Apple Secure Boot Model Identifier",
                        "to matching T2 model if spoofing.",
                        "Note: Incompatible with Root Patching.",
                        "Always disabled on T2 targets.",
                    ],
                    "condition": not target_is_t2_mac,
                },
                "System Integrity Protection": {
                    "type": "title",
                },
                "Populate SIP": {
                    "type": "populate",
                    "function": self._populate_sip_settings,
                    "args": wx.Frame,
                },
            },
            "SMBIOS": {
                "Model Spoofing": {
                    "type": "title",
                },
                "SMBIOS Spoof Level": {
                    "type": "choice",
                    "choices": [
                        "None",
                        "Minimal",
                        "Moderate",
                        "Advanced",
                    ],
                    "value": self.constants.serial_settings,
                    "variable": "serial_settings",
                    "description": [
                        "Supported Levels:",
                        "   - None: No spoofing.",
                        "   - Minimal: Overrides Board ID.",
                        "   - Moderate: Overrides Model.",
                        "   - Advanced: Overrides Model and serial.",
                    ],
                },

                "SMBIOS Spoof Model": {
                    "type": "choice",
                    "choices": models + ["Default"],
                    "value": self.constants.override_smbios,
                    "variable": "override_smbios",
                    "description": [
                        "Set Mac Model to spoof to.",
                    ],

                },
                "wrap_around 1": {
                    "type": "wrap_around",
                },
                "Allow spoofing native Macs": {
                    "type": "checkbox",
                    "value": self.constants.allow_native_spoofs,
                    "variable": "allow_native_spoofs",
                    "description": [
                        "Allow OpenCore to spoof natively",
                        "supported Macs.",
                        "Primarily used for enabling",
                        "Universal Control on unsupported Macs",
                    ],
                },
                "Serial Spoofing": {
                    "type": "title",
                },
                "Populate Serial Spoofing": {
                    "type": "populate",
                    "function": self._populate_serial_spoofing_settings,
                    "args": wx.Frame,
                },
            },
        }

        return settings


    # MARK: helper functions
    def _populate_graphics_override(self, panel: wx.Panel) -> None:
        gpu_combo_box: wx.Choice = None
        for child in panel.GetChildren():
            if isinstance(child, wx.Choice):
                if "AMD Polaris" in child.GetItems():
                    gpu_combo_box = child
                    break
                continue
        gpu_combo_box.Bind(wx.EVT_CHOICE, self.gpu_selection_click)
        gpu_combo_box.SetStringSelection(f"{self.constants.imac_vendor} {self.constants.imac_model}")
        socketed_gpu_models = ["iMac9,1", "iMac10,1", "iMac11,1", "iMac11,2", "iMac11,3", "iMac12,1", "iMac12,2"]
        if ((not self.constants.custom_model and self.constants.computer.real_model not in socketed_gpu_models) or (self.constants.custom_model and self.constants.custom_model not in socketed_gpu_models)):
            gpu_combo_box.Disable()
            return

    def _populate_fu_override(self, panel: wx.Panel) -> None:
        gpu_combo_box: wx.Choice = None
        for child in panel.GetChildren():
            if isinstance(child, wx.Choice):
                gpu_combo_box = child
                break

        gpu_combo_box.Bind(wx.EVT_CHOICE, self.fu_selection_click)
        if self.constants.fu_status is False:
            gpu_combo_box.SetStringSelection("Disabled")
        elif self.constants.fu_arguments is None or self.constants.fu_arguments == "":
            gpu_combo_box.SetStringSelection("Enabled")
        else:
            gpu_combo_box.SetStringSelection("Partial")


    def fu_selection_click(self, event: wx.Event) -> None:
        value = event.GetEventObject().GetStringSelection()
        if value == "Enabled":
            logging.info("Updating FU Status: Enabled")
            self.constants.fu_status = True
            self.constants.fu_arguments = None
            global_settings.GlobalEnviromentSettings().write_property("GUI:fu_status", True)
            global_settings.GlobalEnviromentSettings().write_property("GUI:fu_arguments", "PYTHON_NONE_VALUE")
            return

        if value == "Partial":
            logging.info("Updating FU Status: Partial")
            self.constants.fu_status = True
            self.constants.fu_arguments = " -disable_sidecar_mac"
            global_settings.GlobalEnviromentSettings().write_property("GUI:fu_status", True)
            global_settings.GlobalEnviromentSettings().write_property("GUI:fu_arguments", " -disable_sidecar_mac")
            return

        logging.info("Updating FU Status: Disabled")
        self.constants.fu_status = False
        self.constants.fu_arguments = None
        global_settings.GlobalEnviromentSettings().write_property("GUI:fu_status", False)
        global_settings.GlobalEnviromentSettings().write_property("GUI:fu_arguments", "PYTHON_NONE_VALUE")


    def gpu_selection_click(self, event: wx.Event) -> None:
        gpu_choice = event.GetEventObject().GetStringSelection()

        logging.info(f"Updating GPU Selection: {gpu_choice}")
        if "AMD" in gpu_choice:
            self.constants.imac_vendor = "AMD"
            self.constants.metal_build = True
            if "Polaris" in gpu_choice:
                self.constants.imac_model = "Polaris"
            elif "GCN" in gpu_choice:
                self.constants.imac_model = "GCN"
            elif "Lexa" in gpu_choice:
                self.constants.imac_model = "Lexa"
            elif "Navi" in gpu_choice:
                self.constants.imac_model = "Navi"
            else:
                raise Exception("Unknown GPU Model")
            global_settings.GlobalEnviromentSettings().write_property("GUI:imac_vendor", "AMD")
            global_settings.GlobalEnviromentSettings().write_property("GUI:metal_build", True)
            global_settings.GlobalEnviromentSettings().write_property("GUI:imac_model", self.constants.imac_model)
        elif "Nvidia" in gpu_choice:
            self.constants.imac_vendor = "Nvidia"
            self.constants.metal_build = True
            if "Kepler" in gpu_choice:
                self.constants.imac_model = "Kepler"
            elif "GT" in gpu_choice:
                self.constants.imac_model = "GT"
            else:
                raise Exception("Unknown GPU Model")
            global_settings.GlobalEnviromentSettings().write_property("GUI:imac_vendor", "Nvidia")
            global_settings.GlobalEnviromentSettings().write_property("GUI:metal_build", True)
            global_settings.GlobalEnviromentSettings().write_property("GUI:imac_model", self.constants.imac_model)
        else:
            self.constants.imac_vendor = "None"
            self.constants.metal_build = False
            global_settings.GlobalEnviromentSettings().write_property("GUI:imac_vendor", "None")
            global_settings.GlobalEnviromentSettings().write_property("GUI:metal_build", False)

    def on_checkbox(self, event: wx.Event, warning_pop: str = "", override_function: bool = False) -> None:
        """
        """
        label = event.GetEventObject().GetLabel()
        value = event.GetEventObject().GetValue()
        if warning_pop != "" and value is True:
            warning = wx.MessageDialog(self.frame_modal, warning_pop, f"Warning: {label}", wx.YES_NO | wx.ICON_WARNING | wx.NO_DEFAULT)
            if warning.ShowModal() == wx.ID_NO:
                event.GetEventObject().SetValue(not event.GetEventObject().GetValue())
                return
            if label == "Allow native models":
                if self.constants.computer.real_model in smbios_data.smbios_dictionary:
                    if self.constants.detected_os > smbios_data.smbios_dictionary[self.constants.computer.real_model]["Max OS Supported"]:
                        chassis_type = "aluminum"
                        if self.constants.computer.real_model in ["MacBook5,2", "MacBook6,1", "MacBook7,1"]:
                            chassis_type = "plastic"
                        dlg = wx.MessageDialog(self.frame_modal, f"This model, {self.constants.computer.real_model}, does not natively support macOS {os_data.os_conversion.kernel_to_os(self.constants.detected_os)}, {os_data.os_conversion.convert_kernel_to_marketing_name(self.constants.detected_os)}. The last native OS was macOS {os_data.os_conversion.kernel_to_os(smbios_data.smbios_dictionary[self.constants.computer.real_model]['Max OS Supported'])}, {os_data.os_conversion.convert_kernel_to_marketing_name(smbios_data.smbios_dictionary[self.constants.computer.real_model]['Max OS Supported'])}\n\nToggling this option will break booting on this OS. Are you absolutely certain this is desired?\n\nYou may end up with a nice {chassis_type} brick \ud83e\uddf1", "Are you certain?", wx.YES_NO | wx.ICON_WARNING | wx.NO_DEFAULT)
                        if dlg.ShowModal() == wx.ID_NO:
                            event.GetEventObject().SetValue(not event.GetEventObject().GetValue())
                            return
        if override_function is True:
            self.settings[self._find_parent_for_key(label)][label]["override_function"](self.settings[self._find_parent_for_key(label)][label]["variable"], value, self.settings[self._find_parent_for_key(label)][label]["constants_variable"] if "constants_variable" in self.settings[self._find_parent_for_key(label)][label] else None)
            return

        self._update_setting(self.settings[self._find_parent_for_key(label)][label]["variable"], value)
        if label == "Allow native models":
            # Re-gate this frame's own Save/Install buttons: the checkbox that was just
            # toggled is exactly what host_can_build() reads, so the answer may have
            # changed under us.
            self._refresh_build_gated_buttons()
            # NOTE: the main menu's "OpenCore" button (self.parent.build_button) is deliberately
            # NOT touched here any more. It only opens this frame, and disabling it on an
            # unsupported host is what made this checkbox unreachable in the first place -
            # unticking it here would have re-locked the door from the inside.

    def on_spinctrl(self, event: wx.Event, label: str) -> None:
        """
        """
        value = event.GetEventObject().GetValue()
        self._update_setting(self.settings[self._find_parent_for_key(label)][label]["variable"], value)

    def on_sip_value(self, event: wx.Event) -> None:
        """
        """
        dict = sip_data.system_integrity_protection.csr_values_extended[f"CSR_{event.GetEventObject().GetLabel()}"]

        if event.GetEventObject().GetValue() is True:
            self.sip_value = self.sip_value + dict["value"]
        else:
            self.sip_value = self.sip_value - dict["value"]

        if hex(self.sip_value) == "0x0":
            self.constants.custom_sip_value = None
            self.constants.sip_status = True
            global_settings.GlobalEnviromentSettings().write_property("GUI:custom_sip_value", "PYTHON_NONE_VALUE")
            global_settings.GlobalEnviromentSettings().write_property("GUI:sip_status", True)
        elif hex(self.sip_value) == "0x803":
            self.constants.custom_sip_value = None
            self.constants.sip_status = False
            global_settings.GlobalEnviromentSettings().write_property("GUI:custom_sip_value", "PYTHON_NONE_VALUE")
            global_settings.GlobalEnviromentSettings().write_property("GUI:sip_status", False)
        else:
            self.constants.custom_sip_value = hex(self.sip_value)
            global_settings.GlobalEnviromentSettings().write_property("GUI:custom_sip_value", hex(self.sip_value))

        self.sip_configured_label.SetLabel(f"Currently configured SIP: {hex(self.sip_value)}")

    def on_choice(self, event: wx.Event, label: str) -> None:
        """
        """
        value = event.GetString()
        self._update_setting(self.settings[self._find_parent_for_key(label)][label]["variable"], value)


    def on_generate_serial_number(self, event: wx.Event) -> None:
        dlg = wx.MessageDialog(self.frame_modal, "Please take caution when using serial spoofing. This should only be used on machines that were legally obtained and require reserialization.\n\nNote: new serials are only overlayed through OpenCore and are not permanently installed into ROM.\n\nMisuse of this setting can break power management and other aspects of the OS if the system does not need spoofing\n\nDortania does not condone the use of our software on stolen devices.\n\nAre you certain you want to continue?", "Warning", wx.YES_NO | wx.ICON_WARNING | wx.NO_DEFAULT)
        if dlg.ShowModal() != wx.ID_YES:
            return

        macserial_output = subprocess.run([self.constants.macserial_path, "--generate", "--model", self.constants.custom_model or self.constants.computer.real_model, "--num", "1"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        macserial_output = macserial_output.stdout.decode().strip().split(" | ")
        if len(macserial_output) == 2:
            self.custom_serial_number_textbox.SetValue(macserial_output[0])
            self.custom_board_serial_number_textbox.SetValue(macserial_output[1])
        else:
            wx.MessageBox(f"Failed to generate serial number:\n\n{macserial_output}", "Error", wx.OK | wx.ICON_ERROR)


    def on_custom_serial_number_textbox(self, event: wx.Event) -> None:
        self.constants.custom_serial_number = event.GetEventObject().GetValue()
        global_settings.GlobalEnviromentSettings().write_property("GUI:custom_serial_number", self.constants.custom_serial_number)


    def on_custom_board_serial_number_textbox(self, event: wx.Event) -> None:
        self.constants.custom_board_serial_number = event.GetEventObject().GetValue()
        global_settings.GlobalEnviromentSettings().write_property("GUI:custom_board_serial_number", self.constants.custom_board_serial_number)

    def on_return(self, event):
        self.frame_modal.Destroy()
        self.parent.Enable()


    def on_save(self, event):
        # Must be initialised before the branch below. If a build profile is already set
        # (e.g. an earlier build in the same session), the prompt is skipped entirely and
        # the reset check at the end of this method would hit an unbound local, crashing
        # with UnboundLocalError right after the build was already written to disk.
        user_had_prompt_set = False
        if self.constants.build_profile is None or self.constants.build_profile == "":
            user_had_prompt_set = True
            choices = [
                "Standard / Safe Build",
                "[LEVEL-B] Experimental GPU",
                "[LEVEL-C] Experimental Tahoe (Native SMBIOS)",
                "[LEVEL-C] Experimental Spoof T2 (MacBookPro16,1)",
                "[LEVEL-D] All-In-One Tahoe (Wi-Fi + Audio + GPU + T1)"
            ]
            dialog = wx.SingleChoiceDialog(
                self,
                "Select the OpenCore build profile you wish to generate:",
                "Build OpenCore",
                choices
            )

            if dialog.ShowModal() == wx.ID_OK:
                selection = dialog.GetSelection()
                if selection == 0:
                    self.constants.build_profile = "standard"
                elif selection == 1:
                    self.constants.build_profile = "test_b"
                elif selection == 2:
                    self.constants.build_profile = "test_c"
                elif selection == 3:
                    self.constants.build_profile = "test_c_spoofed"
                elif selection == 4:
                    self.constants.build_profile = "test_d"
                dialog.Destroy()
            else: #We asume that the user doesn't want to save OpenCore so we stop.
                dialog.Destroy()
                return
        # Throw pop up to get save location
        #
        # wx.FileDialog(wx.FD_SAVE) is unusable here: wxWidgets' macOS backend
        # (src/osx/cocoa/filedlg.mm, as pinned by wxPython 4.2.5) only sets
        # m_useFileTypeFilter when the wildcard holds two or more filter pairs.
        # With a single pair m_firstFileTypeFilter stays -1 and ShowModal() then
        # evaluates m_filterExtensions[-1], which asserts with
        # "wxArrayString: index out of bounds" (arrstr.h:227) before the dialog
        # is ever shown. Fixed upstream, not in the wxPython we ship against.
        #
        # We only ever used the parent directory of the picked path anyway
        # (constants.build_path == oc_build_path.parent), so pick the directory
        # directly - a directory picker carries no wildcard and no filter array.
        with wx.DirDialog(self.parent, "Select where to save the OpenCore build", style=wx.DD_DEFAULT_STYLE | wx.DD_NEW_DIR_BUTTON) as dirDialog:
            if dirDialog.ShowModal() == wx.ID_CANCEL:
                # Profile was only picked for this save, don't leak it into the next build
                if user_had_prompt_set:
                    self.constants.build_profile = ""
                return

            # Must match constants.opencore_release_folder (build_path/oc_build_folder_name),
            # otherwise gui_build writes Build.log into a folder that was never created.
            self.constants.oc_build_path = Path(dirDialog.GetPath()) / self.constants.oc_build_folder_name
            self.frame_modal.Destroy()
            self.parent.Hide()
            logging.info(f"Saving OpenCore-Build to {self.constants.build_path}")
            gui_build.BuildFrame(
                parent=None,
                title=self.title,
                global_constants=self.constants,
                screen_location=self.parent.GetPosition(),
                save=True
            )
            wx.CallAfter(self.parent.Destroy)
            if user_had_prompt_set:
                self.constants.build_profile = ""

    def _update_setting(self, variable, value):
        logging.info(f"Updating Local Setting: {variable} = {value}")
        setattr(self.constants, variable, value)
        tmp_value = value
        if tmp_value is None:
            tmp_value = "PYTHON_NONE_VALUE"
        global_settings.GlobalEnviromentSettings().write_property(f"GUI:{variable}", tmp_value)


    def on_choice(self, event: wx.Event, label: str) -> None:
        """
        """
        value = event.GetString()
        self._update_setting(self.settings[self._find_parent_for_key(label)][label]["variable"], value)

    def on_oc_settings_tab_changed(self, event: wx.Event) -> None:
        """
        wx.Notebook only lays out its currently active page, so a scrolled
        page's virtual size/scrollbars can be stale until it's actually
        shown. Re-adjust the Security tab's scrollbars each time it's
        selected to avoid this.
        """
        page = self._oc_settings_notebook.GetPage(event.GetSelection())
        if isinstance(page, wx.ScrolledWindow):
            page.AdjustScrollbars()
        event.Skip()

    def _populate_sip_settings(self, panel: wx.Frame) -> None:

        horizontal_spacer = 250

        # Look for title on frame
        sip_title: wx.StaticText = None
        for child in panel.GetChildren():
            if child.GetLabel() == "System Integrity Protection":
                sip_title = child
                break

        # These paragraphs used to wrap at a flat 480px, which left them sitting
        # right at the edge of the (then-unscrolled) 600px dialog. Now that this
        # tab is a wx.ScrolledWindow (see _generate_elements), its vertical
        # scrollbar permanently claims some of that width for itself, so the
        # same 480px was clipping the tail end of wrapped lines (eg. part of
        # the SIP warning text) behind/under the scrollbar. Size the wrap width
        # off the actual scrollbar width instead of another guessed constant.
        safe_wrap_width = 480 - wx.SystemSettings.GetMetric(wx.SYS_VSCROLL_X) - 20

        # SIP customization has no effect on T2 Macs: OpenCore hardcodes
        # csr-active-config to 0xFFF there regardless of these settings.
        if (self.constants.custom_model or self.constants.computer.real_model) in model_array.T2Macs:
            sip_unavailable_label = wx.StaticText(panel, label="Customizing SIP is not available for T2 Macs.", pos=(sip_title.GetPosition()[0] - 20, sip_title.GetPosition()[1] + 30))
            sip_unavailable_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
            sip_unavailable_label.Wrap(safe_wrap_width)
            return

        # Label: Flip individual bits corresponding to XNU's csr.h
        # If you're unfamiliar with how SIP works, do not touch this menu
        sip_label = wx.StaticText(panel, label="Flip individual bits corresponding to", pos=(sip_title.GetPosition()[0] - 20, sip_title.GetPosition()[1] + 30))
        sip_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))

        # Hyperlink: csr.h
        spacer = 1 if self.constants.detected_os >= os_data.os_data.big_sur else 3
        sip_csr_h = wx.adv.HyperlinkCtrl(panel, id=wx.ID_ANY, label="XNU's csr.h", url="https://github.com/apple-oss-distributions/xnu/blob/xnu-8020.101.4/bsd/sys/csr.h", pos=(sip_label.GetPosition()[0] + sip_label.GetSize()[0] + 4, sip_label.GetPosition()[1] + spacer))
        sip_csr_h.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        sip_csr_h.SetHoverColour(self.hyperlink_colour)
        sip_csr_h.SetNormalColour(self.hyperlink_colour)
        sip_csr_h.SetVisitedColour(self.hyperlink_colour)

        # Label: SIP Status
        if self.constants.custom_sip_value is not None:
            self.sip_value = int(self.constants.custom_sip_value, 16)
        elif self.constants.sip_status is True:
            self.sip_value = 0x00
        else:
            self.sip_value = 0x803

        # Bug fix: these three lines used to all be assigned to the same
        # 'sip_configured_label' variable and were only offset by 3-20px
        # vertically, so they were drawn on top of each other (and of the
        # 'sip_label'/hyperlink line above). They also had no Wrap(), so the
        # long sentences ran past the dialog edge and got clipped. Each label
        # now gets its own variable and is stacked below the previous one
        # using its actual rendered height, and the long sentences wrap
        # within the panel instead of overflowing it.
        wrap_width = safe_wrap_width

        sip_description_label = wx.StaticText(panel, label="SIP, in short for System Integrity Protection, is a function that prevents attackers from tampering with core system files.", pos=(sip_label.GetPosition()[0], sip_label.GetPosition()[1] + sip_label.GetSize()[1] + 8))
        sip_description_label.SetFont(gui_support.font_factory(12, wx.FONTWEIGHT_NORMAL))
        sip_description_label.Wrap(wrap_width)

        sip_warning_label = wx.StaticText(panel, label="WARNING: If a random person on the internet asks you to set SIP to 0xFFF just to run an app without explaining why, then that app is likely to be malware.", pos=(sip_description_label.GetPosition()[0], sip_description_label.GetPosition()[1] + sip_description_label.GetSize()[1] + 6))
        sip_warning_label.SetFont(gui_support.font_factory(12, wx.FONTWEIGHT_NORMAL))
        sip_warning_label.Wrap(wrap_width)

        sip_configured_label = wx.StaticText(panel, label=f"Currently configured SIP: {hex(self.sip_value)}", pos=(sip_warning_label.GetPosition()[0], sip_warning_label.GetPosition()[1] + sip_warning_label.GetSize()[1] + 10))
        sip_configured_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_BOLD))
        self.sip_configured_label = sip_configured_label

        # Label: SIP Status
        sip_booted_label = wx.StaticText(panel, label=f"Currently booted SIP: {hex(py_sip_xnu.SipXnu().get_sip_status().value)}", pos=(sip_configured_label.GetPosition()[0], sip_configured_label.GetPosition()[1] + sip_configured_label.GetSize()[1] + 4))
        sip_booted_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))


        # SIP toggles
        entries_per_row = len(sip_data.system_integrity_protection.csr_values) // 2
        horizontal_spacer = 15
        vertical_spacer = 25
        index = 1
        for sip_bit in sip_data.system_integrity_protection.csr_values_extended:
            self.sip_checkbox = wx.CheckBox(panel, label=sip_data.system_integrity_protection.csr_values_extended[sip_bit]["name"].split("CSR_")[1], pos = (vertical_spacer, sip_booted_label.GetPosition()[1] + 20 + horizontal_spacer))
            self.sip_checkbox.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
            self.sip_checkbox.SetToolTip(f'Description: {sip_data.system_integrity_protection.csr_values_extended[sip_bit]["description"]}\nValue: {hex(sip_data.system_integrity_protection.csr_values_extended[sip_bit]["value"])}\nIntroduced in: macOS {sip_data.system_integrity_protection.csr_values_extended[sip_bit]["introduced_friendly"]}')

            if self.sip_value & sip_data.system_integrity_protection.csr_values_extended[sip_bit]["value"] == sip_data.system_integrity_protection.csr_values_extended[sip_bit]["value"]:
                self.sip_checkbox.SetValue(True)

            horizontal_spacer += 20
            if index == entries_per_row:
                horizontal_spacer = 15
                vertical_spacer += 250

            index += 1
            self.sip_checkbox.Bind(wx.EVT_CHECKBOX, self.on_sip_value)



    def on_build_and_install_standard(self, event: wx.Event = None):
        self.constants.build_profile = "standard"
        self.on_build_and_install(event)

    def on_build_opencore_menu(self, event: wx.Event = None):
        if self.constants.build_profile is None or self.constants.build_profile == "":
            user_had_prompt_set = True
        else:
            user_had_prompt_set = False
        choices = [
            "Standard / Safe Build",
            "[LEVEL-B] Experimental GPU",
            "[LEVEL-C] Experimental Tahoe (Native SMBIOS)",
            "[LEVEL-C] Experimental Spoof T2 (MacBookPro16,1)",
            "[LEVEL-D] All-In-One Tahoe (Wi-Fi + Audio + GPU + T1)"
        ]
        dialog = wx.SingleChoiceDialog(
            self,
            "Select the OpenCore build profile you wish to generate:",
            "Build OpenCore",
            choices
        )

        if dialog.ShowModal() == wx.ID_OK:
            selection = dialog.GetSelection()
            if selection == 0:
                self.constants.build_profile = "standard"
            elif selection == 1:
                self.constants.build_profile = "test_b"
            elif selection == 2:
                self.constants.build_profile = "test_c"
            elif selection == 3:
                self.constants.build_profile = "test_c_spoofed"
            elif selection == 4:
                self.constants.build_profile = "test_d"
            self.on_build_and_install(event)
            if user_had_prompt_set:
                self.constants.build_profile = ""
        dialog.Destroy()

    def on_build_and_install(self, event: wx.Event = None):
        try:
            parent = self.parent
            self.frame_modal.Destroy()
            parent.Hide()
            gui_build.BuildFrame(parent=None, title=self.title, global_constants=self.constants, screen_location=self.GetPosition(), install=True)
            wx.CallAfter(self.Destroy)
            # The main menu behind this window was only hidden, never destroyed - and a
            # hidden top-level window keeps wx's main loop running, so leaving it behind
            # made a later Cmd+Q close the visible frame without ever quitting the app.
            # on_save() already tears its parent down the same way.
            wx.CallAfter(parent.Destroy)
        except Exception as e:
            logging.error(f"We failed to open up Build and Install OpenCore: {e}")
            logging.exception("Stack Trace:")

    def _populate_serial_spoofing_settings(self, panel: wx.Frame) -> None:
        title: wx.StaticText = None
        for child in panel.GetChildren():
            if child.GetLabel() == "Serial Spoofing":
                title = child
                break

        # Label: Custom Serial Number
        custom_serial_number_label = wx.StaticText(panel, label="Custom Serial Number", pos=(title.GetPosition()[0] - 150, title.GetPosition()[1] + 30))
        custom_serial_number_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_BOLD))

        # Textbox: Custom Serial Number
        custom_serial_number_textbox = wx.TextCtrl(panel, pos=(custom_serial_number_label.GetPosition()[0] - 27, custom_serial_number_label.GetPosition()[1] + 20), size=(200, 25))
        custom_serial_number_textbox.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        custom_serial_number_textbox.SetToolTip("Enter a custom serial number here. This will be used for the SMBIOS and iMessage.\n\nNote: This will not be used if the \"Use Custom Serial Number\" checkbox is not checked.")
        custom_serial_number_textbox.Bind(wx.EVT_TEXT, self.on_custom_serial_number_textbox)
        custom_serial_number_textbox.SetValue(self.constants.custom_serial_number)
        self.custom_serial_number_textbox = custom_serial_number_textbox

        # Label: Custom Board Serial Number
        custom_board_serial_number_label = wx.StaticText(panel, label="Custom Board Serial Number", pos=(title.GetPosition()[0] + 120, custom_serial_number_label.GetPosition()[1]))
        custom_board_serial_number_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_BOLD))

        # Textbox: Custom Board Serial Number
        custom_board_serial_number_textbox = wx.TextCtrl(panel, pos=(custom_board_serial_number_label.GetPosition()[0] - 5, custom_serial_number_textbox.GetPosition()[1]), size=(200, 25))
        custom_board_serial_number_textbox.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        custom_board_serial_number_textbox.SetToolTip("Enter a custom board serial number here. This will be used for the SMBIOS and iMessage.\n\nNote: This will not be used if the \"Use Custom Board Serial Number\" checkbox is not checked.")
        custom_board_serial_number_textbox.Bind(wx.EVT_TEXT, self.on_custom_board_serial_number_textbox)
        custom_board_serial_number_textbox.SetValue(self.constants.custom_board_serial_number)
        self.custom_board_serial_number_textbox = custom_board_serial_number_textbox

        # Button: Generate Serial Number (below)
        generate_serial_number_button = wx.Button(panel, label=f"Generate S/N: {self.constants.custom_model or self.constants.computer.real_model}", pos=(title.GetPosition()[0] - 30, custom_board_serial_number_label.GetPosition()[1] + 60), size=(200, 25))
        generate_serial_number_button.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        generate_serial_number_button.Bind(wx.EVT_BUTTON, self.on_generate_serial_number)

    def _find_parent_for_key(self, key: str) -> str:
        for parent in self.settings:
            if key in self.settings[parent]:
                return parent
