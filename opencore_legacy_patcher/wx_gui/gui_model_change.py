import wx
import logging


from opencore_legacy_patcher.datasets import model_array
from opencore_legacy_patcher.support import defaults
from opencore_legacy_patcher.wx_gui import gui_support

from .. import constants
class ModelPickerFrame(wx.Frame):
    """ 
    shows the Model Change Diolog, which fixes one of the biggest visual gliches in OpenCore
    """

    def __init__(self, parent: wx.Frame, title: str, global_constants: constants.Constants, screen_location: tuple = None):
        logging.info("Initializing Model Picker Frame")
        self.constants: constants.Constants = global_constants
        self.title: str = title
        self.parent: wx.Frame = parent

        self.frame_modal = wx.Dialog(parent, title=title, size=(470, 188))

        self.generate_elements(self.frame_modal)

        self.frame_modal.ShowWindowModal()

    def generate_elements(self, frame: wx.Frame = None):
        model_label = wx.StaticText(frame, label="Target Model", pos=(-1, 5))
        model_label.SetFont(gui_support.font_factory(15, wx.FONTWEIGHT_BOLD))
        model_label.Center(wx.HORIZONTAL)

        model_choice = wx.Choice(frame, choices=model_array.SupportedSMBIOS + ["Host Model"], pos=(-1, model_label.GetPosition()[1] + 32), size=(150, -1))
        model_choice.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        selection = self.constants.custom_model if self.constants.custom_model else "Host Model"
        model_choice.SetSelection(model_choice.FindString(selection))
        model_choice.Center(wx.HORIZONTAL)
        model_description = wx.StaticText(frame, label="Overrides Mac Model the Patcher will build for.", pos=(-1, model_choice.GetPosition()[1] + 25))
        model_description.SetFont(gui_support.font_factory(11, wx.FONTWEIGHT_NORMAL))
        model_description.Center(wx.HORIZONTAL)


        # Cancel Button
        cancel_button = wx.Button(frame, label="Cancel", pos=(270, 130))
        cancel_button.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        cancel_button.Bind(wx.EVT_BUTTON, lambda event, function=self.on_cancel: function(event))

        # Done Button
        done_button = wx.Button(frame, label="Done", pos=(cancel_button.GetPosition()[0] + cancel_button.GetSize()[0] + 20, cancel_button.GetPosition()[1]))
        done_button.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        done_button.SetDefault()
        done_button.Bind(wx.EVT_BUTTON, lambda event, function=self.on_done: function(model_choice, event))


    def on_done(self, model_choice: wx.Choice, event: wx.Event = None) -> None:
        """
        closes the diolog and saves the model
        """
        selection = model_choice.GetStringSelection()
        if selection == "Host Model":
            selection = self.constants.computer.real_model
            self.constants.custom_model = None
            logging.info(f"Using Real Model: {self.constants.computer.real_model}")
            defaults.GenerateDefaults(self.constants.computer.real_model, True, self.constants)
        else:
            logging.info(f"Using Custom Model: {selection}")
            self.constants.custom_model = selection
            defaults.GenerateDefaults(self.constants.custom_model, False, self.constants)
            if hasattr(self.parent, 'build_button') and self.parent.build_button:
                self.parent.build_button.Enable()



        self.parent.model_button.SetLabel(f"Model: {selection}")
        self.parent.model_button.Centre(wx.HORIZONTAL)

        self.frame_modal.Hide()
        self.frame_modal.Destroy()
        self.parent.Enable()

    def on_cancel(self, event: wx.Event = None):
        self.frame_modal.Hide()
        self.parent.Enable()
        self.frame_modal.Destroy()