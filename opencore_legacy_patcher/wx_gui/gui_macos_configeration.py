"""
gui_macos_configeration.py: MacOS Settings and Patch Frame
"""
import wx
import logging
import subprocess
import pprint


from .. import constants

from ..sys_patch import sys_patch

from ..wx_gui import (
    gui_support,
    gui_sys_patch_display,
    gui_update
)

from ..datasets import (
    smbios_data,
    os_data,
)
from ..support import (
    global_settings,
    network_handler,
    subprocess_wrapper,
)


class MacosConfigFrame(wx.Frame):
    """
    Create a modal frame for macOS and root patch related settings and configerations
    """
    def __init__(self, parent: wx.Frame, title: str, global_constants: constants.Constants, screen_location: tuple = None):
        logging.info("Initializing MacOS Configeration Frame")
        self.constants: constants.Constants = global_constants
        self.title: str = title
        self.parent: wx.Frame = parent

        self.hyperlink_colour = (25, 179, 231)

        self.settings = self._settings()

        self.frame_modal = wx.Dialog(parent, title=title, size=(600, 520))
        self._generate_elements(self.frame_modal)

        self.frame_modal.ShowWindowModal()

    def _generate_elements(self, frame: wx.Frame = None) -> None:
        """
        Generates elements for the Settings Frame
        Uses wx.Notebook to implement a tabbed interface
        and relies on 'self._settings()' for populating
        """

        notebook = wx.Notebook(frame)
        notebook.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.AddSpacer(10)

        tabs = list(self.settings.keys())
        if not (self.constants.Experimental_Features or self.constants.True_Developer_Mode):
            tabs.remove("Advanced")
        for tab in tabs:
            panel = wx.ScrolledWindow(notebook)
            panel.SetScrollRate(0, 20)
            notebook.AddPage(panel, tab)

        sizer.Add(notebook, 1, wx.EXPAND | wx.ALL, 10)


        # Add root patch frame button
        root_patch_button = wx.Button(frame, label="Root Patching", pos=(-1, -1), size=(120, 30))
        root_patch_button.Bind(wx.EVT_BUTTON, self.on_root_patch)
        root_patch_button.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        # host_can_root_patch(), not host_can_build(): this button only patches the volume
        # this host already runs, so it may stay available where building an EFI is not
        # (eg. a VMware VM with allow_vmware_root_patching set) - see gui_support.py.
        if (gui_support.CheckProperties(self.constants).host_can_root_patch() is False) or (self.constants.detected_os < os_data.os_data.big_sur):
            root_patch_button.Disable()
        sizer.Add(root_patch_button, 0, wx.ALIGN_CENTER | wx.ALL, 0)

        # Add return button
        return_button = wx.Button(frame, label="Return", pos=(-1, -1), size=(100, 30))
        return_button.Bind(wx.EVT_BUTTON, self.on_return)
        return_button.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        sizer.Add(return_button, 0, wx.ALIGN_CENTER | wx.ALL, 0)

        frame.SetSizer(sizer)

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
                    continue

                if setting_info["type"] == "sub_title":
                    # Add sub-title
                    sub_title = wx.StaticText(panel, label=setting, pos=(-1, -1))
                    sub_title.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))

                    sub_title.SetPosition((int(horizontal_center) - int(sub_title.GetSize()[0] / 2) - 15, height))
                    highest_height_reached = height + sub_title.GetSize()[1] + 10
                    height += sub_title.GetSize()[1] + 10
                    continue

                if setting_info["type"] == "wrap_around":
                    height = highest_height_reached
                    width = 300 if width is stock_width else stock_width
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
                        description.SetForegroundColour((128, 128, 128))

                # Check number of lines in description, and adjust spacer accordingly
                for i, line in enumerate(lines.split('\n')):
                    if line == "":
                        continue
                    if i == 0:
                        height += 11
                    else:
                        height += 13

                if height > lowest_height_reached:
                    lowest_height_reached = height


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
        settings = {
        "Graphics": {
                "TeraScale 2 Acceleration": {
                    "type": "checkbox",
                    "value": global_settings.GlobalEnviromentSettings().read_property("MacBookPro_TeraScale_2_Accel") or self.constants.allow_ts2_accel,
                    "variable": "MacBookPro_TeraScale_2_Accel",
                    "constants_variable": "allow_ts2_accel",
                    "description": [
                        "Enable AMD TeraScale 2 GPU",
                        "Acceleration on MacBookPro8,2 and",
                        "MacBookPro8,3.",
                        "By default this is disabled due to",
                        "common GPU failures on these models.",
                    ],
                    "override_function": self._update_global_settings,
                    "condition": not bool(self.constants.computer.real_model not in ["MacBookPro8,2", "MacBookPro8,3"])
                },
                "wrap_around 1": {
                    "type": "wrap_around",
                },
                "Non-Metal Configuration": {
                    "type": "title",
                },
                "Log out required to apply changes to SkyLight": {
                    "type": "sub_title",
                },
                "Dark Menu Bar": {
                    "type": "checkbox",
                    "value": self._get_system_settings("Moraea_DarkMenuBar"),
                    "variable": "Moraea_DarkMenuBar",
                    "description": [
                        "If Beta Menu Bar is enabled,",
                        "menu bar colour will dynamically",
                        "change as needed.",
                    ],
                    "override_function": self._update_system_defaults,
                    "condition": gui_support.CheckProperties(self.constants).host_is_non_metal(general_check=True)
                },
                "Beta Blur": {
                    "type": "checkbox",
                    "value": self._get_system_settings("Moraea_BlurBeta"),
                    "variable": "Moraea_BlurBeta",
                    "description": [
                        "Control window blur behaviour.",
                    ],
                    "override_function": self._update_system_defaults,
                    "condition": gui_support.CheckProperties(self.constants).host_is_non_metal(general_check=True)

                },
                "Beach Ball Cursor Workaround": {
                    "type": "checkbox",
                    "value": self._get_system_settings("Moraea.EnableSpinHack"),
                    "variable": "Moraea.EnableSpinHack",
                    "description": [
                        "Control beach ball cursor behaviour.",
                    ],
                    "override_function": self._update_system_defaults_root,
                    "condition": gui_support.CheckProperties(self.constants).host_is_non_metal(general_check=True)
                },
                "wrap_around 2": {
                    "type": "wrap_around",
                },
                "Beta Menu Bar": {
                    "type": "checkbox",
                    "value": self._get_system_settings("Amy.MenuBar2Beta"),
                    "variable": "Amy.MenuBar2Beta",
                    "description": [
                        "Supports dynamic colour changes.",
                        "Note: Setting is still experimental.",
                        "If you experience issues, please",
                        "disable this setting.",
                    ],
                    "override_function": self._update_system_defaults,
                    "condition": gui_support.CheckProperties(self.constants).host_is_non_metal(general_check=True)
                },
                "Disable Beta Rim": {
                    "type": "checkbox",
                    "value": self._get_system_settings("Moraea_RimBetaDisabled"),
                    "variable": "Moraea_RimBetaDisabled",
                    "description": [
                        "Control Window Rim rendering.",
                    ],
                    "override_function": self._update_system_defaults,
                    "condition": gui_support.CheckProperties(self.constants).host_is_non_metal(general_check=True)
                },
                "Disable Color Widgets Enforcement": {
                    "type": "checkbox",
                    "value": self._get_system_settings("Moraea_ColorWidgetDisabled"),
                    "variable": "Moraea_ColorWidgetDisabled",
                    "description": [
                        "Control Color Desktop Widgets Enforcement.",
                    ],
                    "override_function": self._update_system_defaults,
                    "condition": gui_support.CheckProperties(self.constants).host_is_non_metal(general_check=True)
                },
            },
      "Advanced": {
                "Tahoe UI Render Optimization": {
                    "type": "checkbox",
                    "value": global_settings.GlobalEnviromentSettings().read_property("Tahoe_UI_Render") or getattr(self.constants, "tahoe_ui_render", False),
                    "variable": "Tahoe_UI_Render",
                    "constants_variable": "tahoe_ui_render",
                    "description": [
                        "Enable experimental graphics",
                        "optimizations for macOS Tahoe",
                        "to improve rendering performance.",
                    ],
                },
                "Developer Root Volume Patching": {
                    "type": "title",
                },
                "Mount Root Volume": {
                    "type": "button",
                    "function": self.on_mount_root_vol,
                    "description": [
                        "Life's too short to type 'sudo mount -o",
                        "nobrowse -t apfs /dev/diskXsY",
                        "/System/Volumes/Update/mnt1' every time.",
                    ],
                    "condition": self.constants.True_Developer_Mode
                },
                "wrap_around 2": {
                    "type": "wrap_around",
                },
                "Save Root Volume": {
                    "type": "button",
                    "function": self.on_bless_root_vol,
                    "description": [
                        "Rebuild kernel cache and bless snapshot 🙏",
                    ],
                    "condition": self.constants.True_Developer_Mode
                },
            },
        }

        return settings

       
    
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
                        dlg = wx.MessageDialog(self.frame_modal, f"This model, {self.constants.computer.real_model}, does not natively support macOS {os_data.os_conversion.kernel_to_os(self.constants.detected_os)}, {os_data.os_conversion.convert_kernel_to_marketing_name(self.constants.detected_os)}. The last native OS was macOS {os_data.os_conversion.kernel_to_os(smbios_data.smbios_dictionary[self.constants.computer.real_model]['Max OS Supported'])}, {os_data.os_conversion.convert_kernel_to_marketing_name(smbios_data.smbios_dictionary[self.constants.computer.real_model]['Max OS Supported'])}\n\nToggling this option will break booting on this OS. Are you absolutely certain this is desired?\n\nYou may end up with a nice {chassis_type} brick 🧱", "Are you certain?", wx.YES_NO | wx.ICON_WARNING | wx.NO_DEFAULT)
                        if dlg.ShowModal() == wx.ID_NO:
                            event.GetEventObject().SetValue(not event.GetEventObject().GetValue())
                            return
        if override_function is True:
            self.settings[self._find_parent_for_key(label)][label]["override_function"](self.settings[self._find_parent_for_key(label)][label]["variable"], value, self.settings[self._find_parent_for_key(label)][label]["constants_variable"] if "constants_variable" in self.settings[self._find_parent_for_key(label)][label] else None)
            return

        self._update_setting(self.settings[self._find_parent_for_key(label)][label]["variable"], value)
        # NOTE: "Allow native models" used to enable/disable the main menu's "OpenCore" button
        # (self.parent.build_button) from here. It no longer does: that button only opens the
        # OpenCore settings frame, which gates its own "Save OpenCore"/"Install OpenCore" buttons
        # on host_can_build() - see gui_oc_settings._refresh_build_gated_buttons(). Disabling the
        # entry point from here would put an unsupported host back in the state this fix removes:
        # unable to reach the very settings ("Allow native models", target model) that unlock
        # building in the first place.


    def on_spinctrl(self, event: wx.Event, label: str) -> None:
        """
        """
        value = event.GetEventObject().GetValue()
        self._update_setting(self.settings[self._find_parent_for_key(label)][label]["variable"], value)


    def _update_setting(self, variable, value):
        logging.info(f"Updating Local Setting: {variable} = {value}")
        setattr(self.constants, variable, value)
        tmp_value = value
        if tmp_value is None:
            tmp_value = "PYTHON_NONE_VALUE"
        global_settings.GlobalEnviromentSettings().write_property(f"GUI:{variable}", tmp_value)


    def _update_global_settings(self, variable, value, global_setting = None) -> None:
        logging.info(f"Updating Global Setting: {variable} = {value}")
        tmp_value = value
        if tmp_value is None:
            tmp_value = "PYTHON_NONE_VALUE"
        global_settings.GlobalEnviromentSettings().write_property(variable, tmp_value)
        if global_setting is not None:
            self._update_setting(global_setting, value)


    def _update_system_defaults(self, variable, value, global_setting = None):
        value_type = type(value)
        if value_type is str:
            value_type = "-string"
        elif value_type is int:
            value_type = "-int"
        elif value_type is bool:
            value_type = "-bool"

        logging.info(f"Updating System Defaults: {variable} = {value} ({value_type})")
        subprocess.run(["/usr/bin/defaults", "write", "-globalDomain", variable, value_type, str(value)])


    def _update_system_defaults_root(self, variable, value, global_setting = None):
        value_type = type(value)
        if value_type is str:
            value_type = "-string"
        elif value_type is int:
            value_type = "-int"
        elif value_type is bool:
            value_type = "-bool"

        logging.info(f"Updating System Defaults (root): {variable} = {value} ({value_type})")
        subprocess_wrapper.run_as_root(["/usr/bin/defaults", "write", "/Library/Preferences/.GlobalPreferences.plist", variable, value_type, str(value)])

    def _get_system_settings(self, variable) -> bool:
        result = subprocess.run(["/usr/bin/defaults", "read", "-globalDomain", variable], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if result.returncode == 0:
            try:
                return bool(int(result.stdout.decode().strip()))
            except:
                return False
        return False


    def on_return(self, event):
        # End the sheet session before destroying, and defer the Destroy - the button
        # handling this event is a child of the dialog (see gui_support.end_window_modal).
        gui_support.end_window_modal(self.frame_modal)
        self.frame_modal.Hide()
        wx.CallAfter(self.frame_modal.Destroy)

    def on_root_patch(self, event):
        # Ends this sheet before the root patch sheet gets attached to the same parent:
        # a leftover modal session here would keep the parent blocked for good.
        gui_support.end_window_modal(self.frame_modal)
        self.frame_modal.Hide()
        wx.CallAfter(self.frame_modal.Destroy)
        gui_sys_patch_display.SysPatchDisplayFrame(
            parent=self.parent,
            title=self.title,
            global_constants=self.constants,
            screen_location=self.parent.GetPosition()
        )

    def on_nightly(self, event: wx.Event) -> None:
        # Ask prompt for which branch
        branches = ["main"]
        if self.constants.commit_info[0] not in ["Running from source", "Built from source"]:
            branches = [self.constants.commit_info[0].split("/")[-1]]
        result = network_handler.NetworkUtilities().get("https://api.github.com/repos/dortania/OpenCore-Legacy-Patcher/branches")
        if result is not None:
            result = result.json()
            for branch in result:
                if branch["name"] == "gh-pages":
                    continue
                if branch["name"] not in branches:
                    branches.append(branch["name"])

            with wx.SingleChoiceDialog(self.parent, "Which branch would you like to download?", "Branch Selection", branches) as dialog:
                if dialog.ShowModal() == wx.ID_CANCEL:
                    return

                branch = dialog.GetStringSelection()
        else:
            branch = "main"

        gui_update.UpdateFrame(
            parent=self.parent,
            title=self.title,
            global_constants=self.constants,
            screen_location=self.parent.GetPosition(),
            url=f"https://nightly.link/dortania/OpenCore-Legacy-Patcher/workflows/build-app-wxpython/{branch}/OpenCore-Patcher.pkg.zip",
            version_label="(Nightly)"
        )


    def on_export_constants(self, event: wx.Event) -> None:
        # Throw pop up to get save location
        with wx.FileDialog(self.parent, "Save Constants File", wildcard="JSON files (*.txt)|*.txt|All files (*.*)|*.*", style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT, defaultFile=f"constants-{self.constants.patcher_version}.txt") as fileDialog:
            if fileDialog.ShowModal() == wx.ID_CANCEL:
                return

            # Save the current contents in the file
            pathname = fileDialog.GetPath()
            logging.info(f"Saving constants to {pathname}")
            with open(pathname, 'w') as file:
                file.write(pprint.pformat(vars(self.constants), indent=4))


    def on_test_exception(self, event: wx.Event) -> None:
        raise Exception("Test Exception")

    def on_mount_root_vol(self, event: wx.Event) -> None:
        #Don't need to pass model as we're bypassing all logic
        if sys_patch.PatchSysVolume("",self.constants)._mount_root_vol() == True:
            wx.MessageDialog(self.parent, "Root Volume Mounted, remember to fix permissions before saving the Root Volume", "Success", wx.OK | wx.ICON_INFORMATION).ShowModal()
        else:
            wx.MessageDialog(self.parent, "Root Volume Mount Failed, check terminal output", "Error", wx.OK | wx.ICON_ERROR).ShowModal()

    def on_bless_root_vol(self, event: wx.Event) -> None:
        #Don't need to pass model as we're bypassing all logic
        if sys_patch.PatchSysVolume("",self.constants)._rebuild_root_volume() == True:
            wx.MessageDialog(self.parent, "Root Volume saved, please reboot to apply changes", "Success", wx.OK | wx.ICON_INFORMATION).ShowModal()
        else:
            wx.MessageDialog(self.parent, "Root Volume update Failed, check terminal output", "Error", wx.OK | wx.ICON_ERROR).ShowModal()
    

    def _find_parent_for_key(self, key: str) -> str:
        for parent in self.settings:
            if key in self.settings[parent]:
                return parent
        return ""